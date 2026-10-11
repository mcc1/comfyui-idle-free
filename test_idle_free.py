#!/usr/bin/env python3
"""Tests for the idle VRAM release node. No ComfyUI, torch or GPU needed:
`server` and `comfy.model_management` are stubbed in sys.modules, and the stub
queue keeps the two methods the node calls with ComfyUI's own semantics
(execution.PromptQueue.get_tasks_remaining / set_flag).

    python3 test_idle_free.py
"""
import importlib.util
import os
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))


def _import_node(**env):
    for k, v in env.items():
        os.environ[k] = v
    spec = importlib.util.spec_from_file_location("idle_free", os.path.join(HERE, "__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for k in env:
        del os.environ[k]
    return mod


node = _import_node(COMFYUI_IDLE_FREE_NO_AUTOSTART="1")


def test_fires_once_after_idle_and_rearms_on_activity():
    w = node.IdleWatch(3600, now=0)
    assert not w.tick(3599, 0, 3), "fired before the idle time"
    assert w.tick(3600, 0, 3)
    assert not w.tick(3700, 0, 3), "fired twice in one idle stretch"
    assert not w.tick(3800, 1, 3)                  # a job runs: clock resets
    assert not w.tick(3800 + 3599, 0, 3)
    assert w.tick(3800 + 3600, 0, 3)
    print("ok  one unload per idle stretch; a job resets the clock and re-arms")


def test_startup_counts_as_activity_and_nothing_loaded_is_skipped():
    w = node.IdleWatch(60, now=1000)
    assert not w.tick(1059, 0, 2), "startup must count as activity"
    assert not w.tick(1100, 0, 0), "nothing loaded -> nothing to free"
    assert w.armed
    assert w.tick(1200, 0, 1)
    print("ok  startup counts as activity; an empty card is not 'freed'")


class _Queue:
    """The slice of execution.PromptQueue the node touches."""

    def __init__(self):
        self.remaining = 0
        self.flags = {}
        self.mutex = threading.Lock()

    def get_tasks_remaining(self):
        with self.mutex:
            return self.remaining

    def set_flag(self, name, data):
        with self.mutex:
            self.flags[name] = data


def _stub_comfy(queue, loaded):
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(prompt_queue=queue))
    comfy = types.ModuleType("comfy")
    mm = types.ModuleType("comfy.model_management")
    mm.current_loaded_models = loaded
    comfy.model_management = mm
    sys.modules.update({"server": server, "comfy": comfy, "comfy.model_management": mm})


def _wait(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def test_watch_thread_sets_the_free_flags():
    q = _Queue()
    _stub_comfy(q, loaded=["model"])
    n = _import_node(COMFYUI_IDLE_FREE_SECONDS="0.3", COMFYUI_IDLE_FREE_POLL="0.05")
    assert _wait(lambda: q.flags), "watch thread never unloaded"
    assert q.flags == {"unload_models": True}, q.flags   # node cache kept by default
    assert any(t.name == "comfyui-idle-free" and t.daemon for t in threading.enumerate())

    q2 = _Queue()
    _stub_comfy(q2, loaded=["model"])
    q2.remaining = 1                                     # a long job in progress
    _import_node(COMFYUI_IDLE_FREE_SECONDS="0.2", COMFYUI_IDLE_FREE_POLL="0.05",
                 COMFYUI_IDLE_FREE_CACHE="1")
    time.sleep(0.6)
    assert q2.flags == {}, "unloaded while a job was running"
    q2.remaining = 0
    assert _wait(lambda: q2.flags)
    assert q2.flags == {"unload_models": True, "free_memory": True}, q2.flags
    assert n.IDLE_SECONDS == 0.3
    print("ok  the thread raises POST /free's flags after idle, never during a job")


def test_zero_disables():
    before = sum(t.name == "comfyui-idle-free" for t in threading.enumerate())
    _import_node(COMFYUI_IDLE_FREE_SECONDS="0")
    after = sum(t.name == "comfyui-idle-free" for t in threading.enumerate())
    assert after == before
    print("ok  COMFYUI_IDLE_FREE_SECONDS=0 starts no thread")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\nPASS {len(tests)} tests (comfyui-idle-free)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
