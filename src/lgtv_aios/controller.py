"""高階控制器：把 webOS SSAP、DIAL、Wake-on-LAN 包成 AI 代理好呼叫的動作。

所有方法都回傳可直接 JSON 序列化的 dict，方便給 LLM tool / MCP 使用。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp

from aiowebostv import WebOsClient
from aiowebostv.buttons import BUTTONS
from aiowebostv.exceptions import WebOsTvCommandError, WebOsTvError

from . import dial, wol, youtube
from .apps import BROWSER_APP_ID, NETFLIX_APP_ID, YOUTUBE_APP_ID, resolve_app
from .config import TVConfig

_LOGGER = logging.getLogger(__name__)

INSERT_TEXT = "com.webos.service.ime/insertText"
SEND_ENTER = "com.webos.service.ime/sendEnterKey"
SCREEN_CAPTURE = "tv/executeOneShot"
FOREGROUND_APP = "com.webos.applicationManager/getForegroundAppInfo"


_PROFILE_NOTE = (
    "若電視停在 YouTube「誰在觀看」帳號畫面，影片不會播放："
    "請呼叫 youtube_select_profile()，或用 capture_screen() 看畫面確認。"
)


class TVNotReachable(RuntimeError):
    """電視關機 / 不在網路上 / 無法連線。"""


class LGTVController:
    def __init__(self, config: TVConfig, client_factory=WebOsClient) -> None:
        if not config.host:
            raise ValueError("尚未設定電視 IP：請執行 `lgtv setup --host <IP>` 或設定 LGTV_HOST")
        self.config = config
        self._client_factory = client_factory
        self._client: WebOsClient | None = None
        self._lock = asyncio.Lock()
        self._last_youtube: youtube.YouTubeTarget | None = None

    # ------------------------------------------------------------------ 連線
    async def __aenter__(self) -> "LGTVController":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def connect(self, timeout: float = 10.0) -> WebOsClient:
        """連線（首次會在電視上跳出配對提示，請用遙控器按「允許」）。"""
        async with self._lock:
            if self._client is not None and self._client.is_connected():
                return self._client
            client = self._client or self._client_factory(
                self.config.host, client_key=self.config.client_key, connect_timeout=timeout
            )
            try:
                await client.connect()
            except (OSError, asyncio.TimeoutError, WebOsTvError) as err:
                self._client = None
                raise TVNotReachable(f"無法連線到電視 {self.config.host}：{err}") from err
            self._client = client
            if client.client_key and client.client_key != self.config.client_key:
                self.config.client_key = client.client_key
                path = self.config.save()
                _LOGGER.info("配對成功，client-key 已存到 %s", path)
            return client

    async def _try_connect(self, timeout: float) -> WebOsClient | None:
        try:
            return await self.connect(timeout=timeout)
        except TVNotReachable:
            return None

    # ------------------------------------------------------------------ 電源
    async def power_on(self, wait: float = 25.0) -> dict[str, Any]:
        client = await self._try_connect(timeout=3)
        if client and client.tv_state.is_on:
            return {"ok": True, "already_on": True}
        if client:
            # 已連得上但在待機（Quick Start+ 等），先試 SSAP 開機
            try:
                await client.power_on()
            except WebOsTvError:
                pass
        if not self.config.mac:
            if client:
                return {"ok": True, "method": "ssap", "note": "未設定 MAC，無法送 Wake-on-LAN"}
            raise TVNotReachable("電視無回應且未設定 MAC 位址，無法用 Wake-on-LAN 開機")
        await wol.send_magic_packet(self.config.mac, self.config.broadcast)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            client = await self._try_connect(timeout=3)
            if client and client.tv_state.is_on:
                return {"ok": True, "method": "wake-on-lan"}
            await asyncio.sleep(1.5)
            await wol.send_magic_packet(self.config.mac, self.config.broadcast, repeat=1)
        raise TVNotReachable(
            "已送出 Wake-on-LAN 但電視未在時限內上線；請確認電視已開啟「透過 Wi‑Fi 開機」"
        )

    async def ensure_on(self) -> WebOsClient:
        client = await self._try_connect(timeout=3)
        if client is None or not client.tv_state.is_on:
            await self.power_on()
            client = await self.connect()
        return client

    async def power_off(self) -> dict[str, Any]:
        client = await self._try_connect(timeout=3)
        if client is None:
            return {"ok": True, "already_off": True}
        await client.power_off()
        return {"ok": True}

    async def screen(self, on: bool) -> dict[str, Any]:
        client = await self.connect()
        await client.set_screen_state(on)
        return {"ok": True, "screen_on": on}

    # ------------------------------------------------------------------ 狀態
    async def status(self) -> dict[str, Any]:
        client = await self._try_connect(timeout=3)
        if client is None:
            return {"reachable": False, "is_on": False}
        st = client.tv_state
        app = st.apps.get(st.current_app_id or "", {})
        return {
            "reachable": True,
            "is_on": st.is_on,
            "is_screen_on": st.is_screen_on,
            "power_state": st.power_state.get("state"),
            "current_app_id": st.current_app_id,
            "current_app_title": app.get("title"),
            "volume": st.volume,
            "muted": st.muted,
            "sound_output": st.sound_output,
            "model": client.tv_info.system.get("modelName"),
            "webos_version": client.tv_info.software.get("major_ver"),
        }

    async def foreground_app(self) -> str | None:
        """目前在前景的 app id（例如 youtube.leanback.v4）。"""
        client = await self.connect()
        res = await client.request(FOREGROUND_APP)
        return (res or {}).get("appId")

    async def capture_screen(self, path: str | Path | None = None) -> dict[str, Any]:
        """擷取電視目前畫面（960x540 JPEG）並存檔，讓 AI 代理能「看」電視。

        實機 G3 可擷取 YouTube 等串流 app 的畫面；HDMI / 受 DRM 保護的內容可能是黑畫面。
        """
        client = await self.connect()
        res = await client.request(
            SCREEN_CAPTURE, {"path": "/tmp/capture.png", "method": "DISPLAY", "format": "PNG"}
        )
        uri = (res or {}).get("imageUri")
        if not uri:
            return {"ok": False, "error": "電視沒有回傳截圖網址", "response": res}
        if path is None:
            path = Path.cwd() / f"tv-{datetime.now():%Y%m%d-%H%M%S}.jpg"
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        # 截圖由電視的 https://<IP>:3001 提供，憑證是電視自簽，只在區網內連線，故不驗證憑證
        async with aiohttp.ClientSession() as session:
            async with session.get(uri, ssl=False, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                path.write_bytes(await resp.read())
        return {"ok": True, "path": str(path), "bytes": path.stat().st_size}

    async def list_apps(self) -> list[dict[str, str]]:
        client = await self.connect()
        apps = client.tv_state.apps or {a["id"]: a for a in (await client.get_apps() or [])}
        return sorted(({"id": a["id"], "title": a.get("title", "")} for a in apps.values()), key=lambda x: x["title"])

    async def list_inputs(self) -> list[dict[str, str]]:
        client = await self.connect()
        return [
            {"id": i.get("id", ""), "label": i.get("label", ""), "app_id": app_id, "connected": i.get("connected")}
            for app_id, i in client.tv_state.inputs.items()
        ]

    # ------------------------------------------------------------------ App
    async def launch_app(self, name_or_id: str, params: dict | None = None, content_id: str | None = None) -> dict[str, Any]:
        client = await self.ensure_on()
        points = list(client.tv_state.apps.values()) or (await client.get_apps() or [])
        match = resolve_app(name_or_id, points)
        if match is None:
            titles = ", ".join(sorted(p.get("title", "") for p in points)[:40])
            return {"ok": False, "error": f"找不到 app「{name_or_id}」", "installed_apps": titles}
        if content_id:
            res = await client.launch_app_with_content_id(match.app_id, content_id)
        elif params:
            res = await client.launch_app_with_params(match.app_id, params)
        else:
            res = await client.launch_app(match.app_id)
        return {"ok": True, "app_id": match.app_id, "title": match.title, "match": match.source, "response": res}

    async def close_app(self, name_or_id: str) -> dict[str, Any]:
        client = await self.connect()
        match = resolve_app(name_or_id, list(client.tv_state.apps.values()))
        if match is None:
            return {"ok": False, "error": f"找不到 app「{name_or_id}」"}
        await client.close_app(match.app_id)
        return {"ok": True, "app_id": match.app_id}

    # ------------------------------------------------------------------ YouTube
    async def search_youtube(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        results = await youtube.search(
            query,
            api_key=self.config.youtube_api_key,
            max_results=max_results,
            region_code=self.config.region_code,
            relevance_language=self.config.relevance_language,
        )
        return [{"video_id": r.video_id, "title": r.title, "channel": r.channel, "is_live": r.is_live, "url": r.url} for r in results]

    async def play_youtube(self, query_or_url: str, method: str = "auto") -> dict[str, Any]:
        """播放 YouTube：可給影片網址 / 影片 ID / 節目名稱（自動搜尋取第一筆）。

        method: "auto"（SSAP，失敗改 DIAL）| "ssap" | "dial"
        """
        target = youtube.parse_youtube(query_or_url)
        picked: dict[str, Any] | None = None
        if target is None:
            results = await self.search_youtube(query_or_url, max_results=5)
            if not results:
                return {"ok": False, "error": f"YouTube 找不到「{query_or_url}」"}
            picked = results[0]
            target = youtube.YouTubeTarget(video_id=picked["video_id"])

        self._last_youtube = target
        await self.ensure_on()
        errors: list[str] = []
        if method in ("auto", "ssap"):
            try:
                client = await self.connect()
                await client.launch_app_with_params(YOUTUBE_APP_ID, target.launch_params())
                return {"ok": True, "method": "ssap", "tv_url": target.tv_url(), "picked": picked, "note": _PROFILE_NOTE}
            except WebOsTvCommandError as err:
                errors.append(f"ssap: {err}")
                try:
                    await client.launch_app_with_content_id(YOUTUBE_APP_ID, target.tv_url())
                    return {"ok": True, "method": "ssap-contentId", "tv_url": target.tv_url(), "picked": picked}
                except WebOsTvCommandError as err2:
                    errors.append(f"ssap-contentId: {err2}")
        if method in ("auto", "dial") and target.video_id:
            try:
                res = await dial.launch_youtube(self.config.host, target.video_id)
                if res["status"] in (200, 201):
                    return {
                        "ok": True,
                        "method": "dial",
                        "dial": res,
                        "picked": picked,
                        "note": "DIAL 回 200 只代表電視收下請求，請用 capture_screen() 確認是否真的在播。",
                    }
                errors.append(f"dial: HTTP {res['status']}")
            except Exception as err:  # noqa: BLE001 - 回報給代理即可
                errors.append(f"dial: {err}")
        return {"ok": False, "errors": errors, "picked": picked}

    async def youtube_select_profile(self, index: int = 0, replay: bool = True) -> dict[str, Any]:
        """處理 YouTube「誰在觀看」帳號選擇畫面：選第 index 個帳號（0 = 最左邊），再重播上一支影片。

        帳號畫面出現時，啟動參數帶進去的影片會被丟掉，所以選完帳號要重新送一次。
        只在畫面確實停在帳號選擇時呼叫：在播放中按 ENTER 會暫停或叫出控制列。
        """
        if index > 0:
            await self.button("RIGHT", index)
        await self.button("ENTER")
        result: dict[str, Any] = {"ok": True, "profile_index": index}
        if replay and self._last_youtube is not None:
            await asyncio.sleep(3)
            client = await self.connect()
            await client.launch_app_with_params(YOUTUBE_APP_ID, self._last_youtube.launch_params())
            result["replayed"] = self._last_youtube.tv_url()
        return result

    # ------------------------------------------------------------------ 其他串流 / 網頁
    async def play_netflix(self, title_id: str) -> dict[str, Any]:
        """Netflix 深層連結（社群做法，title_id 為 netflix.com/watch/<id> 的數字）。"""
        content_id = f"m=https://www.netflix.com/watch/{title_id}&source_type=4"
        return await self.launch_app(NETFLIX_APP_ID, content_id=content_id)

    async def open_url(self, url: str) -> dict[str, Any]:
        return await self.launch_app(BROWSER_APP_ID, params={"target": url})

    # ------------------------------------------------------------------ 遙控
    async def set_volume(self, level: int) -> dict[str, Any]:
        client = await self.connect()
        level = max(0, min(100, int(level)))
        await client.set_volume(level)
        return {"ok": True, "volume": level}

    async def volume_step(self, up: bool, steps: int = 1) -> dict[str, Any]:
        client = await self.connect()
        for _ in range(max(1, steps)):
            await (client.volume_up() if up else client.volume_down())
        return {"ok": True, "volume": client.tv_state.volume}

    async def mute(self, muted: bool) -> dict[str, Any]:
        client = await self.connect()
        await client.set_mute(muted)
        return {"ok": True, "muted": muted}

    async def media(self, action: str) -> dict[str, Any]:
        client = await self.connect()
        actions = {
            "play": client.play,
            "pause": client.pause,
            "stop": client.stop,
            "rewind": client.rewind,
            "fast_forward": client.fast_forward,
        }
        if action not in actions:
            return {"ok": False, "error": f"不支援的動作 {action}，可用：{', '.join(actions)}"}
        await actions[action]()
        return {"ok": True, "action": action}

    async def button(self, name: str, times: int = 1) -> dict[str, Any]:
        name = name.upper()
        if name not in BUTTONS:
            return {"ok": False, "error": f"未知按鍵 {name}", "available": list(BUTTONS)}
        client = await self.connect()
        for _ in range(max(1, times)):
            await client.button(name)
            await asyncio.sleep(0.15)
        return {"ok": True, "button": name, "times": times}

    async def type_text(self, text: str, enter: bool = False) -> dict[str, Any]:
        """在目前聚焦的輸入框輸入文字（例如 app 內搜尋框）。"""
        client = await self.connect()
        await client.request(INSERT_TEXT, {"text": text, "replace": 0})
        if enter:
            await client.request(SEND_ENTER)
        return {"ok": True}

    async def set_input(self, input_id: str) -> dict[str, Any]:
        client = await self.ensure_on()
        await client.set_input(input_id)
        return {"ok": True, "input": input_id}

    async def notify(self, message: str) -> dict[str, Any]:
        client = await self.connect()
        await client.send_message(message)
        return {"ok": True}
