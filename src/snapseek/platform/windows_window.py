"""Defensive Windows-native window integration for the Qt desktop client."""

from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from snapseek.windows_hotkey import WM_HOTKEY


LOGGER = logging.getLogger(__name__)

DWM_WINDOW_CORNER_PREFERENCE_ATTRIBUTE = 33
DWM_WINDOW_CORNER_ROUND = 2
WCA_ACCENT_POLICY = 19
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
ACCENT_FLAGS_DRAW_ALL_BORDERS = 2
ACRYLIC_TINT_COLOR_ABGR = 0x30F0E4C8
WM_NCHITTEST = 0x0084
WM_MOUSEACTIVATE = 0x0021
MA_ACTIVATE = 1
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HT_LEFT = 10
HT_RIGHT = 11
HT_TOP = 12
HT_TOPLEFT = 13
HT_TOPRIGHT = 14
HT_BOTTOM = 15
HT_BOTTOMLEFT = 16
HT_BOTTOMRIGHT = 17


class _NativeMessage(ctypes.Structure):
    """Pointer-sized Windows MSG layout used for native hit testing."""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wparam", ctypes.c_size_t),
        ("lparam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("point_x", ctypes.c_long),
        ("point_y", ctypes.c_long),
        ("private", ctypes.c_uint),
    ]


class _NativeRect(ctypes.Structure):
    """Win32 RECT stored in physical screen coordinates."""

    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _AccentPolicy(ctypes.Structure):
    """Windows composition settings for optional Acrylic material."""

    _fields_ = [
        ("accent_state", ctypes.c_int),
        ("accent_flags", ctypes.c_int),
        ("gradient_color", ctypes.c_uint),
        ("animation_id", ctypes.c_int),
    ]


