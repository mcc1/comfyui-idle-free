# comfyui-idle-free

ComfyUI 閒置一段時間後，自動把模型從 VRAM 卸載。

ComfyUI 跑完一個 workflow 之後，模型會一直留在顯示卡上，直到下一個 workflow 需要空間，或有人手動呼叫 `POST /free` 才會放掉。如果同一張卡上還有其他服務（embedding、LLM、另一個推論服務），閒置的 ComfyUI 可能會佔著好幾 GB 的 VRAM，一放就是好幾天。

這個 custom node 補上 ComfyUI 缺少的閒置逾時機制。只要佇列空了一段時間（預設 1 小時），它就做和 `POST /free {"unload_models": true}` 一樣的事。

> English: a ComfyUI custom node that unloads models from VRAM after the prompt queue has been idle for a while (default 1 hour), the same thing `POST /free {"unload_models": true}` does. Configure it in Settings > Idle Free; changes apply without a restart.

## 運作方式

- 不會在節點清單裡新增任何節點。載入套件時會啟動一個背景 daemon thread，並在 ComfyUI 的設定畫面加上「Idle Free」分類。
- 每隔一段時間（預設 30 秒，可在設定畫面調整）檢查一次 prompt queue。如果超過設定的閒置時間都沒有工作在跑或在排隊，而且卡上確實載入了模型，它就會設定 `unload_models` flag。
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

### 更新

```sh
cd ComfyUI/custom_nodes/comfyui-idle-free
git pull
```

接著重啟 ComfyUI。設定畫面裡改過的值會保留。

## 設定

打開 ComfyUI 的 **Settings（齒輪）**，在左欄「Other（其他）」底下找到 **Idle Free**，或直接在設定的搜尋框輸入 `idle`：

| 設定 | 預設值 | 說明 |
|---|---|---|
| Enabled | 開 | 是否在閒置時卸載模型。 |
| Idle minutes | 60 | 閒置幾分鐘後卸載。 |
| Poll seconds | 30 | 每隔幾秒檢查一次佇列（1 到 3600）。實際卸載最多會比閒置時間晚一個間隔；設定的變更也是在下一次檢查時生效。 |
| Clear cache | 關 | 連節點輸出快取一起清掉（等同 `free_memory` flag），會多釋放幾百 MB 的 VRAM 和一些 RAM，但下次跑同一個 workflow 時每個節點都要重算。 |

改完**不需要重啟** ComfyUI，下一次檢查（預設最多 30 秒）就會生效，log 會出現 `settings changed: ...`。

### 用環境變數設定預設值（選用）

沒有開瀏覽器的伺服器，或想統一部署時，可以用環境變數設定預設值。只要在設定畫面改過某一項，就以設定畫面的值為準。

| 環境變數 | 預設值 | 說明 |
|---|---|---|
| `COMFYUI_IDLE_FREE_SECONDS` | `3600` | 閒置秒數。設為 `0` 代表預設關閉（之後仍可在設定畫面打開）。 |
| `COMFYUI_IDLE_FREE_POLL` | `30` | 預設每隔幾秒檢查一次佇列。 |
| `COMFYUI_IDLE_FREE_CACHE` | `0` | 設為 `1` 代表預設連快取一起清。 |

### 多使用者模式

設定畫面的值存在 `ComfyUI/user/default/comfy.settings.json`。用 `--multi-user` 啟動時，這個 node 只會讀 `default` 使用者的設定，其他使用者在設定畫面改的值不會生效。

## 實測結果

以下是在 RTX 3060 12GB 上，和一個 embedding 服務共用同一張卡時的測試結果：

- 在設定畫面把閒置時間改成 2 分鐘，12 秒後 log 就出現 `settings changed: unloading models after 120s idle ...`，不需要重啟。
- SDXL workflow 跑完後，ComfyUI 佔 4814 MiB。閒置 2 分鐘後 node 觸發卸載，ComfyUI 降到 270 MiB，釋放約 4.5 GB。同一張卡上的 embedding 服務沒有受影響。
- 在設定畫面關掉 Enabled，3 秒後 log 出現 `settings changed: disabled`。
- 保留節點快取（預設）時，卸載後還會留幾百 MiB。打開 Clear cache 可以再降一些。
- 有些模型（例如啟用 CPU offload 的 Nunchaku）跑完本來就會自己釋放 VRAM。這種情況卡上沒有模型，node 就不會觸發，這是預期行為。
- 有裝 ComfyUI-Manager 時，瀏覽器 console 可能出現 `[ConflictDetection] No Registry data found for mcc1/comfyui-idle-free`。這只是因為還沒上架 Comfy Registry，不影響功能。

## 測試

```sh
python3 test_idle_free.py
```

測試用 stub 取代 ComfyUI 的佇列和 `comfy.model_management`，不需要 ComfyUI、torch 或 GPU。

## 授權

MIT
