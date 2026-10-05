"""Rain-gap / drought alerts from GPM IMERG + SMAP (design §5.2)."""

from .config import RainConfig
from .detect import RainAlert, detect_rain_series

__all__ = ["RainAlert", "RainConfig", "detect_rain_series"]
