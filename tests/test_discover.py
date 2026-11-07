import json

from lgtv_aios import discover

MAC_ARP = "? (192.168.1.50) at a8:23:fe:1:2:3 on en0 ifscope [ethernet]\n"
LINUX_ARP = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    "192.168.1.5      0x1         0x2         11:22:33:44:55:66     *        eth0\n"
    "192.168.1.50     0x1         0x2         a8:23:fe:01:02:03     *        eth0\n"
)
WIN_ARP = "  192.168.1.50          a8-23-fe-01-02-03     dynamic\n"


def test_parse_arp_formats():
    for text in (MAC_ARP, LINUX_ARP, WIN_ARP):
        assert discover.parse_arp_output(text, "192.168.1.50") == "A8:23:FE:01:02:03"
    # 不可把 192.168.1.5 誤判為 192.168.1.50
    assert discover.parse_arp_output(LINUX_ARP, "192.168.1.5") == "11:22:33:44:55:66"
    assert discover.parse_arp_output(LINUX_ARP, "192.168.1.9") is None


def test_parse_description():
    xml = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>
      <friendlyName>[LG] webOS TV OLED65G3PTA</friendlyName><manufacturer>LG Electronics</manufacturer>
      <modelName>OLED65G3PTA</modelName></device></root>"""
    assert discover._parse_description(xml) == {
        "friendlyName": "[LG] webOS TV OLED65G3PTA",
        "manufacturer": "LG Electronics",
        "modelName": "OLED65G3PTA",
    }
    assert discover._parse_description("not xml") == {}


def test_macs_from_connection_info():
    info = {"wiredInfo": {"macAddress": "a8:23:fe:00:00:01"}, "wifiInfo": {"macAddress": "a8:23:fe:00:00:02"}}
    assert discover.macs_from_connection_info(info) == {"wired": "A8:23:FE:00:00:01", "wifi": "A8:23:FE:00:00:02"}
    assert discover.macs_from_connection_info({}) == {}
