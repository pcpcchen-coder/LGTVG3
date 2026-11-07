"""命令列工具：`lgtv <指令>`，方便手動測試與給 AIOS 以 shell 方式呼叫。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from . import discover
from .config import TVConfig, default_config_path
from .controller import LGTVController


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


async def _run(args: argparse.Namespace) -> int:
    cfg = TVConfig.load()

    if args.cmd == "discover":
        _print([t.to_dict() for t in await discover.discover(args.timeout)])
        return 0

    if args.cmd == "setup":
        if args.host:
            cfg.host = args.host
        if args.mac:
            cfg.mac = args.mac
        if args.broadcast:
            cfg.broadcast = args.broadcast
        if args.youtube_api_key:
            cfg.youtube_api_key = args.youtube_api_key
        if not cfg.host:
            print("未指定 --host，正在區網上搜尋 LG 電視…")
            found = await discover.discover()
            if len(found) != 1:
                _print([t.to_dict() for t in found])
                print("找到 0 台或多台電視，請用 --host <IP> 指定。" if found else
                      "找不到電視：請確認電視已開機、與本機在同一網段，或用 --host 指定 IP。")
                return 1
            cfg.host = found[0].host
            print(f"找到電視：{found[0].name or found[0].model} @ {cfg.host}")
        path = cfg.save()
        print(f"設定已儲存：{path}")
        if args.pair:
            print("正在連線電視，請在電視畫面上用遙控器按「允許」完成配對（60 秒內）…")
            async with LGTVController(cfg) as tv:
                client = await tv.connect(timeout=60)
                if not cfg.mac:
                    candidates = discover.macs_from_connection_info(client.tv_info.connection)
                    mac = discover.mac_from_arp(cfg.host)
                    if not mac and len(candidates) == 1:
                        mac = next(iter(candidates.values()))
                    if mac:
                        cfg.mac = mac
                        cfg.save()
                        print(f"已自動偵測電視 MAC：{mac}（Wake-on-LAN 用）")
                    elif candidates:
                        print(f"電視網卡 MAC：{candidates}，請用 `lgtv setup --mac <目前連線那張>` 設定")
                _print(await tv.status())
        return 0

    if args.cmd == "search":
        async with LGTVController(cfg) as tv:
            _print(await tv.search_youtube(args.query, args.n))
        return 0

    async with LGTVController(cfg) as tv:
        match args.cmd:
            case "status":
                res = await tv.status()
            case "on":
                res = await tv.power_on()
            case "off":
                res = await tv.power_off()
            case "apps":
                res = await tv.list_apps()
            case "inputs":
                res = await tv.list_inputs()
            case "launch":
                params = json.loads(args.params) if args.params else None
                res = await tv.launch_app(args.app, params=params, content_id=args.content_id)
            case "close":
                res = await tv.close_app(args.app)
            case "youtube":
                res = await tv.play_youtube(args.target, method=args.method)
            case "netflix":
                res = await tv.play_netflix(args.title_id)
            case "url":
                res = await tv.open_url(args.url)
            case "volume":
                if args.level in ("up", "down"):
                    res = await tv.volume_step(args.level == "up", args.steps)
                else:
                    res = await tv.set_volume(int(args.level))
            case "mute":
                res = await tv.mute(args.state == "on")
            case "media":
                res = await tv.media(args.action)
            case "button":
                res = await tv.button(args.name, args.times)
            case "type":
                res = await tv.type_text(args.text, enter=args.enter)
            case "input":
                res = await tv.set_input(args.input_id)
            case "notify":
                res = await tv.notify(args.message)
            case "screen":
                res = await tv.screen(args.state == "on")
            case _:
                raise SystemExit(f"未知指令 {args.cmd}")
        _print(res)
        return 0 if not (isinstance(res, dict) and res.get("ok") is False) else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lgtv", description="LG webOS（G3）電視控制工具")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("discover", help="搜尋區網上的 LG webOS 電視")
    s.add_argument("--timeout", type=float, default=4.0)

    s = sub.add_parser("setup", help=f"儲存設定（{default_config_path()}）並可選擇立即配對")
    s.add_argument("--host", help="電視 IP；省略時自動搜尋區網（建議在路由器設固定 IP）")
    s.add_argument("--mac", help="電視 MAC（Wake-on-LAN 用；有線/無線的 MAC 不同）")
    s.add_argument("--broadcast", help="廣播位址，例如 192.168.1.255")
    s.add_argument("--youtube-api-key", help="YouTube Data API v3 key（依節目名稱搜尋用）")
    s.add_argument("--pair", action="store_true", help="立即連線並在電視上配對")

    sub.add_parser("status", help="電源、目前 app、音量")
    sub.add_parser("on", help="開機（Wake-on-LAN）")
    sub.add_parser("off", help="關機")
    sub.add_parser("apps", help="列出已安裝 app 與 app id")
    sub.add_parser("inputs", help="列出外部輸入（HDMI）")

    s = sub.add_parser("launch", help="依名稱或 app id 開啟 app")
    s.add_argument("app")
    s.add_argument("--params", help='JSON，例如 \'{"contentTarget": "..."}\'')
    s.add_argument("--content-id")

    s = sub.add_parser("close", help="關閉 app")
    s.add_argument("app")

    s = sub.add_parser("youtube", help="播放 YouTube（網址 / 影片 ID / 節目名稱）")
    s.add_argument("target")
    s.add_argument("--method", choices=["auto", "ssap", "dial"], default="auto")

    s = sub.add_parser("search", help="只搜尋 YouTube，不播放")
    s.add_argument("query")
    s.add_argument("-n", type=int, default=5)

    s = sub.add_parser("netflix", help="播放 Netflix 影片（watch/<id>）")
    s.add_argument("title_id")

    s = sub.add_parser("url", help="用電視瀏覽器開網址")
    s.add_argument("url")

    s = sub.add_parser("volume", help="音量：數字 / up / down")
    s.add_argument("level")
    s.add_argument("--steps", type=int, default=1)

    s = sub.add_parser("mute")
    s.add_argument("state", choices=["on", "off"])

    s = sub.add_parser("media", help="播放控制")
    s.add_argument("action", choices=["play", "pause", "stop", "rewind", "fast_forward"])

    s = sub.add_parser("button", help="模擬遙控器按鍵，例如 HOME / BACK / ENTER / UP")
    s.add_argument("name")
    s.add_argument("--times", type=int, default=1)

    s = sub.add_parser("type", help="在目前輸入框打字")
    s.add_argument("text")
    s.add_argument("--enter", action="store_true")

    s = sub.add_parser("input", help="切換輸入源，例如 HDMI_1")
    s.add_argument("input_id")

    s = sub.add_parser("notify", help="在電視上顯示通知")
    s.add_argument("message")

    s = sub.add_parser("screen", help="關閉/開啟螢幕（聲音持續）")
    s.add_argument("state", choices=["on", "off"])
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        return asyncio.run(_run(args))
    except Exception as err:  # noqa: BLE001
        _print({"ok": False, "error": f"{type(err).__name__}: {err}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
