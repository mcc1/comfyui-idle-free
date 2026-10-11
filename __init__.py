"""ComfyUI idle VRAM release.

ComfyUI keeps its models on the card after a job and only lets go when its own
next job needs the room or someone calls `POST /free`. On a GPU shared with
other services that can mean gigabytes held for days by an idle ComfyUI.
This custom node is the `keep_alive` ComfyUI lacks: once the prompt queue has
been empty for the configured idle time, it raises the same flag as
`POST /free {"unload_models": true}`.

It adds no nodes to the graph; loading the package starts one daemon thread
and registers four entries in ComfyUI's Settings dialog (web/idle_free.js).
The unload itself still runs on ComfyUI's prompt worker (that is what the flag
does), so it never races a job: a prompt queued in the meantime simply runs
first, and resets the idle clock.

Settings (Settings > Idle Free), stored by the frontend in the default user's
comfy.settings.json and re-read on every poll, so a change applies without a
restart:
- IdleFree.Enabled       on/off
- IdleFree.IdleMinutes   idle time before unloading
- IdleFree.ClearCache    also drop the node output cache (the "free_memory"
                         flag, frees RAM too; the next run of an unchanged
                         workflow recomputes everything)
- IdleFree.PollSeconds   seconds between queue checks

Env, read once at startup, gives the defaults for a setting nobody has changed
in the UI (handy for headless installs):
- COMFYUI_IDLE_FREE_SECONDS  idle time; default 3600, 0 = disabled
- COMFYUI_IDLE_FREE_POLL     seconds between queue checks; default 30
- COMFYUI_IDLE_FREE_CACHE    1 = clear the node cache too; default 0
"""
import json
import logging
import os
import threading
import time

NODE_CLASS_MAPPINGS = {}       # required for ComfyUI to load the package at all
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web"

ENV_IDLE_SECONDS = float(os.environ.get("COMFYUI_IDLE_FREE_SECONDS", "3600"))
ENV_POLL_SECONDS = float(os.environ.get("COMFYUI_IDLE_FREE_POLL", "30"))
ENV_FREE_CACHE = os.environ.get("COMFYUI_IDLE_FREE_CACHE", "0") == "1"

# Setting ids; must match web/idle_free.js.
S_ENABLED = "IdleFree.Enabled"
S_MINUTES = "IdleFree.IdleMinutes"
S_CACHE = "IdleFree.ClearCache"
S_POLL = "IdleFree.PollSeconds"

POLL_MIN, POLL_MAX = 1.0, 3600.0   # UI bounds; 0 would spin the thread

log = logging.getLogger("comfyui-idle-free")


class Config:
    def __init__(self, enabled: bool, idle_seconds: float, free_cache: bool,
                 poll_seconds: float = 30.0):
        self.enabled = enabled
        self.idle_seconds = idle_seconds
        self.free_cache = free_cache
        self.poll_seconds = poll_seconds

    def __eq__(self, other):
        return vars(self) == vars(other)

    def describe(self) -> str:
        if not self.enabled:
            return "disabled"
        return "unloading models after %.0fs idle (poll %.0fs, node cache %s)" % (
            self.idle_seconds, self.poll_seconds, "too" if self.free_cache else "kept")


def env_config() -> Config:
    return Config(ENV_IDLE_SECONDS > 0, ENV_IDLE_SECONDS if ENV_IDLE_SECONDS > 0 else 3600,
                  ENV_FREE_CACHE, ENV_POLL_SECONDS if ENV_POLL_SECONDS > 0 else 30.0)


def merge_settings(settings: dict, base: Config) -> Config:
    """UI settings win; a key the user never touched (absent) keeps the env
    default. Bad values are ignored rather than trusted."""
    enabled, idle, cache = base.enabled, base.idle_seconds, base.free_cache
    if isinstance(settings.get(S_ENABLED), bool):
        enabled = settings[S_ENABLED]
    minutes = settings.get(S_MINUTES)
    if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and minutes > 0:
        idle = float(minutes) * 60
    if isinstance(settings.get(S_CACHE), bool):
        cache = settings[S_CACHE]
    poll = base.poll_seconds
    seconds = settings.get(S_POLL)
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0:
        poll = min(max(float(seconds), POLL_MIN), POLL_MAX)
    return Config(enabled, idle, cache, poll)


class SettingsFile:
    """Reads the frontend's comfy.settings.json, re-parsing only when it changes."""

    def __init__(self, path):
        self.path = path
        self._stamp = None
        self._data = {}

    def read(self) -> dict:
        try:
            st = os.stat(self.path)
        except OSError:
            self._stamp, self._data = None, {}
            return self._data
        stamp = (st.st_mtime_ns, st.st_size)
        if stamp != self._stamp:
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                self._data = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                # Mid-write or corrupt: keep the last good copy, retry next poll.
                return self._data
            self._stamp = stamp
        return self._data


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


def _settings_path() -> str:
    import folder_paths
    return os.path.join(folder_paths.get_user_directory(), "default", "comfy.settings.json")


def _watch() -> None:
    from server import PromptServer
    q = PromptServer.instance.prompt_queue
    base = env_config()
    settings = SettingsFile(_settings_path())
    cfg = merge_settings(settings.read(), base)
    log.info(cfg.describe())
    w = IdleWatch(cfg.idle_seconds, time.monotonic())
    while True:
        time.sleep(cfg.poll_seconds)
        try:
            new = merge_settings(settings.read(), base)
            if new != cfg:
                cfg = new
                log.info("settings changed: %s", cfg.describe())
            w.idle_seconds = cfg.idle_seconds
            loaded = _loaded_models() if cfg.enabled else 0  # 0 = never fire
            if w.tick(time.monotonic(), q.get_tasks_remaining(), loaded):
                log.info("idle for %.0fs with %d model(s) loaded: unloading to free VRAM",
                         cfg.idle_seconds, loaded)
                # Same flags POST /free sets; the prompt worker wakes on them.
                q.set_flag("unload_models", True)
                if cfg.free_cache:
                    q.set_flag("free_memory", True)
        except Exception:
            log.exception("idle check failed; will retry next poll")


def defaults_json() -> dict:
    base = env_config()
    return {"enabled": base.enabled, "idle_minutes": round(base.idle_seconds / 60, 2),
            "clear_cache": base.free_cache, "poll_seconds": base.poll_seconds}


def _add_routes() -> None:
    from aiohttp import web
    from server import PromptServer

    @PromptServer.instance.routes.get("/idle-free/defaults")
    async def _defaults(request):
        return web.json_response(defaults_json())


def _start() -> None:
    try:
        _add_routes()
    except Exception:
        log.exception("could not register /idle-free/defaults; the UI will use built-in defaults")
    # Always start, even when disabled: the UI can turn it on at any time.
    threading.Thread(target=_watch, name="comfyui-idle-free", daemon=True).start()


# ComfyUI imports custom nodes once, after PromptServer exists. Tests import
# the module without a server, so they set COMFYUI_IDLE_FREE_NO_AUTOSTART.
if not os.environ.get("COMFYUI_IDLE_FREE_NO_AUTOSTART"):
    _start()
