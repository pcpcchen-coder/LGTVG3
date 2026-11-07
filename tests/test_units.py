import pytest

from lgtv_aios.apps import resolve_app
from lgtv_aios.wol import magic_packet, parse_mac
from lgtv_aios.youtube import YouTubeTarget, parse_youtube

POINTS = [
    {"id": "youtube.leanback.v4", "title": "YouTube"},
    {"id": "netflix", "title": "Netflix"},
    {"id": "com.linecorp.linetv", "title": "LINE TV"},
    {"id": "kktv.tw", "title": "KKTV"},
]


@pytest.mark.parametrize(
    "text,vid,lst,t",
    [
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ", None, None),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1m30s", "dQw4w9WgXcQ", None, 90),
        ("https://youtu.be/dQw4w9WgXcQ?t=42", "dQw4w9WgXcQ", None, 42),
        ("youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ", None, None),
        ("https://m.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ", None, None),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123", "dQw4w9WgXcQ", "PL123", None),
        ("https://www.youtube.com/playlist?list=PL123", None, "PL123", None),
    ],
)
def test_parse_youtube(text, vid, lst, t):
    target = parse_youtube(text)
    assert target == YouTubeTarget(video_id=vid, playlist_id=lst, start_seconds=t)


@pytest.mark.parametrize("text", ["孤獨的美食家 第十季", "https://example.com/watch?v=dQw4w9WgXcQ", "hello"])
def test_parse_youtube_rejects_non_youtube(text):
    assert parse_youtube(text) is None


def test_tv_url_and_params():
    t = YouTubeTarget(video_id="dQw4w9WgXcQ", start_seconds=90)
    assert t.tv_url() == "https://www.youtube.com/tv?v=dQw4w9WgXcQ&t=90"
    assert t.launch_params() == {"contentTarget": "https://www.youtube.com/tv?v=dQw4w9WgXcQ&t=90"}


@pytest.mark.parametrize(
    "query,expected,source",
    [
        ("netflix", "netflix", "exact-id"),
        ("YouTube", "youtube.leanback.v4", "title"),
        ("yt", "youtube.leanback.v4", "alias"),
        ("line tv", "com.linecorp.linetv", "title"),
        ("linetv", "com.linecorp.linetv", "fuzzy"),
        ("網飛", "netflix", "alias"),
    ],
)
def test_resolve_app(query, expected, source):
    m = resolve_app(query, POINTS)
    assert m is not None
    assert (m.app_id, m.source) == (expected, source)


def test_resolve_app_unknown():
    assert resolve_app("完全不存在的 app", POINTS) is None


def test_magic_packet():
    pkt = magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(pkt) == 102
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:12] == bytes.fromhex("aabbccddeeff")
    assert parse_mac("aa-bb-cc-dd-ee-ff") == parse_mac("aabb.ccdd.eeff")
    with pytest.raises(ValueError):
        parse_mac("12:34")
