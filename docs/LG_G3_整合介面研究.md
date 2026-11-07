# LG OLED G3（webOS 23）整合介面研究報告

> 目標：讓 AIOS（AI 代理）用自然語言控制電視：開機、開 YouTube 播指定節目、開其他 App、調音量等。
> 日期：2026-11 ｜ 狀態：已完成原型（`src/lgtv_aios`）並以模擬電視通過端到端測試，**尚未在實機 G3 上驗證**。

---

## 1. 結論摘要

| 需求 | 建議做法 | 成熟度 |
|---|---|---|
| 開機 | Wake-on-LAN（需開啟「透過 Wi‑Fi 開機」） | 社群廣泛使用 |
| 關機、音量、靜音、按鍵、切 HDMI | webOS **SSAP**（區網 WebSocket，`wss://<TV>:3001`） | 成熟，Home Assistant 官方整合使用 |
| 開任意 App | SSAP `system.launcher/launch` + App 名稱模糊比對 | 成熟 |
| YouTube 播放指定影片 | SSAP launch `youtube.leanback.v4` 帶 `contentTarget`；失敗時退回 `contentId`，再退回 **DIAL** | 社群做法，需實機確認 |
| 依「節目名稱」播放 | 先用 YouTube Data API v3（或 yt-dlp）搜尋取得 video ID，再用上述方式播放 | 穩定（搜尋在電視外完成） |
| 給 AI 代理使用 | 本專案提供 **MCP server**（`lgtv-mcp`）、Python API、CLI 三種接法 | 已完成原型 |

**一句話**：G3 沒有官方公開的「第三方遙控 API 文件」，但 LG 自家手機 App 使用的 **SSAP over WebSocket** 是事實標準（Home Assistant、Homebridge、各種開源 remote 都用它），足以完成所有需求；唯一要額外處理的是「節目名稱 → 影片 ID」的搜尋，以及待機時要靠 Wake-on-LAN 喚醒。

---

## 2. G3 可用的整合介面總覽

| 介面 | 傳輸 | 認證 | 能做什麼 | 適用性 |
|---|---|---|---|---|
| **SSAP**（Simple Service Access Protocol） | 區網 WebSocket；webOS 23 走 `wss://:3001`（自簽憑證），舊版 `ws://:3000` | 首次在電視上按「允許」，取得 `client-key` | 幾乎所有遙控功能：App、音量、輸入源、媒體控制、通知、文字輸入、按鍵、滑鼠 | ⭐ 主力 |
| **DIAL**（Discovery And Launch） | SSDP 探索 + HTTP（LG 常見 port 36866） | 無 | 啟動支援 DIAL 的 App 並傳參數（YouTube `v=<id>`、Netflix 等） | ⭐ YouTube 備援 |
| **Wake-on-LAN** | UDP 廣播 magic packet | 無 | 待機時開機 | ⭐ 開機必需 |
| LG ThinQ Connect（雲端 API） | HTTPS + MQTT | Personal Access Token；**正式使用需與 LG 簽合作** | 以家電為主；公開資料中**未確認支援電視** | ✗ 不建議 |
| HDMI-CEC | 透過接在電視上的裝置（如 Apple TV、樹莓派） | 無 | 開關機、切輸入、部分遙控鍵 | △ 有額外硬體時可作為備援 |
| AirPlay 2 | 從 Apple 裝置投放 | 無 | 投放影片/畫面 | △ 不適合由代理主動控制 |
| webOS 開發者模式 / root（Luna bus） | SSH | 開發者帳號 / 破解 | 完整系統控制 | ✗ 風險高、會失效，不建議 |

---

## 3. SSAP 細節（主力介面）

### 3.1 連線與配對流程

```
AIOS ──wss://<TV_IP>:3001 (自簽憑證，需關閉驗證)──▶ G3
  1. {"type":"hello"}                         → 取得 TV 基本資訊
  2. ssap://system/getSystemInfo               → 新版 webOS 要在註冊前呼叫
  3. {"type":"register", payload: manifest(+client-key)}
       ├ 沒有 client-key → TV 畫面跳出「允許連線？」→ 回 {"type":"registered","payload":{"client-key":"…"}}
       └ 有 client-key   → 直接 registered
  4. ssap://com.webos.service.networkinput/getPointerInputSocket
       → 回傳第二條 WebSocket 路徑，用來送「遙控器按鍵 / 滑鼠」
```

