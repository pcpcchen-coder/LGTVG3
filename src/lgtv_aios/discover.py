"""在區網上找 LG webOS 電視（SSDP），以及查詢電視 MAC 位址（Wake-on-LAN 用）。"""

from __future__ import annotations

import asyncio
import re
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

import aiohttp

SSDP_ADDR = ("239.255.255.250", 1900)
# LG 手機 App 用來找電視的服務類型；DIAL 作為補充（部分韌體只回應其一）
SEARCH_TARGETS = (
    "urn:lge-com:service:webos-second-screen:1",
    "urn:dial-multiscreen-org:service:dial:1",
)
_MAC_RE = re.compile(r"([0-9a-fA-F]{1,2}(?:[:-][0-9a-fA-F]{1,2}){5})")


@dataclass
class FoundTV:
    host: str
    name: str = ""
    model: str = ""
    manufacturer: str = ""
    server: str = ""
    location: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_headers(data: bytes) -> dict[str, str]:
    headers = {}
    for line in data.decode(errors="ignore").split("\r\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers


def _ssdp_search(timeout: float) -> dict[str, dict[str, str]]:
    """回傳 {ip: headers}。"""
    found: dict[str, dict[str, str]] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.5)
        for st in SEARCH_TARGETS:
            msg = (
                "M-SEARCH * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 2\r\n"
                f"ST: {st}\r\n\r\n"
            ).encode()
            for _ in range(2):
                sock.sendto(msg, SSDP_ADDR)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, (addr, _) = sock.recvfrom(4096)
            except (TimeoutError, socket.timeout):
                continue
            headers = _parse_headers(data)
            st = headers.get("st", "")
            server = headers.get("server", "")
            if st in SEARCH_TARGETS or "webos" in server.lower():
                found.setdefault(addr, headers)
    return found


def _parse_description(xml_text: str) -> dict[str, str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    out = {}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("friendlyName", "modelName", "manufacturer") and el.text and tag not in out:
            out[tag] = el.text.strip()
    return out


async def discover(timeout: float = 4.0) -> list[FoundTV]:
    """搜尋區網上的 LG webOS 電視。"""
    raw = await asyncio.to_thread(_ssdp_search, timeout)
    tvs: list[FoundTV] = []
    async with aiohttp.ClientSession() as session:
        for host, headers in raw.items():
            tv = FoundTV(host=host, server=headers.get("server", ""), location=headers.get("location", ""))
            if tv.location:
                try:
                    async with session.get(tv.location, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        desc = _parse_description(await resp.text())
                    tv.name = desc.get("friendlyName", "")
                    tv.model = desc.get("modelName", "")
                    tv.manufacturer = desc.get("manufacturer", "")
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
            is_lg = "lg" in tv.manufacturer.lower() or "webos" in tv.server.lower()
            if is_lg or not tv.manufacturer:
                tvs.append(tv)
    return sorted(tvs, key=lambda t: t.host)


def parse_arp_output(text: str, host: str) -> str | None:
    for line in text.splitlines():
        if re.search(rf"(?<![\d.]){re.escape(host)}(?![\d.])", line):
            m = _MAC_RE.search(line)
            if m:
                return ":".join(p.zfill(2) for p in re.split("[:-]", m.group(1))).upper()
    return None


def mac_from_arp(host: str) -> str | None:
    """從本機 ARP 表查電視「目前使用中網卡」的 MAC（需先與電視通訊過）。"""
    try:
        with open("/proc/net/arp", encoding="utf-8") as f:  # Linux
            mac = parse_arp_output(f.read(), host)
            if mac and mac != "00:00:00:00:00:00":
                return mac
    except OSError:
        pass
    try:  # macOS / BSD / Windows
        out = subprocess.run(["arp", "-n", host], capture_output=True, text=True, timeout=3).stdout
        if not out.strip():
            out = subprocess.run(["arp", "-a", host], capture_output=True, text=True, timeout=3).stdout
        return parse_arp_output(out, host)
    except (OSError, subprocess.SubprocessError):
        return None


def macs_from_connection_info(info: dict) -> dict[str, str]:
    """從 SSAP connectionmanager/getinfo 回應取出各網卡 MAC。"""
    out = {}
    for key, label in (("wiredInfo", "wired"), ("wifiInfo", "wifi")):
        mac = (info.get(key) or {}).get("macAddress")
        if mac:
            out[label] = mac.upper()
    return out
