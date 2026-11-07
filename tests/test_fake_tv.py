"""端到端：真正的 aiowebostv client ↔ 模擬電視（ws://127.0.0.1:3000）。"""

import asyncio
import socket

import pytest

from fake_tv import FakeTV, start_fake_tv
from lgtv_aios import youtube
from lgtv_aios.config import TVConfig
from lgtv_aios.controller import LGTVController


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


pytestmark = pytest.mark.skipif(not _port_free(3000), reason="port 3000 被占用")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("LGTV_AIOS_CONFIG", str(tmp_path / "cfg.json"))
    for k in ("LGTV_HOST", "LGTV_MAC", "LGTV_CLIENT_KEY", "YOUTUBE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return TVConfig(host="127.0.0.1")


def run(coro):
    return asyncio.run(coro)


def test_pairing_saves_client_key(cfg):
    async def main():
        tv = FakeTV()
        runner = await start_fake_tv(tv)
        try:
            async with LGTVController(cfg) as ctl:
                status = await ctl.status()
        finally:
            await runner.cleanup()
        return status

    status = run(main())
    assert status["is_on"] and status["model"] == "OLED65G3PTA" and status["volume"] == 12
    assert TVConfig.load().client_key == "FAKE-CLIENT-KEY"


def test_play_youtube_url_uses_content_target(cfg):
    async def main():
        tv = FakeTV()
        runner = await start_fake_tv(tv)
        try:
            async with LGTVController(cfg) as ctl:
                res = await ctl.play_youtube("https://youtu.be/dQw4w9WgXcQ")
        finally:
            await runner.cleanup()
        return tv, res

    tv, res = run(main())
    assert res["ok"] and res["method"] == "ssap"
    assert tv.launches()[-1] == {
        "id": "youtube.leanback.v4",
        "params": {"contentTarget": "https://www.youtube.com/tv?v=dQw4w9WgXcQ"},
    }


def test_play_youtube_by_name_searches_first(cfg, monkeypatch):
    async def fake_search(query, **kwargs):
        assert query == "孤獨的美食家"
        return [youtube.VideoResult("abcdefghijk", "孤獨的美食家 EP1", "某頻道")]

    monkeypatch.setattr(youtube, "search", fake_search)

    async def main():
        tv = FakeTV()
        runner = await start_fake_tv(tv)
        try:
            async with LGTVController(cfg) as ctl:
                res = await ctl.play_youtube("孤獨的美食家")
        finally:
            await runner.cleanup()
        return tv, res

    tv, res = run(main())
    assert res["ok"] and res["picked"]["video_id"] == "abcdefghijk"
    assert tv.launches()[-1]["params"]["contentTarget"].endswith("v=abcdefghijk")


def test_play_youtube_falls_back_to_content_id(cfg):
    async def main():
        tv = FakeTV(reject_launch_params=True)
        runner = await start_fake_tv(tv)
        try:
            async with LGTVController(cfg) as ctl:
                res = await ctl.play_youtube("dQw4w9WgXcQ", method="ssap")
        finally:
            await runner.cleanup()
        return tv, res

    tv, res = run(main())
    assert res["method"] == "ssap-contentId"
    assert tv.launches()[-1] == {"id": "youtube.leanback.v4", "contentId": "https://www.youtube.com/tv?v=dQw4w9WgXcQ"}


def test_launch_other_apps_and_remote(cfg):
    async def main():
        tv = FakeTV()
        runner = await start_fake_tv(tv)
        try:
            async with LGTVController(cfg) as ctl:
                r1 = await ctl.launch_app("LINE TV")
                r2 = await ctl.launch_app("disney plus")
                r3 = await ctl.launch_app("不存在")
                await ctl.set_volume(30)
                await ctl.button("home")
                await asyncio.sleep(0.2)
                apps = await ctl.list_apps()
        finally:
            await runner.cleanup()
        return tv, r1, r2, r3, apps

    tv, r1, r2, r3, apps = run(main())
    assert r1["app_id"] == "com.linecorp.linetv"
    assert r2["app_id"] == "com.disney.disneyplus-prod"
    assert r3["ok"] is False
    assert tv.volume == 30
    assert any("name:HOME" in m for m in tv.input_msgs)
    assert {"id": "netflix", "title": "Netflix"} in apps


def test_power_off_when_unreachable(cfg):
    async def main():
        async with LGTVController(cfg) as ctl:
            return await ctl.power_off()

    assert run(main()) == {"ok": True, "already_off": True}
