# comfyui-idle-free

ComfyUI 閒置一段時間後，自動把模型從 VRAM 卸載。

ComfyUI 跑完一個 workflow 之後，模型會一直留在顯示卡上，直到下一個 workflow 需要空間，或有人手動呼叫 `POST /free` 才會放掉。如果同一張卡上還有其他服務（embedding、LLM、另一個推論服務），閒置的 ComfyUI 可能會佔著好幾 GB 的 VRAM，一放就是好幾天。

這個 custom node 補上 ComfyUI 缺少的閒置逾時機制。只要佇列空了一段時間（預設 1 小時），它就做和 `POST /free {"unload_models": true}` 一樣的事。

> English: a ComfyUI custom node that unloads models from VRAM after the prompt queue has been idle for a while (default 1 hour), the same thing `POST /free {"unload_models": true}` does. Configure it with the environment variables below.

## 運作方式

- 不會在節點清單裡新增任何節點。載入套件時只會啟動一個背景 daemon thread。
- 每 `COMFYUI_IDLE_FREE_POLL` 秒檢查一次 prompt queue。如果超過 `COMFYUI_IDLE_FREE_SECONDS` 秒沒有任何工作在跑或在排隊，而且卡上確實載入了模型，它就會設定 `unload_models` flag。
- 實際的卸載由 ComfyUI 自己的 prompt worker 執行，所以不會打斷正在跑的工作。閒置期間如果有新的 prompt 進來，會先跑完那個 prompt，閒置計時也會重新開始。
- 每段閒置期間只觸發一次，不會每次輪詢都重複卸載。
- 下一次跑 workflow 時，模型會從硬碟（或 page cache）重新載入，多花幾秒鐘。

## 安裝

```sh
cd ComfyUI/custom_nodes
git clone https://github.com/mcc1/comfyui-idle-free.git
```

接著重啟 ComfyUI。啟動 log 應該會出現：

```
unloading models after 3600s idle (poll 30s, node cache kept)
```

不需要安裝任何額外的 Python 套件。

## 設定

設定都透過環境變數，在 ComfyUI 啟動時讀取一次。

| 環境變數 | 預設值 | 說明 |
|---|---|---|
| `COMFYUI_IDLE_FREE_SECONDS` | `3600` | 閒置多少秒後卸載。設為 `0` 會停用這個 node。 |
| `COMFYUI_IDLE_FREE_POLL` | `30` | 每隔幾秒檢查一次佇列。 |
| `COMFYUI_IDLE_FREE_CACHE` | `0` | 設為 `1` 時連節點輸出快取一起清掉（等同 `free_memory` flag），會多釋放一些 VRAM 和 RAM，但下次跑同一個 workflow 時每個節點都要重算。 |

用 systemd 跑 ComfyUI 的話，可以加一個 drop-in：

```ini
# ~/.config/systemd/user/comfyui.service.d/idle-free.conf
[Service]
Environment=COMFYUI_IDLE_FREE_SECONDS=1800
```

在 Windows 上，可以在啟動 ComfyUI 的 `.bat` 裡加一行 `set COMFYUI_IDLE_FREE_SECONDS=1800`。

## 實測結果

以下是在 RTX 3060 12GB 上和一個 embedding 服務共用同一張卡時的測試結果：

- SDXL workflow 跑完後，ComfyUI 佔 4784 MiB。閒置時間到了以後，node 觸發卸載，ComfyUI 降到 304 MiB。
- 保留節點快取（預設）時，卸載後還會留幾百 MiB。設定 `COMFYUI_IDLE_FREE_CACHE=1` 可以再降一些。
- 有些模型（例如啟用 CPU offload 的 Nunchaku）跑完本來就會自己釋放 VRAM。這種情況卡上沒有模型，node 就不會觸發，這是預期行為。

## 測試

```sh
python3 test_idle_free.py
```

測試用 stub 取代 ComfyUI 的佇列和 `comfy.model_management`，不需要 ComfyUI、torch 或 GPU。

## 授權

MIT