- `client-key` 等同一把遙控器，**請妥善保存**（本專案存在 `~/.config/lgtv-aios/lgtv-aios.json`，權限 600）。
- 2023 年後的韌體通常拒絕 `ws://:3000`，只接受 `wss://:3001`；本專案使用的 `aiowebostv`（Home Assistant 官方整合同一套）會先試 3000 再自動改用 3001。
- 若電視「設定 → 一般 → 裝置 → 外部裝置/電視管理」中的 **LG Connect Apps** 被關閉，SSAP 會連不上。

### 3.2 訊息格式

```json
{"id": 7, "type": "request", "uri": "ssap://system.launcher/launch",
 "payload": {"id": "youtube.leanback.v4",
             "params": {"contentTarget": "https://www.youtube.com/tv?v=dQw4w9WgXcQ"}}}
```
回應：`{"id": 7, "type": "response", "payload": {"returnValue": true, ...}}`；訂閱類（`"type":"subscribe"`）會持續推送狀態變化。

### 3.3 常用 endpoint

| 功能 | URI（皆加 `ssap://` 前綴） | payload |
|---|---|---|
| 啟動 App | `system.launcher/launch` | `{"id": appId, "params": {...}}` 或 `{"id", "contentId"}` |
| 關閉 App | `system.launcher/close` | `{"id": appId}` |
| 已安裝 App 清單 | `com.webos.applicationManager/listLaunchPoints` | – |
| 目前前景 App | `com.webos.applicationManager/getForegroundAppInfo` | – |
| 關機 | `system/turnOff` | – |
| 電源狀態 | `com.webos.service.tvpower/power/getPowerState` | – |
| 關閉/開啟螢幕（聲音繼續） | `com.webos.service.tvpower/power/turnOffScreen` / `turnOnScreen` | – |
| 音量 | `audio/setVolume`、`audio/volumeUp`、`audio/volumeDown`、`audio/setMute` | `{"volume": 0-100}` / `{"mute": bool}` |
| 媒體控制 | `media.controls/play`、`pause`、`stop`、`rewind`、`fastForward` | – |
| 切換輸入源 | `tv/switchInput` | `{"inputId": "HDMI_1"}` |
| 外部輸入清單 | `tv/getExternalInputList` | – |
| 文字輸入 | `com.webos.service.ime/insertText`、`sendEnterKey` | `{"text": "...", "replace": 0}` |
| 跳出通知 | `system.notifications/createToast` | `{"message": "..."}` |
| 遙控器按鍵 | （第二條 input socket）`type:button\nname:HOME\n\n` | HOME/BACK/ENTER/UP/DOWN/數字… |

---

## 4. YouTube：播放「我要的節目」

### 4.1 YouTube on webOS 的啟動方式

| 方式 | 指令 | 備註 |
|---|---|---|
| A. `contentTarget`（本專案預設） | `launch {"id":"youtube.leanback.v4","params":{"contentTarget":"https://www.youtube.com/tv?v=<ID>"}}` | webosbrew/youtube-webos 專案與 HA 社群採用；也接受 `v=<ID>` 簡寫 |
| B. `contentId` | `launch {"id":"youtube.leanback.v4","contentId":"https://www.youtube.com/tv?v=<ID>"}` | 部分韌體只吃這種，A 失敗時自動改用 |
| C. DIAL | `POST http://<TV>:36866/apps/YouTube`，body `v=<ID>` | 不需配對；SSAP 都失敗時的最後手段（本專案先用 SSDP 找正確的 Application-URL） |

已知狀況（社群回報，需實機驗證）：
- YouTube 若啟用多帳號，可能先跳出「**誰在看？**」選擇畫面，影片要等選完帳號才播放。可在 YouTube app 設定中關閉帳號選擇，或由代理接著送 `ENTER` 按鍵。
- 從 App 外部叫影片時，部分版本會跳出確認提示。
- 播放清單可在 URL 加 `&list=<PLAYLIST_ID>`、起始秒數加 `&t=<秒>`（本專案已支援，**未經實機確認**）。

### 4.2 節目名稱 → 影片 ID

YouTube TV app 沒有公開的「搜尋深層連結」，因此在電視外先搜尋：

