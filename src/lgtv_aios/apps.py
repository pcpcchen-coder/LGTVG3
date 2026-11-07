"""App 名稱 → webOS app id 解析。

webOS 上 app id 會因地區、韌體與 app 版本而異，下表只是「常見值」；
執行時會優先用電視回傳的 listLaunchPoints 清單（含實際安裝的台灣在地 app，
例如 LINE TV、KKTV、friDay 影音、愛奇藝、myVideo 等）做比對。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

YOUTUBE_APP_ID = "youtube.leanback.v4"
NETFLIX_APP_ID = "netflix"
BROWSER_APP_ID = "com.webos.app.browser"
LIVE_TV_APP_ID = "com.webos.app.livetv"

# 別名 → 常見 app id（G3 / webOS 23 社群回報值，實機請以 `lgtv apps` 為準）
KNOWN_APPS: dict[str, str] = {
    "youtube": YOUTUBE_APP_ID,
    "yt": YOUTUBE_APP_ID,
    "netflix": NETFLIX_APP_ID,
    "網飛": NETFLIX_APP_ID,
    "prime video": "amazon",
    "amazon": "amazon",
    "disney+": "com.disney.disneyplus-prod",
    "disney plus": "com.disney.disneyplus-prod",
    "apple tv": "com.apple.appletv",
    "spotify": "spotify-beehive",
    "browser": BROWSER_APP_ID,
    "瀏覽器": BROWSER_APP_ID,
    "web browser": BROWSER_APP_ID,
    "live tv": LIVE_TV_APP_ID,
    "電視": LIVE_TV_APP_ID,
    "第四台": LIVE_TV_APP_ID,
    "settings": "com.palm.app.settings",
    "設定": "com.palm.app.settings",
    "hdmi1": "com.webos.app.hdmi1",
    "hdmi2": "com.webos.app.hdmi2",
    "hdmi3": "com.webos.app.hdmi3",
    "hdmi4": "com.webos.app.hdmi4",
}


@dataclass(frozen=True)
class AppMatch:
    app_id: str
    title: str
    score: float
    source: str  # "exact-id" | "title" | "alias" | "fuzzy"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    return " ".join(text.replace("-", " ").replace("_", " ").split())


def resolve_app(query: str, launch_points: Iterable[dict] | None = None) -> AppMatch | None:
    """把使用者說的 app 名稱（中英文皆可）解析成 app id。

    launch_points: 電視 `listLaunchPoints` 回傳的清單（每筆含 id / title）。
    """
    q = normalize(query)
    if not q:
        return None
    points = [p for p in (launch_points or []) if p.get("id")]

    # 1) 直接給 app id
    for p in points:
        if p["id"] == query or normalize(p["id"]) == q:
            return AppMatch(p["id"], p.get("title", p["id"]), 1.0, "exact-id")

    # 2) 標題完全相同
    for p in points:
        if normalize(p.get("title", "")) == q:
            return AppMatch(p["id"], p["title"], 1.0, "title")

    # 3) 內建別名
    if q in KNOWN_APPS:
        app_id = KNOWN_APPS[q]
        title = next((p.get("title", app_id) for p in points if p["id"] == app_id), app_id)
        return AppMatch(app_id, title, 0.95, "alias")

    # 4) 模糊比對（標題包含 / 相似度）
    best: AppMatch | None = None
    for p in points:
        title = normalize(p.get("title", ""))
        if not title:
            continue
        if q in title or title in q:
            score = 0.9
        else:
            score = SequenceMatcher(None, q, title).ratio()
        if best is None or score > best.score:
            best = AppMatch(p["id"], p.get("title", p["id"]), score, "fuzzy")
    if best and best.score >= 0.6:
        return best

    # 5) 沒有清單時，退回把字串當 app id（呼叫端應自行驗證）
    if not points and "." in query:
        return AppMatch(query, query, 0.5, "exact-id")
    return None
