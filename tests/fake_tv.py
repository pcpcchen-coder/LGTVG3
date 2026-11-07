"""模擬 LG webOS SSAP WebSocket 伺服器（ws://127.0.0.1:3000），用來在沒有實機時做端到端測試。

只實作 aiowebostv 連線流程會用到的訊息：hello → getSystemInfo → register →
getPointerInputSocket → 各種 subscribe，並記錄收到的 launch / 輸入指令。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from aiohttp import WSMsgType, web

LAUNCH_POINTS = [
    {"id": "youtube.leanback.v4", "title": "YouTube"},
    {"id": "netflix", "title": "Netflix"},
    {"id": "com.disney.disneyplus-prod", "title": "Disney+"},
    {"id": "com.linecorp.linetv", "title": "LINE TV"},
    {"id": "com.webos.app.browser", "title": "網頁瀏覽器"},
    {"id": "com.webos.app.livetv", "title": "Live TV"},
]


@dataclass
class FakeTV:
    client_key: str = "FAKE-CLIENT-KEY"
    power_state: str = "Active"
    current_app: str = "com.webos.app.home"
    volume: int = 12
    reject_launch_params: bool = False
    requests: list[dict] = field(default_factory=list)
    input_msgs: list[str] = field(default_factory=list)
    port: int = 3000

    def payload_for(self, uri: str, payload: dict) -> tuple[str, dict]:
        """回傳 (type, payload)。"""
        ep = uri.removeprefix("ssap://")
        if ep == "system/getSystemInfo":
            return "response", {"returnValue": True, "modelName": "OLED65G3PTA"}
        if ep == "com.webos.service.update/getCurrentSWInformation":
            return "response", {"returnValue": True, "major_ver": "23", "product_name": "webOSTV 23"}
        if ep == "com.webos.service.networkinput/getPointerInputSocket":
            return "response", {"returnValue": True, "socketPath": f"ws://127.0.0.1:{self.port}/input"}
        if ep == "com.webos.service.tvpower/power/getPowerState":
            return "response", {"returnValue": True, "state": self.power_state}
        if ep == "com.webos.applicationManager/getForegroundAppInfo":
            return "response", {"returnValue": True, "appId": self.current_app}
        if ep == "audio/getStatus":
            return "response", {"returnValue": True, "mute": False}
        if ep == "audio/getVolume":
            return "response", {"returnValue": True, "volumeStatus": {"volume": self.volume}}
        if ep == "com.webos.applicationManager/listLaunchPoints":
            return "response", {"returnValue": True, "launchPoints": LAUNCH_POINTS}
        if ep == "tv/getExternalInputList":
            return "response", {"returnValue": True, "devices": [{"id": "HDMI_1", "label": "HDMI 1", "appId": "com.webos.app.hdmi1", "connected": True}]}
        if ep == "com.webos.service.apiadapter/audio/getSoundOutput":
            return "response", {"returnValue": True, "soundOutput": "tv_speaker"}
        if ep == "com.webos.media/getForegroundAppInfo":
            return "response", {"returnValue": True, "foregroundAppInfo": []}
        if ep == "com.webos.service.connectionmanager/getinfo":
            return "response", {"returnValue": True, "wifiInfo": {"macAddress": "a8:23:fe:12:34:56"}}
        if ep == "tv/getChannelList":
            return "error", {}
        if ep == "system.launcher/launch":
            if self.reject_launch_params and "params" in payload:
                return "response", {"returnValue": False, "errorText": "rejected"}
            self.current_app = payload["id"]
            return "response", {"returnValue": True, "id": payload["id"], "sessionId": "s1"}
        if ep == "audio/setVolume":
            self.volume = payload["volume"]
        return "response", {"returnValue": True}

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if request.path == "/input":
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    self.input_msgs.append(msg.data)
            return ws
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                break
            m = json.loads(msg.data)
            mtype, mid = m.get("type"), m.get("id")
            if mtype == "hello":
                await ws.send_json({"type": "hello", "id": mid, "payload": {"protocolVersion": 1, "deviceOS": "webOS"}})
                continue
            if mtype == "register":
                if m["payload"].get("client-key") != self.client_key:
                    await ws.send_json({"type": "response", "id": mid, "payload": {"pairingType": "PROMPT", "returnValue": True}})
                await ws.send_json({"type": "registered", "id": mid, "payload": {"client-key": self.client_key}})
                continue
            if mtype == "unsubscribe":
                continue
            self.requests.append(m)
            rtype, payload = self.payload_for(m.get("uri", ""), m.get("payload") or {})
            if rtype == "error":
                await ws.send_json({"type": "error", "id": mid, "error": "404 no such service or method", "payload": {}})
            else:
                await ws.send_json({"type": "response", "id": mid, "payload": payload})
        return ws

    def launches(self) -> list[dict]:
        return [r["payload"] for r in self.requests if r.get("uri") == "ssap://system.launcher/launch"]


async def start_fake_tv(tv: FakeTV) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", tv.handle)
    app.router.add_get("/input", tv.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", tv.port)
    await site.start()
    return runner
