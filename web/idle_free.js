// Settings > Idle Free. The frontend stores these in the user's
// comfy.settings.json; the Python side re-reads that file every poll, so a
// change applies within one poll without restarting ComfyUI.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Defaults come from the server so they match the COMFYUI_IDLE_FREE_* env
// vars until someone changes a value here.
let defaults = { enabled: true, idle_minutes: 60, clear_cache: false, poll_seconds: 30 };
try {
    const res = await api.fetchApi("/idle-free/defaults");
    if (res.ok) defaults = { ...defaults, ...(await res.json()) };
} catch (e) {
    console.warn("[comfyui-idle-free] could not load defaults:", e);
}

const CATEGORY = "Idle Free";
const SECTION = "Unload models when idle";

app.registerExtension({
    name: "comfyui-idle-free",
    settings: [
        {
            id: "IdleFree.Enabled",
            category: [CATEGORY, SECTION, "Enabled"],
            name: "Unload models from VRAM when the queue is idle",
            type: "boolean",
            defaultValue: defaults.enabled,
            tooltip: "Frees VRAM for other programs after ComfyUI has been idle. The next run reloads its models, which takes a few seconds.",
        },
        {
            id: "IdleFree.IdleMinutes",
            category: [CATEGORY, SECTION, "Idle minutes"],
            name: "Idle time before unloading (minutes)",
            type: "number",
            defaultValue: defaults.idle_minutes,
            attrs: { min: 1, max: 1440, step: 1, showButtons: true },
            tooltip: "Counted from the end of the last job. The queue is checked every poll interval, so the actual unload can come up to one interval later.",
        },
        {
            id: "IdleFree.PollSeconds",
            category: [CATEGORY, SECTION, "Poll seconds"],
            name: "Check the queue every (seconds)",
            type: "number",
            defaultValue: defaults.poll_seconds,
            attrs: { min: 1, max: 3600, step: 1, showButtons: true },
            tooltip: "How often the idle check runs. Shorter reacts sooner; the check itself is cheap. Setting changes are also picked up at this interval.",
        },
        {
            id: "IdleFree.ClearCache",
            category: [CATEGORY, SECTION, "Clear cache"],
            name: "Also clear the node output cache",
            type: "boolean",
            defaultValue: defaults.clear_cache,
            tooltip: "Frees a few hundred MB more VRAM and some RAM, but the next run of an unchanged workflow recomputes every node.",
        },
    ],
});
