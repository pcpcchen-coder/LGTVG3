"""lgtv_aios：讓 AI 代理控制 LG webOS 電視（以 LG OLED G3 / webOS 23 為目標）。"""

from .config import TVConfig
from .controller import LGTVController, TVNotReachable

__all__ = ["LGTVController", "TVConfig", "TVNotReachable"]
__version__ = "0.1.0"
