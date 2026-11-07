"""在區網上找 LG webOS 電視，以及查詢電視 MAC 位址（Wake-on-LAN 用）。

搜尋順序：
1. SSDP（LG second-screen / DIAL）
2. SSDP 沒有回應時（有些路由器或 mesh 網路會擋多播）：掃描本機所在 /24 網段的
   SSAP 埠 3001，再用 DIAL 讀 brand / model 確認是 LG 電視。實機環境就是靠這條找到 G3。
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

import aiohttp

from . import dial

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


def local_ipv4() -> str | None:
    """本機對外網卡的 IPv4（不會真的送出封包）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET，只用來讓系統選路由
            return s.getsockname()[0]
        except OSError:
            return None


async def _port_open(host: str, port: int, timeout: float) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def scan_subnet(
    subnet: str | None = None, port: int = 3001, timeout: float = 0.6, concurrency: int = 128
) -> list[FoundTV]:
    """掃描網段上開著 SSAP 埠的主機，並用 DIAL 確認是 LG 電視。

    subnet 例如 "192.168.1.0/24"；省略時用本機 IP 所在的 /24。
    """
    if subnet is None:
        ip = local_ipv4()
        if not ip:
            return []
        subnet = str(ipaddress.ip_network(f"{ip}/24", strict=False))
    hosts = [str(h) for h in ipaddress.ip_network(subnet, strict=False).hosts()]
    sem = asyncio.Semaphore(concurrency)

    async def probe(host: str) -> FoundTV | None:
        async with sem:
            if not await _port_open(host, port, timeout):
                return None
        info = await dial.youtube_app_info(host) or {}
        brand = info.get("brand", "")
        if brand and "lg" not in brand.lower():
            return None
        return FoundTV(host=host, model=info.get("model", ""), manufacturer=brand, server="scan:ssap")

    found = await asyncio.gather(*(probe(h) for h in hosts))
    return [tv for tv in found if tv is not None]


async def discover(timeout: float = 4.0, scan_fallback: bool = True) -> list[FoundTV]:
    """搜尋區網上的 LG webOS 電視；SSDP 沒結果時改掃描網段。"""
    tvs = await _discover_ssdp(timeout)
    if not tvs and scan_fallback:
        tvs = await scan_subnet()
    return sorted(tvs, key=lambda t: tuple(int(x) for x in t.host.split(".")))


async def _discover_ssdp(timeout: float) -> list[FoundTV]:
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
    return tvs


def parse_arp_output(text: str, host: str) -> str | None:
    for line in text.splitlines():
        if re.search(rf"(?<![\d.]){re.escape(host)}(?![\d.])", line):
            m = _MAC_RE.search(line)
            if m:
                return ":".join(p.zfill(2) for p in re.split("[:-]", m.group(1))).upper()
    return None


def mac_from_arp(host: str) -> str | None:
    """從本機 ARP 表查電視「目前使用中網卡」的 MAC。

    ARP 紀錄閒置幾分鐘就會過期，所以先連一下電視的 SSAP 埠讓系統重新記錄。
    """
    try:
        with socket.create_connection((host, 3001), timeout=1):
            pass
    except OSError:
        pass
    try:
        with open("/proc/net/arp", encoding="utf-8") as f:  # Linux
            mac = parse_arp_output(f.read(), host)
            if mac and mac != "00:00:00:00:00:00":
                return mac
    except OSError:
        pass
    # macOS / BSD / Windows。macOS 有多張網卡時，ARP 紀錄會綁定網卡（ifscope），
    # `arp -n <IP>` 回「no entry」，只有列整張表 `arp -an` 才看得到，所以逐一嘗試。
    for cmd in (["arp", "-n", host], ["arp", "-a", host], ["arp", "-an"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        mac = parse_arp_output(out, host)
        if mac:
            return mac
    return None


def macs_from_connection_info(info: dict) -> dict[str, str]:
    """從 SSAP connectionmanager/getinfo 回應取出各網卡 MAC。"""
    out = {}
    for key, label in (("wiredInfo", "wired"), ("wifiInfo", "wifi")):
        mac = (info.get(key) or {}).get("macAddress")
        if mac:
            out[label] = mac.upper()
    return out
