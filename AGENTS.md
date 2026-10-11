# AGENTS.md

給在這個 repo 工作的 coding agent 看的說明。

## 這是什麼

一個 ComfyUI custom node 套件。ComfyUI 的 prompt queue 閒置超過設定時間後，它會自動卸載模型、釋放 VRAM。使用方式見 [README.md](README.md)。

## 檔案

- `__init__.py`：全部的程式碼。
  - `IdleWatch`：判斷什麼時候該卸載的純邏輯，不依賴 ComfyUI，可以直接測試。
  - `_watch()`：背景 thread 的主迴圈，讀 ComfyUI 的佇列和已載入模型數，該卸載時呼叫 `set_flag`。
  - `_start()`：讀環境變數、啟動 thread。模組被 import 時自動執行。
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

## 設計原則

修改時請維持以下行為：

- **不直接卸載模型**。只設 flag，讓 ComfyUI 自己的 prompt worker 在兩個工作之間執行卸載，這樣就不會和正在跑的工作搶。不要在背景 thread 裡直接呼叫 `unload_all_models()`。
- **每段閒置只觸發一次**。卸載後要等佇列再度忙碌過，才會重新計時。
- **啟動時視為有活動**。ComfyUI 剛啟動時不會立刻卸載。
- **卡上沒有模型就不觸發**，但要保持 armed，讓同一段閒置期間後來載入的模型還是會被處理。
- **不在節點清單新增節點**。`NODE_CLASS_MAPPINGS` 必須存在（ComfyUI 需要它才會載入套件），但保持空的。
- **只用標準函式庫**。不要加入 `requirements.txt` 的相依套件。
- 設定只透過環境變數，在啟動時讀一次。
- 背景 thread 裡的例外要記 log 後繼續下一輪，不能讓 thread 死掉。

## 測試的寫法

- 測試透過 `importlib` 重新載入 `__init__.py`，用環境變數控制行為。
- 只測 `IdleWatch` 時，設定 `COMFYUI_IDLE_FREE_NO_AUTOSTART=1`，避免 import 時啟動 thread。
- 要測 thread 時，先用 `_stub_comfy()` 把假的 `server` 和 `comfy.model_management` 塞進 `sys.modules`，再用很短的秒數（例如 0.3 秒）載入。
- 新增行為時，先在 `IdleWatch` 層加單元測試，需要時再加 thread 層的測試。
