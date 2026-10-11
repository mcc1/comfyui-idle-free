"""ComfyUI idle VRAM release.

ComfyUI keeps its models on the card after a job and only lets go when its own
next job needs the room or someone calls `POST /free`. On a GPU shared with
other services that can mean gigabytes held for days by an idle ComfyUI.
This custom node is the `keep_alive` ComfyUI lacks: once the prompt queue has
been empty for COMFYUI_IDLE_FREE_SECONDS, it raises the same flag as
`POST /free {"unload_models": true}`.

It adds no nodes to the graph; loading the package starts one daemon thread.
The unload itself still runs on ComfyUI's prompt worker (that is what the flag
does), so it never races a job: a prompt queued in the meantime simply runs
first, and resets the idle clock.

Env (read once at startup):
- COMFYUI_IDLE_FREE_SECONDS  idle time before unloading; default 3600, 0 = off
- COMFYUI_IDLE_FREE_POLL     seconds between queue checks; default 30
- COMFYUI_IDLE_FREE_CACHE    1 = also drop the node output cache (the "free_memory"
                             flag, frees RAM too; the next run of an unchanged
                             workflow recomputes everything); default 0
"""
import logging
import os
import threading
import time

NODE_CLASS_MAPPINGS = {}       # required for ComfyUI to load the package at all
NODE_DISPLAY_NAME_MAPPINGS = {}

IDLE_SECONDS = float(os.environ.get("COMFYUI_IDLE_FREE_SECONDS", "3600"))
POLL_SECONDS = float(os.environ.get("COMFYUI_IDLE_FREE_POLL", "30"))
FREE_CACHE = os.environ.get("COMFYUI_IDLE_FREE_CACHE", "0") == "1"

log = logging.getLogger("comfyui-idle-free")


class IdleWatch:
    """Decides when to unload. Pure state, so it can be tested without ComfyUI.

    Fires once per idle stretch: after an unload it stays quiet until the queue
    has been busy again, so an idle box is not told to unload every poll."""

    def __init__(self, idle_seconds: float, now: float):
        self.idle_seconds = idle_seconds
        self.last_busy = now      # startup counts as activity
        self.armed = True

    def tick(self, now: float, tasks_remaining: int, models_loaded: int) -> bool:
        if tasks_remaining > 0:
            self.last_busy = now
            self.armed = True
            return False
        if not self.armed or now - self.last_busy < self.idle_seconds:
            return False
        if models_loaded == 0:
            # Nothing on the card (e.g. someone already called /free); stay
            # armed so a later load in this stretch is still caught. Models only
            # load inside a prompt, so in practice the next busy tick re-arms.
            return False
        self.armed = False
        return True


def _loaded_models() -> int:
    import comfy.model_management as mm
    return len(mm.current_loaded_models)


def _watch() -> None:
    from server import PromptServer
    q = PromptServer.instance.prompt_queue
    w = IdleWatch(IDLE_SECONDS, time.monotonic())
    while True:
        time.sleep(POLL_SECONDS)
        try:
            loaded = _loaded_models()
            if w.tick(time.monotonic(), q.get_tasks_remaining(), loaded):
                log.info("idle for %.0fs with %d model(s) loaded: unloading to free VRAM",
                         IDLE_SECONDS, loaded)
                # Same flags POST /free sets; the prompt worker wakes on them.
                q.set_flag("unload_models", True)
                if FREE_CACHE:
                    q.set_flag("free_memory", True)
        except Exception:
            log.exception("idle check failed; will retry next poll")


def _start() -> None:
    if IDLE_SECONDS <= 0:
        log.info("disabled (COMFYUI_IDLE_FREE_SECONDS=0)")
        return
    threading.Thread(target=_watch, name="comfyui-idle-free", daemon=True).start()
    log.info("unloading models after %.0fs idle (poll %.0fs, node cache %s)",
             IDLE_SECONDS, POLL_SECONDS, "too" if FREE_CACHE else "kept")


# ComfyUI imports custom nodes once, after PromptServer exists. Tests import
# IdleWatch without a server, so they set COMFYUI_IDLE_FREE_NO_AUTOSTART.
if not os.environ.get("COMFYUI_IDLE_FREE_NO_AUTOSTART"):
    _start()