class _WindowCompositionAttributeData(ctypes.Structure):
    """ctypes-backed payload for SetWindowCompositionAttribute."""

    _fields_ = [
        ("attribute", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("size_of_data", ctypes.c_size_t),
    ]


WINDOWS_APP_USER_MODEL_ID = "SnapSeek.Desktop"
SINGLE_INSTANCE_MUTEX_NAME = "Local\\SnapSeek.Desktop.SingleInstance"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9
_single_instance_handle = None


def acquire_single_instance(
    create_mutex=None,
    find_window=None,
    show_window=None,
    foreground_window=None,
    close_handle=None,
    get_last_error=None,
) -> bool:
    """Keep one Windows process and restore its existing window on relaunch."""
    global _single_instance_handle
    if sys.platform != "win32":
        return True
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        create_mutex = create_mutex or kernel32.CreateMutexW
        find_window = find_window or user32.FindWindowW
        show_window = show_window or user32.ShowWindow
        foreground_window = foreground_window or user32.SetForegroundWindow
        close_handle = close_handle or kernel32.CloseHandle
        get_last_error = get_last_error or kernel32.GetLastError
        create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        create_mutex.restype = ctypes.c_void_p
        handle = create_mutex(None, True, SINGLE_INSTANCE_MUTEX_NAME)
        if not handle:
            LOGGER.warning("Could not create the single-instance mutex.")
            return True
        if get_last_error() == ERROR_ALREADY_EXISTS:
            window_handle = find_window(None, "SnapSeek")
            if window_handle:
                show_window(window_handle, SW_RESTORE)
                foreground_window(window_handle)
            close_handle(handle)
            return False
        _single_instance_handle = handle
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        LOGGER.warning("Single-instance protection is unavailable.", exc_info=True)
        return True


def set_app_user_model_id(setter=None) -> bool:
    """Set a stable Windows taskbar identity before QApplication is created."""
    if sys.platform != "win32":
        return False
    if setter is None:
        try:
            setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
            setter.argtypes = [ctypes.c_wchar_p]
            setter.restype = ctypes.c_long
        except (AttributeError, OSError) as error:
            LOGGER.warning("Could not configure the Windows application identity: %s", error)
            return False
    try:
        result = setter(WINDOWS_APP_USER_MODEL_ID)
    except OSError as error:
        LOGGER.warning("Could not configure the Windows application identity: %s", error)
        return False
    if result != 0:
        LOGGER.warning(
            "Windows rejected AppUserModelID %s (HRESULT 0x%08X)",
            WINDOWS_APP_USER_MODEL_ID,
            result & 0xFFFFFFFF,
        )
        return False
    return True


def windows_supports_acrylic_blur() -> bool:
    """Return whether this Windows release is new enough for Acrylic blur."""
    if sys.platform != "win32":
        return False
    application = QApplication.instance()
    if application is not None and application.platformName().lower() != "windows":
        return False
    try:
        return sys.getwindowsversion().build >= 17763
    except (AttributeError, OSError):
        return False


def windows_transparency_effects_enabled() -> bool:
    """Respect Windows Reduce transparency and High Contrast settings."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            enabled, _kind = winreg.QueryValueEx(key, "EnableTransparency")
        if not bool(enabled):
            return False
    except OSError:
        pass
    try:
        class HighContrast(ctypes.Structure):
            _fields_ = [
                ("cb_size", ctypes.c_uint),
                ("flags", ctypes.c_uint),
                ("default_scheme", ctypes.c_wchar_p),
            ]

        preference = HighContrast(ctypes.sizeof(HighContrast), 0, None)
        query = ctypes.windll.user32.SystemParametersInfoW
        query.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        query.restype = ctypes.c_bool
        if query(0x0042, ctypes.sizeof(preference), ctypes.byref(preference), 0):
            return not bool(preference.flags & 0x00000001)
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return True


def enable_acrylic_blur(hwnd: int, setter=None) -> bool:
    """Request Windows Acrylic blur and fail closed if unavailable."""
    if not windows_supports_acrylic_blur() or not windows_transparency_effects_enabled():
        return False
    if setter is None:
        try:
            setter = ctypes.windll.user32.SetWindowCompositionAttribute
            setter.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            setter.restype = ctypes.c_bool
        except (AttributeError, OSError):
            return False
    policy = _AccentPolicy(
        ACCENT_ENABLE_ACRYLICBLURBEHIND,
        ACCENT_FLAGS_DRAW_ALL_BORDERS,
        ACRYLIC_TINT_COLOR_ABGR,
        0,
    )
    data = _WindowCompositionAttributeData(
        WCA_ACCENT_POLICY,
        ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
        ctypes.sizeof(policy),
    )
    try:
        result = setter(hwnd, ctypes.byref(data))
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return bool(result)


def enable_rounded_corners(hwnd: int, setter=None) -> bool:
    """Ask DWM for native rounded-window treatment."""
    if sys.platform != "win32":
        return False
    try:
        if setter is None:
            setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
            setter.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            setter.restype = ctypes.c_long
        corner = ctypes.c_int(DWM_WINDOW_CORNER_ROUND)
        result = setter(
            hwnd,
            DWM_WINDOW_CORNER_PREFERENCE_ATTRIBUTE,
            ctypes.byref(corner),
            ctypes.sizeof(corner),
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return result == 0


def enable_resize_frame(hwnd: int, get_style=None, set_style=None, refresh_frame=None) -> bool:
    """Enable the native sizing frame while retaining Qt's frameless client."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32 if any(
            value is None for value in (get_style, set_style, refresh_frame)
        ) else None
        if get_style is None:
            assert user32 is not None
            get_style = user32.GetWindowLongPtrW
            get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
            get_style.restype = ctypes.c_ssize_t
        if set_style is None:
            assert user32 is not None
            set_style = user32.SetWindowLongPtrW
            set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            set_style.restype = ctypes.c_ssize_t
        if refresh_frame is None:
            assert user32 is not None
            set_window_pos = user32.SetWindowPos
            set_window_pos.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint,
            ]
            set_window_pos.restype = ctypes.c_bool

            def refresh_frame(window_handle):
                flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
                return set_window_pos(window_handle, None, 0, 0, 0, 0, flags)

        window_handle = ctypes.c_void_p(hwnd)
        old_style = int(get_style(window_handle, GWL_STYLE))
        if old_style & WS_THICKFRAME:
            return True
        previous_style = int(set_style(window_handle, GWL_STYLE, old_style | WS_THICKFRAME))
        if old_style and not previous_style:
            return False
        if refresh_frame(window_handle):
            return True
        set_style(window_handle, GWL_STYLE, old_style)
        refresh_frame(window_handle)
    except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
        return False
    return False


