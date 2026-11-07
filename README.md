# LGTVG3 — 讓 AIOS 控制 LG G3 電視

讓 AI 代理（AIOS）透過區網控制 LG OLED G3（webOS 23）：開關機、用 YouTube 播你指定的節目、開 Netflix / Disney+ / LINE TV 等 App、調音量、切 HDMI。

- 研究報告：[docs/LG_G3_整合介面研究.md](docs/LG_G3_整合介面研究.md)
- 原型程式：`src/lgtv_aios/`（Python 3.11+，以 Home Assistant 使用的 `aiowebostv` 為底層）

## 運作原理

| 功能 | 介面 |
|---|---|
| 開機 | Wake-on-LAN |
| 其他控制 | webOS SSAP（`wss://<電視IP>:3001`，首次需在電視上按「允許」配對） |
| YouTube 播指定影片 | SSAP 啟動 `youtube.leanback.v4` + `contentTarget`，失敗自動改 `contentId` → DIAL |
| 依節目名稱找影片 | YouTube Data API v3 → yt-dlp → 解析 youtube.com 搜尋頁（免 key，順序與 YouTube 上相同） |
| 看電視畫面 | SSAP `tv/executeOneShot` 截圖（960×540 JPEG），讓 AI 代理確認是否真的在播 |
| 找電視 | SSDP；收不到時改掃描本機 /24 網段的 3001 埠，再用 DIAL 讀 brand/model 確認 |

## 快速開始

**1. 設定電視**：設定 → 一般 → 裝置 → 電視管理／外部裝置 → 開啟「透過 Wi‑Fi 開機」與「LG Connect Apps」；在路由器幫電視設固定 IP，並記下 MAC。

**2. 安裝**（請在與電視同一個區網的主機上）：

```bash
pip install -e ".[mcp]"
```

**3. 搜尋電視並配對**（電視需開機，畫面會跳出提示，用遙控器按「允許」）：

```bash
lgtv discover
```

```bash
lgtv setup --pair
```

`setup` 沒給 `--host` 時會自動搜尋區網；只找到一台就直接配對，並自動偵測電視 MAC（Wake-on-LAN 用）。也可手動指定：`lgtv setup --host 192.168.1.50 --mac AA:BB:CC:DD:EE:FF --youtube-api-key <你的 key> --pair`。

**4. 試用：**

```bash
lgtv youtube "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

```bash
lgtv youtube "孤獨的美食家"
```

```bash
lgtv launch "LINE TV"
```

```bash
lgtv apps
```

```bash
lgtv screenshot --out tv.jpg
```

其他指令：`profile --index 0 --replay <影片>`（YouTube 帳號選擇畫面）、`discover --subnet 192.168.1.0/24`、`on`、`off`、`status`、`volume 20|up|down`、`mute on|off`、`media pause`、`button HOME`、`type "文字" --enter`、`input HDMI_1`、`url <網址>`、`netflix <titleId>`、`notify "訊息"`、`screen off`。所有輸出皆為 JSON。

## 接到 AIOS

### 方式一：MCP（推薦）

`lgtv-mcp` 是 stdio MCP server，提供 `tv_play_youtube`、`tv_search_youtube`、`tv_launch_app`、`tv_power`、`tv_volume` 等 14 個工具。設定範例見 [examples/mcp_config.json](examples/mcp_config.json)。AIOS 在遠端時用 `lgtv-mcp --http`，並放在 VPN 或有認證的反向代理後面。

### 方式二：Python API

```python
from lgtv_aios import LGTVController, TVConfig

async with LGTVController(TVConfig.load()) as tv:
    res = await tv.play_youtube("TVBS新聞直播")   # 搜尋並播第一筆
    shot = await tv.capture_screen("tv.jpg")      # 交給 AIOS 的視覺模型確認畫面
    # 若畫面停在 YouTube「誰在觀看」：
    await tv.youtube_select_profile(index=0)      # 選帳號並自動重播剛才的影片
    await tv.launch_app("Netflix")
```

所有方法都回傳可 JSON 序列化的 dict，電視連不上時丟 `TVNotReachable`。

### YouTube「誰在觀看」帳號畫面

YouTube 每次冷啟動都可能先跳出帳號選擇畫面，**這時啟動參數裡的影片會被丟掉**，所以 `play_youtube` 回傳 `ok: true` 也不代表畫面在播。建議 AIOS 的流程是：

1. `play_youtube(...)`
2. 等 5–10 秒後 `capture_screen()`，看畫面是影片、廣告還是帳號選擇
3. 如果是帳號選擇，呼叫 `youtube_select_profile(index)`，它會按鍵選帳號，再重送一次剛才的影片

`youtube_select_profile` 只能在帳號畫面使用：影片播放中按 ENTER 會暫停或叫出控制列。

### 方式三：CLI

讓 AIOS 以 shell 工具執行 `lgtv ...`，再解析 JSON 輸出。

## 設定與環境變數

設定檔：`~/.config/lgtv-aios/lgtv-aios.json`（可用 `LGTV_AIOS_CONFIG` 改路徑，權限 600，內含配對金鑰，請勿提交到 git）。
環境變數 `LGTV_HOST`、`LGTV_MAC`、`LGTV_CLIENT_KEY`、`YOUTUBE_API_KEY` 會覆蓋設定檔。

## 測試

```bash
pip install -e ".[dev]" && pytest
```

`tests/fake_tv.py` 是模擬 webOS SSAP 的 WebSocket 伺服器，讓真正的 `aiowebostv` client 能在沒有電視時跑完整的配對、啟動 App、按鍵流程。

## 現況

2026-11-08 在實機 **OLED65G3PSA** 上驗證：

| 項目 | 結果 |
|---|---|
| 區網探索（SSDP、網段掃描備援） | ✅ 兩種都找得到 |
| SSAP 配對、儲存 client-key | ✅ |
| 依名稱搜尋 YouTube 並播第一筆（免 API key） | ✅ `lgtv youtube "TVBS新聞直播"` |
| `contentTarget` 用 `https://www.youtube.com/tv?v=ID` 或 `v=ID` | ✅ 兩種格式都能播 |
| YouTube 帳號選擇畫面 | ⚠️ 會吃掉影片；`youtube_select_profile()` 選帳號後重播 |
| DIAL 播放 | ⚠️ 一定要帶 `Origin: https://www.youtube.com`（否則 403）；回 200 但實測沒切換影片，只當最後備援 |
| 截圖 | ✅ YouTube 畫面（含廣告）可擷取 |
| 電視 MAC 偵測 | ✅ 由 SSAP 回報有線 / Wi‑Fi 兩個 MAC |
| Wake-on-LAN 開機、Netflix 深層連結、HDMI 切換 | ⏳ 尚未在實機測試 |

模擬電視的端到端測試也涵蓋上面發現的行為（`tests/test_verified_on_g3.py`）。
