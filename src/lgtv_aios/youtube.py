"""YouTube 相關：解析影片網址、組 webOS 啟動參數、依節目名稱搜尋影片。

搜尋來源（依序）：
1. YouTube Data API v3（需要 API key，最穩定、合規）
2. yt-dlp 的 `ytsearchN:`（選用套件，免 key；屬非官方途徑）
3. 直接解析 youtube.com 搜尋結果頁（免 key、免套件；非官方途徑，YouTube 改版時可能失效）
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import aiohttp

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PATH_ID_RE = re.compile(r"^/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{11})")


@dataclass(frozen=True)
class YouTubeTarget:
    video_id: str | None = None
    playlist_id: str | None = None
    start_seconds: int | None = None

    def tv_url(self) -> str:
        """YouTube TV（leanback）可接受的深層連結。"""
        parts = []
        if self.video_id:
            parts.append(f"v={self.video_id}")
        if self.playlist_id:
            parts.append(f"list={self.playlist_id}")
        if self.start_seconds:
            parts.append(f"t={self.start_seconds}")
        return "https://www.youtube.com/tv?" + "&".join(parts)

    def launch_params(self) -> dict:
        """給 ssap://system.launcher/launch 的 params。"""
        return {"contentTarget": self.tv_url()}


@dataclass(frozen=True)
class VideoResult:
    video_id: str
    title: str
    channel: str = ""
    is_live: bool = False

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def _parse_time(value: str | None) -> int | None:
    if not value:
        return None
    if value.isdigit():
        return int(value)
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def parse_youtube(text: str) -> YouTubeTarget | None:
    """解析影片 ID 或各種 YouTube 網址；不是 YouTube 目標則回傳 None。"""
    text = text.strip()
    if _VIDEO_ID_RE.match(text):
        return YouTubeTarget(video_id=text)
    if "://" not in text and ("youtube.com" in text or "youtu.be" in text):
        text = "https://" + text
    url = urlparse(text)
    host = (url.hostname or "").lower()
    if not (host.endswith("youtube.com") or host == "youtu.be"):
        return None
    qs = parse_qs(url.query)
    video_id = None
    if host == "youtu.be":
        candidate = url.path.lstrip("/").split("/")[0]
        video_id = candidate if _VIDEO_ID_RE.match(candidate) else None
    elif "v" in qs and _VIDEO_ID_RE.match(qs["v"][0]):
        video_id = qs["v"][0]
    else:
        m = _PATH_ID_RE.match(url.path)
        if m:
            video_id = m.group(1)
    playlist_id = qs.get("list", [None])[0]
    start = _parse_time(qs.get("t", qs.get("start", [None]))[0])
    if not video_id and not playlist_id:
        return None
    return YouTubeTarget(video_id=video_id, playlist_id=playlist_id, start_seconds=start)


async def search_data_api(
    query: str,
    api_key: str,
    max_results: int = 5,
    region_code: str = "TW",
    relevance_language: str = "zh-Hant",
    session: aiohttp.ClientSession | None = None,
) -> list[VideoResult]:
    """YouTube Data API v3 search.list（每次呼叫消耗 100 quota 單位）。"""
    params = {
        "part": "snippet",
        "type": "video",
        "q": query,
        "maxResults": str(max_results),
        "regionCode": region_code,
        "relevanceLanguage": relevance_language,
        "safeSearch": "none",
        "key": api_key,
    }
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.get(
            "https://www.googleapis.com/youtube/v3/search", params=params, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"YouTube Data API 錯誤 {resp.status}: {data.get('error', {}).get('message')}")
    finally:
        if own:
            await session.close()
    results = []
    for item in data.get("items", []):
        vid = item.get("id", {}).get("videoId")
        sn = item.get("snippet", {})
        if vid:
            results.append(
                VideoResult(
                    video_id=vid,
                    title=sn.get("title", ""),
                    channel=sn.get("channelTitle", ""),
                    is_live=sn.get("liveBroadcastContent") == "live",
                )
            )
    return results


def _search_ytdlp_sync(query: str, max_results: int) -> list[VideoResult]:
    import yt_dlp  # type: ignore[import-not-found]

    opts = {"quiet": True, "skip_download": True, "extract_flat": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
    out = []
    for e in (info or {}).get("entries", []) or []:
        if e and e.get("id"):
            out.append(
                VideoResult(
                    video_id=e["id"],
                    title=e.get("title", ""),
                    channel=e.get("channel") or e.get("uploader") or "",
                    is_live=e.get("live_status") == "is_live",
                )
            )
    return out


_INITIAL_DATA_RE = re.compile(r"var ytInitialData = (\{.*?\});</script>", re.S)


def parse_results_page(html: str, max_results: int = 5) -> list[VideoResult]:
    """從 youtube.com/results 頁面的 ytInitialData 取出影片（依頁面順序）。"""
    m = _INITIAL_DATA_RE.search(html)
    if not m:
        return []
    out: list[VideoResult] = []

    def walk(node) -> None:
        if len(out) >= max_results:
            return
        if isinstance(node, dict):
            v = node.get("videoRenderer")
            if isinstance(v, dict) and v.get("videoId"):
                title = "".join(r.get("text", "") for r in v.get("title", {}).get("runs", []))
                channel = (v.get("ownerText", {}).get("runs") or [{}])[0].get("text", "")
                badges = json.dumps(v.get("badges", []), ensure_ascii=False)
                out.append(VideoResult(video_id=v["videoId"], title=title, channel=channel, is_live="LIVE" in badges))
                return
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(json.loads(m.group(1)))
    return out


async def search_web(query: str, max_results: int = 5, hl: str = "zh-TW", gl: str = "TW") -> list[VideoResult]:
    """免 API key：抓 youtube.com 搜尋結果頁，順序與在 YouTube 上搜尋看到的一致。"""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://www.youtube.com/results",
            params={"search_query": query, "hl": hl, "gl": gl},
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": hl},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            html = await resp.text()
    return parse_results_page(html, max_results)


async def search(
    query: str,
    api_key: str | None = None,
    max_results: int = 5,
    region_code: str = "TW",
    relevance_language: str = "zh-Hant",
) -> list[VideoResult]:
    """依節目名稱搜尋 YouTube 影片。"""
    if api_key:
        return await search_data_api(query, api_key, max_results, region_code, relevance_language)
    try:
        import yt_dlp  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        return await search_web(query, max_results, gl=region_code)
    return await asyncio.to_thread(_search_ytdlp_sync, query, max_results)
