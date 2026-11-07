"""實機（OLED65G3PSA）驗證時發現的行為，用模擬環境固定下來。"""

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import web

from fake_tv import FAKE_JPEG, FakeTV, start_fake_tv
from lgtv_aios import dial, discover, youtube
from lgtv_aios.config import TVConfig
from lgtv_aios.controller import LGTVController
from test_fake_tv import cfg, run  # noqa: F401  (fixture)

DIAL_XML = (
    '<?xml version="1.0" encoding="UTF-8"?><service xmlns="urn:dial-multiscreen-org:schemas:dial" dialVer="2.2">'
    "<name>YouTube</name><state>running</state><additionalData><brand>LG</brand>"
    "<model>OLED65G3PSA</model></additionalData></service>"
)


async def _start_dial(posts: list) -> tuple[web.AppRunner, str]:
    """模擬 LG 的 DIAL server：沒有 YouTube Origin 一律 403（實機行為）。"""

    async def youtube_app(request: web.Request) -> web.Response:
        if request.headers.get("Origin") != "https://www.youtube.com":
            return web.Response(status=403)
        if request.method == "POST":
            posts.append(await request.text())
            return web.Response(status=200, headers={"Location": f"{request.url}/run"})
        return web.Response(text=DIAL_XML, content_type="text/xml")

    app = web.Application()
    app.router.add_route("*", "/apps/YouTube", youtube_app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/apps"


def test_dial_sends_youtube_origin():
    async def main():
        posts: list = []
        runner, app_url = await _start_dial(posts)
        try:
            res = await dial.launch_youtube("127.0.0.1", "m_dhMSvUCIc", application_url=app_url)
            info = await dial.youtube_app_info("127.0.0.1", application_url=app_url)
        finally:
            await runner.cleanup()
        return posts, res, info

    posts, res, info = run(main())
    assert res["status"] == 200 and posts == ["v=m_dhMSvUCIc"]
    assert info == {"name": "YouTube", "state": "running", "brand": "LG", "model": "OLED65G3PSA"}


def test_parse_results_page_keeps_youtube_order():
    data = {
        "contents": [
            {"videoRenderer": {"videoId": "m_dhMSvUCIc", "title": {"runs": [{"text": "🔴LIVE：TVBS NEWS"}]},
                               "ownerText": {"runs": [{"text": "TVBS NEWS"}]},
                               "badges": [{"metadataBadgeRenderer": {"label": "LIVE"}}]}},
            {"channelRenderer": {"channelId": "x"}},
            {"videoRenderer": {"videoId": "7aattNnxTEE", "title": {"runs": [{"text": "晚間整點新聞"}]}}},
        ]
    }
    html = f"<script>var ytInitialData = {json.dumps(data, ensure_ascii=False)};</script>"
    res = youtube.parse_results_page(html, 5)
    assert [r.video_id for r in res] == ["m_dhMSvUCIc", "7aattNnxTEE"]
    assert res[0].is_live and res[0].channel == "TVBS NEWS" and not res[1].is_live
    assert youtube.parse_results_page("<html></html>") == []


def test_capture_screen_downloads_jpeg(cfg, tmp_path):  # noqa: F811
    async def main():
        tv = FakeTV()
        runner = await start_fake_tv(tv)
        try:
            async with LGTVController(cfg) as ctl:
                return await ctl.capture_screen(tmp_path / "shot.jpg"), await ctl.foreground_app()
        finally:
            await runner.cleanup()

    res, app = run(main())
    assert res["ok"] and Path(res["path"]).read_bytes() == FAKE_JPEG
    assert app == "com.webos.app.home"


def test_select_profile_replays_last_video(cfg):  # noqa: F811
    async def main():
        tv = FakeTV()
        runner = await start_fake_tv(tv)
        try:
            async with LGTVController(cfg) as ctl:
                await ctl.play_youtube("m_dhMSvUCIc", method="ssap")
                res = await ctl.youtube_select_profile(index=1)
                await asyncio.sleep(0.2)
        finally:
            await runner.cleanup()
        return tv, res

    tv, res = run(main())
    presses = [m for m in tv.input_msgs if m.startswith("type:button")]
    assert presses == ["type:button\nname:RIGHT\n\n", "type:button\nname:ENTER\n\n"]
    assert len(tv.launches()) == 2 and res["replayed"].endswith("v=m_dhMSvUCIc")


def test_scan_subnet_identifies_lg_by_dial(monkeypatch):
    async def fake_open(host, port, timeout):
        return host == "10.0.0.7"

    async def fake_info(host, application_url=None, timeout=3.0):
        return {"brand": "LG", "model": "OLED65G3PSA"}

    monkeypatch.setattr(discover, "_port_open", fake_open)
    monkeypatch.setattr(dial, "youtube_app_info", fake_info)
    found = run(discover.scan_subnet("10.0.0.0/28"))
    assert [(t.host, t.model) for t in found] == [("10.0.0.7", "OLED65G3PSA")]