| 來源 | 優點 | 缺點 |
|---|---|---|
| **YouTube Data API v3** `search.list`（建議） | 官方、穩定、可指定 `regionCode=TW`、`relevanceLanguage=zh-Hant` | 需 Google Cloud API key；每次搜尋耗 100 單位，預設每日 10,000 單位 ≈ 每天 100 次搜尋 |
| yt-dlp `ytsearch` | 免 key | 非官方途徑，可能違反 YouTube 服務條款、隨時失效 |
| 電視內打字搜尋（launch YouTube → 按鍵移到搜尋框 → `insertText`） | 不需額外服務 | 依賴 UI 版面，脆弱，只建議作為最後手段 |

**AI 代理最佳流程**：
1. 使用者說「播孤獨的美食家最新一集」→ 代理呼叫 `tv_search_youtube` 取得候選清單。
2. 代理依標題/頻道/是否為官方頻道判斷，必要時反問使用者。
3. 呼叫 `tv_play_youtube(<video_id>)` → 工具自動 WOL 開機 → 等待 SSAP 上線 → 啟動 YouTube 播放。

---

## 5. 其他 App

- **App ID 不固定**：會因地區、韌體、App 版本而異，所以本專案在執行時讀取電視的 `listLaunchPoints`，用「App ID → 完全相同標題 → 內建別名 → 模糊比對」的順序解析使用者說的名稱（例如「line tv」、「網飛」、「disney plus」）。
- 常見 ID（以 `lgtv apps` 實機結果為準）：

| App | 常見 app id |
|---|---|
| YouTube | `youtube.leanback.v4` |
| Netflix | `netflix` |
| Prime Video | `amazon` |
| Disney+ | `com.disney.disneyplus-prod` |
| Apple TV | `com.apple.appletv` |
| Spotify | `spotify-beehive` |
| 網頁瀏覽器 | `com.webos.app.browser` |
| 直播電視 | `com.webos.app.livetv` |
| HDMI 1–4 | `com.webos.app.hdmi1` … `hdmi4` |

- **深層連結（直接播某部劇）**：
  - Netflix：`contentId = "m=https://www.netflix.com/watch/<titleId>&source_type=4"`（社群做法，已包成 `play_netflix`）。
  - 瀏覽器：`params = {"target": "<url>"}`（已包成 `open_url`）。
  - 台灣在地 App（LINE TV、KKTV、friDay 影音、愛奇藝、myVideo、Hami Video…）：深層連結參數多半未公開（LG 的 deep-link 規格屬於合作夥伴 NDA 文件）。可行做法是「啟動 App → 用 `tv_button` / `tv_type_text` 操作搜尋」，或逐一實機觀察 App 的 launch 參數。

---

## 6. 開機：Wake-on-LAN 設定

1. 電視：**設定 → 一般 → 裝置 → 電視管理（或「外部裝置」）→ 開啟「透過 Wi‑Fi 開機 / 行動裝置開啟電視」**（選單名稱依韌體略有不同）。
2. 確認 **LG Connect Apps** 為開啟。
3. 部分使用者回報 **Quick Start+** 會影響喚醒行為，若喚醒不穩可切換此選項測試。
4. 取得電視 MAC：設定 → 一般 → 關於此電視 / 網路狀態。**有線與 Wi‑Fi 的 MAC 不同**，要用目前連線那張。
5. 在路由器把電視設為**固定 IP**（DHCP 保留）。
6. 代理所在主機必須與電視在**同一個 L2 網段**；若在 Docker 中執行要用 `network_mode: host`；路由器的「AP 隔離 / 訪客網路」會擋住廣播。
7. 社群經驗：**連送多個 magic packet** 成功率較高（本專案預設送 3 次，並在等待期間持續補送）。

---

## 7. 給 AIOS 的整合架構

```
 使用者語音/文字
        │
   ┌────▼─────┐   MCP (stdio / streamable-http)   ┌──────────────┐  wss:3001 / DIAL / WOL   ┌────────┐
   │  AIOS    │ ─────────────────────────────────▶│  lgtv-mcp     │ ───────────────────────▶ │ LG G3  │
   │ (LLM 代理) │◀──── tool 結果（JSON）──────────── │ (家中區網主機) │                          │ webOS23│
   └──────────┘                                    └──────┬───────┘                          └────────┘
                                                          │ HTTPS
                                                   YouTube Data API（搜尋）
```