def activate_window(hwnd: int) -> bool:
    """Ask Windows to foreground a clicked layered window."""
    if sys.platform != "win32":
        return False
    try:
        foreground = ctypes.windll.user32.SetForegroundWindow
        foreground.argtypes = [ctypes.c_void_p]
        foreground.restype = ctypes.c_bool
        return bool(foreground(ctypes.c_void_p(hwnd)))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def resize_hit_test(
    rect: tuple[int, int, int, int], screen_x: int, screen_y: int, border: int
) -> Qt.Edges | None:
    """Map a pointer near a frameless edge to Qt resize edges."""
    left, top, right, bottom = rect
    if not (left <= screen_x < right and top <= screen_y < bottom):
        return None
    on_left = screen_x < left + border
    on_right = screen_x >= right - border
    on_top = screen_y < top + border
    on_bottom = screen_y >= bottom - border
    if on_top and on_left:
        return Qt.Edge.TopEdge | Qt.Edge.LeftEdge
    if on_top and on_right:
        return Qt.Edge.TopEdge | Qt.Edge.RightEdge
    if on_bottom and on_left:
        return Qt.Edge.BottomEdge | Qt.Edge.LeftEdge
    if on_bottom and on_right:
        return Qt.Edge.BottomEdge | Qt.Edge.RightEdge
    if on_left:
        return Qt.Edge.LeftEdge
    if on_right:
        return Qt.Edge.RightEdge
    if on_top:
        return Qt.Edge.TopEdge
    if on_bottom:
        return Qt.Edge.BottomEdge
    return None


def windows_resize_hit_test(
    rect: tuple[int, int, int, int], screen_x: int, screen_y: int, border: int
) -> int | None:
    """Return a Win32 non-client resize code for an in-window point."""
    edges = resize_hit_test(rect, screen_x, screen_y, border)
    return {
        Qt.Edge.LeftEdge: HT_LEFT,
        Qt.Edge.RightEdge: HT_RIGHT,
        Qt.Edge.TopEdge: HT_TOP,
        Qt.Edge.BottomEdge: HT_BOTTOM,
        Qt.Edge.TopEdge | Qt.Edge.LeftEdge: HT_TOPLEFT,
        Qt.Edge.TopEdge | Qt.Edge.RightEdge: HT_TOPRIGHT,
        Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: HT_BOTTOMLEFT,
        Qt.Edge.BottomEdge | Qt.Edge.RightEdge: HT_BOTTOMRIGHT,
    }.get(edges)


def native_event_result(window, message) -> tuple[bool, int] | None:
    """Handle SnapSeek's Win32 messages; return None for Qt's default path."""
    if sys.platform != "win32" or window.isMaximized() or window.isFullScreen():
        return None
    try:
        native_message = ctypes.cast(
            ctypes.c_void_p(int(message)), ctypes.POINTER(_NativeMessage)
        ).contents
        if native_message.message == WM_HOTKEY:
            window.raise_()
            window.showNormal()
            window.activateWindow()
            return True, 0
        if native_message.message == WM_MOUSEACTIVATE:
            return True, MA_ACTIVATE
        if native_message.message != WM_NCHITTEST:
            return None
        user32 = ctypes.windll.user32
        hwnd = ctypes.c_void_p(int(window.winId()))
        rect = _NativeRect()
        user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.GetWindowRect.restype = ctypes.c_bool
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        dpi = 96
        try:
            user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
            user32.GetDpiForWindow.restype = ctypes.c_uint
            dpi = user32.GetDpiForWindow(hwnd) or dpi
        except (AttributeError, OSError):
            pass
        border = max(6, round(8 * dpi / 96))
        packed = native_message.lparam
        screen_x = packed & 0xFFFF
        screen_y = (packed >> 16) & 0xFFFF
        if screen_x & 0x8000:
            screen_x -= 0x10000
        if screen_y & 0x8000:
            screen_y -= 0x10000
        hit = windows_resize_hit_test(
            (rect.left, rect.top, rect.right, rect.bottom),
            screen_x,
            screen_y,
            border,
        )
        return (True, hit) if hit is not None else None
    except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
        return None
