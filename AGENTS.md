# AGENTS.md

給在這個 repo 工作的 coding agent 看的說明。

## 這是什麼

一個 ComfyUI custom node 套件。ComfyUI 的 prompt queue 閒置超過設定時間後，它會自動卸載模型、釋放 VRAM。使用方式見 [README.md](README.md)。

## 檔案

- `__init__.py`：全部的 Python 程式碼。
  - `IdleWatch`：判斷什麼時候該卸載的純邏輯，不依賴 ComfyUI，可以直接測試。
  - `Config`、`env_config()`、`merge_settings()`：決定目前生效的設定。設定畫面的值優先，沒改過的項目用環境變數的預設值。
  - `SettingsFile`：讀前端存的 `comfy.settings.json`，檔案沒變就不重新解析，壞掉的 JSON 會沿用上一份。
  - `_watch()`：背景 thread 的主迴圈。每次輪詢都重讀設定，再讀 ComfyUI 的佇列和已載入模型數，該卸載時呼叫 `set_flag`。
  - `_add_routes()`：註冊 `GET /idle-free/defaults`，讓前端拿到環境變數算出來的預設值。
  - `_start()`：註冊 route、啟動 thread。模組被 import 時自動執行。
- `web/idle_free.js`：前端擴充，在 Settings 裡註冊三個設定（`IdleFree.Enabled`、`IdleFree.IdleMinutes`、`IdleFree.ClearCache`）。這些 id 必須和 `__init__.py` 裡的 `S_*` 常數一致。
- `test_idle_free.py`：測試，用 stub 取代 ComfyUI，不需要 torch 或 GPU。

## 執行測試

```sh
python3 test_idle_free.py
```

沒有測試框架、沒有額外的相依套件，只用 Python 標準函式庫。修改任何程式碼後都要跑一次，必須全部 PASS。

## 依賴的 ComfyUI 內部介面

這個 node 只碰到下面幾個 ComfyUI 內部介面。ComfyUI 改版時，先檢查這些是否還在、行為是否一樣：

- `server.PromptServer.instance.prompt_queue`：在載入 custom node 之前就已經存在。
- `PromptQueue.get_tasks_remaining()`：正在跑加上排隊中的工作數量。
- `PromptQueue.set_flag(name, value)`：`main.py` 的 prompt worker 看到 `unload_models` 會呼叫 `comfy.model_management.unload_all_models()`，看到 `free_memory` 會清空節點輸出快取。這和 `POST /free` 走的是同一條路。
- `comfy.model_management.current_loaded_models`：目前載入的模型清單。
- `folder_paths.get_user_directory()`：前端設定存在 `<user dir>/default/comfy.settings.json`（見 ComfyUI 的 `app/app_settings.py`）。
- `PromptServer.instance.routes`：custom node 載入時可以在這裡加 aiohttp route。
- 模組層級的 `WEB_DIRECTORY`：ComfyUI 會把這個資料夾的 `.js` 當前端擴充載入。
- 前端：`app.registerExtension({ settings: [...] })` 和 `api.fetchApi()`。

## 設計原則

修改時請維持以下行為：

- **不直接卸載模型**。只設 flag，讓 ComfyUI 自己的 prompt worker 在兩個工作之間執行卸載，這樣就不會和正在跑的工作搶。不要在背景 thread 裡直接呼叫 `unload_all_models()`。
- **每段閒置只觸發一次**。卸載後要等佇列再度忙碌過，才會重新計時。
- **啟動時視為有活動**。ComfyUI 剛啟動時不會立刻卸載。
- **卡上沒有模型就不觸發**，但要保持 armed，讓同一段閒置期間後來載入的模型還是會被處理。
- **不在節點清單新增節點**。`NODE_CLASS_MAPPINGS` 必須存在（ComfyUI 需要它才會載入套件），但保持空的。
- **只用標準函式庫**。不要加入 `requirements.txt` 的相依套件。
- **設定以設定畫面為主，改了不用重啟**。每次輪詢重讀設定檔；環境變數只提供預設值。關閉時 thread 仍在跑，這樣在設定畫面打開就會立刻生效。
- 設定檔讀取失敗（不存在、寫到一半、格式錯）時不能讓 thread 出錯，也不能把設定重設成預設值以外的東西。
- 背景 thread 裡的例外要記 log 後繼續下一輪，不能讓 thread 死掉。

## 測試的寫法

- 測試透過 `importlib` 重新載入 `__init__.py`，用環境變數控制行為。
- `_stub_comfy()` 會建一個暫存的 user 目錄，可以傳 `settings` 寫入 `comfy.settings.json`，回傳該檔路徑；測試中途改寫它，就能模擬使用者在設定畫面改值。
- 前端 JS 沒有自動化測試。改了 `web/idle_free.js` 或設定相關的 Python 之後，要在真的 ComfyUI 上手動驗證，見下一節。
- 只測 `IdleWatch` 時，設定 `COMFYUI_IDLE_FREE_NO_AUTOSTART=1`，避免 import 時啟動 thread。
- 要測 thread 時，先用 `_stub_comfy()` 把假的 `server` 和 `comfy.model_management` 塞進 `sys.modules`，再用很短的秒數（例如 0.3 秒）載入。
- 新增行為時，先在 `IdleWatch` 層加單元測試，需要時再加 thread 層的測試。

## 在真的 ComfyUI 上驗證

改了設定相關的程式碼後，在一台跑著的 ComfyUI 上依序確認（`$C` 代表 `http://127.0.0.1:8188`）：

1. 重啟 ComfyUI 後，log 有 `comfyui-idle-free` 的啟動訊息（例如 `unloading models after 3600s idle ...`），而且沒有 `IMPORT FAILED`。
2. `curl $C/idle-free/defaults` 回傳的預設值和環境變數一致。
3. `curl $C/extensions` 列出 `/extensions/comfyui-idle-free/idle_free.js`。
4. 先備份 `ComfyUI/user/default/comfy.settings.json`，再用 `curl -X POST $C/settings/IdleFree.IdleMinutes -d 2` 模擬在設定畫面改值。30 秒內 log 應出現 `settings changed: ...`。
5. 跑一個跑完會把模型留在卡上的 workflow（例如 SDXL），閒置時間過後 log 應出現 `idle for ... unloading`，`nvidia-smi` 看得到 VRAM 降下來。
6. 用瀏覽器（或 headless 瀏覽器）打開 Settings，確認 Idle Free 的三個設定都有顯示，值和 `comfy.settings.json` 一致。
7. 還原 `comfy.settings.json`，確認 log 出現回到預設值的 `settings changed`。

`POST /settings/<id>` 是 ComfyUI 前端自己存設定用的 API（`app/app_settings.py`），用它改值和在畫面上改的效果一樣。
