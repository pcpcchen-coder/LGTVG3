"""設定檔：電視 IP、MAC、配對金鑰（client-key）等。

預設路徑：$LGTV_AIOS_CONFIG 或 ~/.config/lgtv-aios/lgtv-aios.json
環境變數 LGTV_HOST / LGTV_MAC / LGTV_CLIENT_KEY / YOUTUBE_API_KEY 會覆蓋檔案內容。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def default_config_path() -> Path:
    env = os.environ.get("LGTV_AIOS_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path("~/.config/lgtv-aios/lgtv-aios.json").expanduser()


@dataclass
class TVConfig:
    host: str = ""
    mac: str = ""
    client_key: str | None = None
    # 用於 Wake-on-LAN 的廣播位址，例如 192.168.1.255；空字串時用 255.255.255.255
    broadcast: str = ""
    youtube_api_key: str | None = None
    # YouTube 搜尋偏好
    region_code: str = "TW"
    relevance_language: str = "zh-Hant"
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "TVConfig":
        path = path or default_config_path()
        data: dict = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        cfg = cls(**known)
        cfg.host = os.environ.get("LGTV_HOST", cfg.host)
        cfg.mac = os.environ.get("LGTV_MAC", cfg.mac)
        cfg.client_key = os.environ.get("LGTV_CLIENT_KEY", cfg.client_key)
        cfg.youtube_api_key = os.environ.get("YOUTUBE_API_KEY", cfg.youtube_api_key)
        return cfg

    def save(self, path: Path | None = None) -> Path:
        path = path or default_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)  # client-key 等同遙控器權限，限制讀取
        except OSError:
            pass
        return path
