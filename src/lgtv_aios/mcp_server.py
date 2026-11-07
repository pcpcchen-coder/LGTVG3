"""MCP server：讓支援 Model Context Protocol 的 AI 代理（AIOS、Claude 等）直接呼叫電視工具。

啟動：`lgtv-mcp`（stdio）或 `lgtv-mcp --http`（streamable-http）
需要：pip install "lgtv-aios[mcp]"
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer

from .config import TVConfig
from .controller import LGTVController

server = MCPServer(
    name="lg-webos-tv",
    instructions=(
        "控制客廳的 LG G3 OLED 電視（webOS 23）。"
        "要播放 YouTube 節目時直接呼叫 tv_play_youtube，傳入網址或節目名稱即可，工具會自動開機。"
        "不確定是哪支影片時，先用 tv_search_youtube 列出候選給使用者選。"
        "開其他 app 前若不確定名稱，可先呼叫 tv_list_apps。"
        "播放後用 tv_screenshot 看電視畫面確認；若停在 YouTube「誰在觀看」帳號畫面，"
        "呼叫 tv_youtube_select_profile 選帳號，它會自動重播剛才的影片。"
    ),
)

_tv: LGTVController | None = None


def _controller() -> LGTVController:
    global _tv
    if _tv is None:
        _tv = LGTVController(TVConfig.load())
    return _tv


@server.tool()
async def tv_status() -> dict[str, Any]:
    """取得電視狀態：是否開機、目前 app、音量、靜音。"""
    return await _controller().status()


@server.tool()
async def tv_power(on: bool) -> dict[str, Any]:
    """開機（on=true，使用 Wake-on-LAN）或關機（on=false）。"""
    tv = _controller()
    return await (tv.power_on() if on else tv.power_off())


@server.tool()
async def tv_list_apps() -> list[dict[str, str]]:
    """列出電視上已安裝的 app（id 與名稱）。"""
    return await _controller().list_apps()


@server.tool()
async def tv_launch_app(app: str) -> dict[str, Any]:
    """開啟 app。app 可為名稱（如 'Netflix'、'Disney+'、'LINE TV'）或 app id。電視關機時會先開機。"""
    return await _controller().launch_app(app)


@server.tool()
async def tv_search_youtube(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """搜尋 YouTube 影片（不播放），回傳 video_id、標題、頻道。"""
    return await _controller().search_youtube(query, max_results)


@server.tool()
async def tv_play_youtube(query_or_url: str) -> dict[str, Any]:
    """在電視的 YouTube app 播放影片。可傳 YouTube 網址、11 碼影片 ID，或節目名稱（自動搜尋取第一筆）。"""
    return await _controller().play_youtube(query_or_url)


@server.tool()
async def tv_screenshot() -> Image:
    """擷取電視目前畫面（960x540 JPEG），用來確認是否真的在播放、或畫面停在哪裡。"""
    import tempfile

    path = Path(tempfile.gettempdir()) / "lgtv-aios-screenshot.jpg"
    res = await _controller().capture_screen(path)
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "截圖失敗"))
    return Image(data=path.read_bytes(), format="jpeg")


@server.tool()
async def tv_youtube_select_profile(index: int = 0) -> dict[str, Any]:
    """YouTube 停在「誰在觀看」帳號畫面時使用：選第 index 個帳號（0 = 最左邊），並重播上一次要播的影片。

    只在截圖確認畫面是帳號選擇時呼叫；播放中呼叫會讓影片暫停。
    """
    return await _controller().youtube_select_profile(index)


@server.tool()
async def tv_play_netflix(title_id: str) -> dict[str, Any]:
    """在 Netflix 播放指定作品。title_id 為 netflix.com/watch/<id> 中的數字。"""
    return await _controller().play_netflix(title_id)


@server.tool()
async def tv_open_url(url: str) -> dict[str, Any]:
    """用電視內建瀏覽器開啟網址。"""
    return await _controller().open_url(url)


@server.tool()
async def tv_volume(level: int | None = None, step: int = 0, mute: bool | None = None) -> dict[str, Any]:
    """調整音量：level=0~100 直接設定；step=+N/-N 逐格調整；mute=true/false 靜音。"""
    tv = _controller()
    if mute is not None:
        return await tv.mute(mute)
    if level is not None:
        return await tv.set_volume(level)
    if step:
        return await tv.volume_step(step > 0, abs(step))
    return await tv.status()


@server.tool()
async def tv_media(action: str) -> dict[str, Any]:
    """播放控制：play / pause / stop / rewind / fast_forward。"""
    return await _controller().media(action)


@server.tool()
async def tv_button(name: str, times: int = 1) -> dict[str, Any]:
    """模擬遙控器按鍵：HOME、BACK、ENTER、UP、DOWN、LEFT、RIGHT、EXIT、MUTE、數字鍵等。"""
    return await _controller().button(name, times)


@server.tool()
async def tv_type_text(text: str, enter: bool = False) -> dict[str, Any]:
    """在電視目前聚焦的輸入框輸入文字（如 app 內搜尋框），enter=true 會送出。"""
    return await _controller().type_text(text, enter)


@server.tool()
async def tv_set_input(input_id: str) -> dict[str, Any]:
    """切換輸入源，例如 HDMI_1、HDMI_2。"""
    return await _controller().set_input(input_id)


@server.tool()
async def tv_notify(message: str) -> dict[str, Any]:
    """在電視畫面右下角顯示一則通知。"""
    return await _controller().notify(message)


def main() -> None:
    ap = argparse.ArgumentParser(prog="lgtv-mcp")
    ap.add_argument("--http", action="store_true", help="改用 streamable-http transport")
    args = ap.parse_args()
    server.run("streamable-http" if args.http else "stdio")


if __name__ == "__main__":
    main()
