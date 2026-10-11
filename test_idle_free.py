#!/usr/bin/env python3
"""Tests for the idle VRAM release node. No ComfyUI, torch or GPU needed:
`server`, `folder_paths` and `comfy.model_management` are stubbed in sys.modules, and the stub
queue keeps the two methods the node calls with ComfyUI's own semantics
(execution.PromptQueue.get_tasks_remaining / set_flag).

    python3 test_idle_free.py
"""
import importlib.util
import json
import os
import sys
import tempfile
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


def _stub_comfy(queue, loaded, settings=None):
    """Stubs ComfyUI; `settings` (a dict) becomes the default user's
    comfy.settings.json. Returns that file's path."""
    user_dir = tempfile.mkdtemp()
    os.makedirs(os.path.join(user_dir, "default"))
    path = os.path.join(user_dir, "default", "comfy.settings.json")
    if settings is not None:
        _write(path, settings)
    fp = types.ModuleType("folder_paths")
    fp.get_user_directory = lambda: user_dir
    routes = _Routes()
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(
        instance=types.SimpleNamespace(prompt_queue=queue, routes=routes))
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.web = types.SimpleNamespace(json_response=lambda data: data)
    comfy = types.ModuleType("comfy")
    mm = types.ModuleType("comfy.model_management")
    mm.current_loaded_models = loaded
    comfy.model_management = mm
    sys.modules.update({"server": server, "folder_paths": fp, "comfy": comfy,
                        "comfy.model_management": mm, "aiohttp": aiohttp})
    _stub_comfy.routes = routes
    return path


class _Routes:
    """aiohttp RouteTableDef's .get decorator, recording handlers by path."""

    def __init__(self):
        self.handlers = {}

    def get(self, path):
        def deco(fn):
            self.handlers[path] = fn
            return fn
        return deco


def _write(path, settings):
    with open(path, "w") as f:
        json.dump(settings, f)


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
    assert n.ENV_IDLE_SECONDS == 0.3
    print("ok  the thread raises POST /free's flags after idle, never during a job")


def test_settings_override_env_defaults():
    base = node.Config(True, 3600, False)
    assert node.merge_settings({}, base) == base, "untouched settings keep env defaults"
    got = node.merge_settings({"IdleFree.Enabled": False, "IdleFree.IdleMinutes": 15,
                               "IdleFree.ClearCache": True, "Comfy.Other": 1}, base)
    assert got == node.Config(False, 900, True), vars(got)
    for bad in ({"IdleFree.IdleMinutes": 0}, {"IdleFree.IdleMinutes": -5},
                {"IdleFree.IdleMinutes": "10"}, {"IdleFree.IdleMinutes": True},
                {"IdleFree.Enabled": "no"}):
        assert node.merge_settings(bad, base) == base, bad
    print("ok  UI settings win over env; untouched or bad values keep the default")


def test_env_zero_means_disabled_by_default():
    n = _import_node(COMFYUI_IDLE_FREE_NO_AUTOSTART="1", COMFYUI_IDLE_FREE_SECONDS="0")
    assert n.env_config().enabled is False
    assert n.defaults_json() == {"enabled": False, "idle_minutes": 60, "clear_cache": False}
    n = _import_node(COMFYUI_IDLE_FREE_NO_AUTOSTART="1", COMFYUI_IDLE_FREE_SECONDS="1800",
                     COMFYUI_IDLE_FREE_CACHE="1")
    assert n.defaults_json() == {"enabled": True, "idle_minutes": 30, "clear_cache": True}
    print("ok  env gives the UI its defaults; SECONDS=0 means off until enabled in the UI")


def test_settings_file_rereads_only_on_change_and_survives_bad_json():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "comfy.settings.json")
    f = node.SettingsFile(path)
    assert f.read() == {}, "missing file = no settings"
    _write(path, {"IdleFree.IdleMinutes": 5})
    assert f.read() == {"IdleFree.IdleMinutes": 5}
    with open(path, "w") as fh:
        fh.write("{ half written")
    assert f.read() == {"IdleFree.IdleMinutes": 5}, "bad JSON keeps the last good copy"
    _write(path, {"IdleFree.IdleMinutes": 7, "pad": "x"})
    assert f.read() == {"IdleFree.IdleMinutes": 7, "pad": "x"}
    os.remove(path)
    assert f.read() == {}
    print("ok  settings file: re-read on change, bad JSON ignored, removal clears")


def test_defaults_route_feeds_the_ui():
    import asyncio
    _stub_comfy(_Queue(), loaded=[])
    _import_node(COMFYUI_IDLE_FREE_SECONDS="600", COMFYUI_IDLE_FREE_POLL="0.05")
    handler = _stub_comfy.routes.handlers["/idle-free/defaults"]
    assert asyncio.run(handler(None)) == {"enabled": True, "idle_minutes": 10,
                                          "clear_cache": False}
    print("ok  GET /idle-free/defaults returns the env defaults for the settings UI")


def test_ui_change_applies_without_restart():
    q = _Queue()
    path = _stub_comfy(q, loaded=["model"], settings={"IdleFree.Enabled": False})
    _import_node(COMFYUI_IDLE_FREE_SECONDS="0.2", COMFYUI_IDLE_FREE_POLL="0.05")
    time.sleep(0.6)
    assert q.flags == {}, "unloaded while disabled in the UI"
    _write(path, {"IdleFree.Enabled": True, "IdleFree.IdleMinutes": 0.005,  # 0.3 s
                  "IdleFree.ClearCache": True})
    assert _wait(lambda: q.flags), "enabling in the UI never took effect"
    assert q.flags == {"unload_models": True, "free_memory": True}, q.flags
    print("ok  turning it on in the UI takes effect on the next poll, no restart")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\nPASS {len(tests)} tests (comfyui-idle-free)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
