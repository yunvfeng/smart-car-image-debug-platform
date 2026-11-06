"""Set Windows DPI awareness before Tk or other GUI libraries create windows."""
import ctypes
from ctypes import wintypes
import sys


def enable_high_dpi() -> str:
    if sys.platform != "win32":
        return "platform_default"
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = wintypes.BOOL
        if set_context(ctypes.c_void_p(-4)):
            return "per_monitor_v2"
        if ctypes.get_last_error() == 5:
            return "already_configured"
    except AttributeError:
        pass
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(2) == 0:
            return "per_monitor"
    except (AttributeError, OSError):
        pass
    user32.SetProcessDPIAware()
    return "system_aware"
