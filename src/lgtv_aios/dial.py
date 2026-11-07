"""DIAL（Discovery And Launch）備援路徑：不需配對即可叫 YouTube 播放指定影片。

流程：SSDP M-SEARCH → 取得裝置描述 LOCATION → 讀 `Application-URL` header →
POST {Application-URL}/YouTube，body 為 `v=<video_id>`。
LG webOS 的 Application-URL 一般在 port 36866，但請以 SSDP 結果為準。
"""

from __future__ import annotations

import asyncio
import socket

import aiohttp

DIAL_ST = "urn:dial-multiscreen-org:service:dial:1"
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
            app_url = f"http://{host}:36866/apps"  # LG 常見值，最後手段
        async with session.post(
            f"{app_url}/YouTube",
            data=f"v={video_id}",
            headers={"Content-Type": "text/plain; charset=utf-8"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            return {"status": resp.status, "app_url": app_url, "instance": resp.headers.get("Location")}