三種接法，依 AIOS 的能力選擇：

1. **MCP（推薦）**：AIOS 若支援 Model Context Protocol，直接把 `lgtv-mcp` 加入工具伺服器。已提供 16 個工具：`tv_status`、`tv_power`、`tv_list_apps`、`tv_launch_app`、`tv_search_youtube`、`tv_play_youtube`、`tv_play_netflix`、`tv_open_url`、`tv_volume`、`tv_media`、`tv_button`、`tv_type_text`、`tv_set_input`、`tv_notify`、`tv_screenshot`、`tv_youtube_select_profile`。
2. **Python API**：`from lgtv_aios import LGTVController`，把方法包成 AIOS 的 function-calling 工具。
3. **CLI**：`lgtv youtube "節目名稱"`，所有輸出皆為 JSON，適合以 shell 工具呼叫。

**部署重點**：控制端必須跑在**家中區網**（NAS、樹莓派、常開電腦）。若 AIOS 在雲端，請以 `lgtv-mcp --http` 搭配 VPN（Tailscale/WireGuard）或有認證的反向代理，**切勿把電視控制埠直接暴露到網際網路**。

---

## 8. 風險與待實機驗證清單

| # | 項目 | 驗證方式 |
|---|---|---|
| 1 | G3 韌體是否只接受 `wss://:3001` | `lgtv setup --host <IP> --pair -v` 觀察 log |
| 2 | `contentTarget` vs `contentId` 哪個能直接播放 | `lgtv youtube <網址> --method ssap`，看回傳 `method` |
| 3 | 「誰在看？」帳號選擇畫面是否擋住播放 | 實機觀察；必要時加送 `ENTER` |
| 4 | DIAL 的 Application-URL / port | `lgtv youtube <網址> --method dial` |
| 5 | 待機（螢幕全黑）多久後 WOL 仍有效、是否需 Quick Start+ | 關機 1 分鐘 / 1 小時 / 隔夜後測 `lgtv on` |
| 6 | 台灣在地 App 的實際 app id | `lgtv apps` |
| 7 | 播放清單 `list=`、起始時間 `t=` 是否被 YouTube TV 接受 | 實機測試 |

其他風險：
- SSAP 非 LG 官方對第三方公開承諾的 API，**韌體更新可能改變行為**（過去曾發生 3000→3001 的變更）。
- YouTube 搜尋請使用官方 Data API 以符合服務條款；yt-dlp 僅作為開發測試用的選項。

---

## 9. 參考資料

- aiowebostv（Home Assistant 官方 webOS 整合使用的函式庫）：https://github.com/home-assistant-libs/aiowebostv
- Home Assistant LG webOS TV 整合文件：https://www.home-assistant.io/integrations/webostv/
- Home Assistant「Device is requested to turn on」+ WOL：https://www.home-assistant.io/triggers/webostv.turn_on/
- lgtv2（Node.js，說明 2023+ 韌體需 wss:3001）：https://github.com/hobbyquaker/lgtv2
- LGWebOSRemote：https://github.com/klattimer/LGWebOSRemote
- webosbrew/youtube-webos（`contentTarget: "v=<id>"` 用法）：https://github.com/webosbrew/youtube-webos
- HA 社群：Play YouTube on smart TV：https://community.home-assistant.io/t/play-youtube-on-smart-tv/101407
- HA 社群：play YouTube video by URL：https://community.home-assistant.io/t/lg-webos-smart-tv-play-youtube-video-by-url-doesnt-work/103511
- HA 社群：YouTube「誰在看」問題：https://community.home-assistant.io/t/play-specific-video-on-youtube-lg-webos-whos-watching/365134
- HA 社群：LG webOS Wake-on-LAN：https://community.home-assistant.io/t/lg-webos-wake-on-lan/91299
- ytcast（DIAL 啟動 YouTube 的實作）：https://github.com/MarcoLucidi01/ytcast
- LG ThinQ Connect 開發者文件：https://thinq.developer.lge.com/en/cloud/docs/thinq-connect
- YouTube Data API search.list：https://developers.google.com/youtube/v3/docs/search/list
