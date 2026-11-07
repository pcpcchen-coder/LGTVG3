"""DIAL（Discovery And Launch）備援路徑：不需配對即可叫 YouTube 播放指定影片。

流程：SSDP M-SEARCH → 取得裝置描述 LOCATION → 讀 `Application-URL` header →
POST {Application-URL}/YouTube，body 為 `v=<video_id>`。
LG webOS 的 Application-URL 一般在 port 36866，但請以 SSDP 結果為準。

實機（OLED65G3PSA）注意事項：
- 請求一定要帶 `Origin: https://www.youtube.com`，否則電視回 403。
- POST 回 200 只代表電視收下請求，不保證開始播放；實測 YouTube 已在前景時
  DIAL 不一定會切換影片，所以 DIAL 只當最後備援，播放結果請用截圖確認。
"""

from __future__ import annotations

import asyncio
import socket

import aiohttp

DIAL_ST = "urn:dial-multiscreen-org:service:dial:1"
YOUTUBE_ORIGIN = "https://www.youtube.com"
DEFAULT_APP_URL = "http://{host}:36866/apps"
SSDP_ADDR = ("239.255.255.250", 1900)


async def discover_locations(timeout: float = 3.0, target_host: str | None = None) -> list[str]:
    """回傳 SSDP 回應中的 LOCATION URL；指定 target_host 時只保留該主機。"""
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        f"ST: {DIAL_ST}\r\n\r\n"
    ).encode()

    def _run() -> list[str]:
        found: list[str] = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.settimeout(timeout)
            sock.sendto(msg, SSDP_ADDR)
            while True:
                try:
                    data, (addr, _) = sock.recvfrom(4096)
                except (TimeoutError, socket.timeout):
                    break
                if target_host and addr != target_host:
                    continue
                for line in data.decode(errors="ignore").split("\r\n"):
                    if line.lower().startswith("location:"):
                        loc = line.split(":", 1)[1].strip()
                        if loc not in found:
                            found.append(loc)
        return found

    return await asyncio.to_thread(_run)


async def get_application_url(location: str, session: aiohttp.ClientSession) -> str | None:
    async with session.get(location, timeout=aiohttp.ClientTimeout(total=5)) as resp:
        url = resp.headers.get("Application-URL")
    return url.rstrip("/") if url else None


async def launch_youtube(
    host: str, video_id: str, application_url: str | None = None, timeout: float = 3.0
) -> dict:
    """透過 DIAL 讓電視 YouTube 播放影片。回傳 {"status": http_status, "app_url": ...}。"""
    async with aiohttp.ClientSession() as session:
        app_url = application_url
        if not app_url:
            for loc in await discover_locations(timeout, target_host=host):
                app_url = await get_application_url(loc, session)
                if app_url:
                    break
        if not app_url:
            app_url = DEFAULT_APP_URL.format(host=host)  # LG 常見值，最後手段
        async with session.post(
            f"{app_url}/YouTube",
            data=f"v={video_id}",
            headers={"Content-Type": "text/plain; charset=utf-8", "Origin": YOUTUBE_ORIGIN},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            return {"status": resp.status, "app_url": app_url, "instance": resp.headers.get("Location")}


async def youtube_app_info(host: str, application_url: str | None = None, timeout: float = 3.0) -> dict | None:
    """讀取電視 DIAL 的 YouTube app 資訊（state、brand、model）；不是 DIAL 裝置則回傳 None。

    不需配對，可用來辨識區網上的裝置是不是 LG 電視。
    """
    import xml.etree.ElementTree as ET

    url = f"{(application_url or DEFAULT_APP_URL.format(host=host)).rstrip('/')}/YouTube"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers={"Origin": YOUTUBE_ORIGIN}, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    return None
                text = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    out: dict[str, str] = {}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("name", "state", "brand", "model") and el.text and tag not in out:
            out[tag] = el.text.strip()
    return out
