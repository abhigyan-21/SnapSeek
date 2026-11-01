"""Best-effort Windows global hotkey registration for the desktop launcher."""

from __future__ import annotations

import ctypes
import os
import sys


HOTKEY_ID = 0x534E
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008


def register_global_hotkey(hwnd: int, specification: str | None = None) -> bool:
    """Register ``Ctrl+Alt+Space`` by default; return false on conflict."""
    if sys.platform != "win32":
        return False
    value = specification or os.environ.get("SNAPSEEK_GLOBAL_HOTKEY", "Ctrl+Alt+Space")
    parts = {part.strip().casefold() for part in value.split("+") if part.strip()}
    modifiers = 0
    for name, flag in (
        ("ctrl", MOD_CONTROL),
        ("control", MOD_CONTROL),
        ("alt", MOD_ALT),
        ("shift", MOD_SHIFT),
        ("win", MOD_WIN),
        ("windows", MOD_WIN),
    ):
        if name in parts:
            modifiers |= flag
    key_name = next((part for part in parts if part not in {"ctrl", "control", "alt", "shift", "win", "windows"}), "")
    key = 0x20 if key_name == "space" else (ord(key_name.upper()) if len(key_name) == 1 else 0)
    if not modifiers or not key:
        return False
    try:
        return bool(ctypes.windll.user32.RegisterHotKey(hwnd, HOTKEY_ID, modifiers, key))
    except (AttributeError, OSError, TypeError):
        return False


def unregister_global_hotkey(hwnd: int) -> None:
    """Release the registered shortcut without affecting other applications."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.UnregisterHotKey(hwnd, HOTKEY_ID)
    except (AttributeError, OSError, TypeError):
        return
