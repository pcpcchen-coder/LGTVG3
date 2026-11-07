"""Wake-on-LAN：電視待機時 WebSocket 不會回應，只能用 magic packet 喚醒。

G3 需在電視上開啟：設定 → 一般 → 裝置 → 電視管理（或「外部裝置」）→
「透過 Wi‑Fi 開機 / 行動裝置開啟電視（Turn on via Wi‑Fi）」。
"""

from __future__ import annotations

import asyncio
import re
import socket


def parse_mac(mac: str) -> bytes:
    hexstr = re.sub(r"[^0-9A-Fa-f]", "", mac)
    if len(hexstr) != 12:
        raise ValueError(f"MAC 位址格式錯誤：{mac!r}")
    return bytes.fromhex(hexstr)


def magic_packet(mac: str) -> bytes:
    return b"\xff" * 6 + parse_mac(mac) * 16


async def send_magic_packet(
    mac: str, broadcast: str = "", port: int = 9, repeat: int = 3, interval: float = 0.3
) -> None:
    """送出多次 magic packet（社群經驗：連送幾次比較容易喚醒 LG）。"""
    packet = magic_packet(mac)
    targets = {broadcast or "255.255.255.255", "255.255.255.255"}
    for i in range(repeat):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for addr in targets:
                for p in {port, 7}:
                    sock.sendto(packet, (addr, p))
        if i < repeat - 1:
            await asyncio.sleep(interval)
