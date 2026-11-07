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
| 依節目名稱找影片 | YouTube Data API v3（或選用 yt-dlp） |

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

其他指令：`on`、`off`、`status`、`volume 20|up|down`、`mute on|off`、`media pause`、`button HOME`、`type "文字" --enter`、`input HDMI_1`、`url <網址>`、`netflix <titleId>`、`notify "訊息"`、`screen off`。所有輸出皆為 JSON。

## 接到 AIOS

### 方式一：MCP（推薦）

`lgtv-mcp` 是 stdio MCP server，提供 `tv_play_youtube`、`tv_search_youtube`、`tv_launch_app`、`tv_power`、`tv_volume` 等 14 個工具。設定範例見 [examples/mcp_config.json](examples/mcp_config.json)。AIOS 在遠端時用 `lgtv-mcp --http`，並放在 VPN 或有認證的反向代理後面。

### 方式二：Python API

```python
from lgtv_aios import LGTVController, TVConfig

async with LGTVController(TVConfig.load()) as tv:
    await tv.play_youtube("周杰倫 演唱會")
    await tv.launch_app("Netflix")
```

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

- ✅ 以模擬電視通過端到端測試（配對、YouTube 啟動參數、退回 `contentId`、App 名稱解析、音量、按鍵）
- ⏳ 尚未在實機 G3 驗證，待驗清單見研究報告第 8 節
