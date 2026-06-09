"""Synthesize mouse and keyboard events on Windows via SendInput.

Coordinates from the master are normalized 0.0-1.0; we scale them to the
local primary display resolution and post them with ``user32.SendInput``.
SendInput accepts ABSOLUTE coordinates in the 0..65535 range, which is
exactly what the cross-platform API gives us.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional

try:
    _user32 = ctypes.windll.user32
    _HAVE_WIN = True
except Exception:               # pragma: no cover - non-Windows
    _user32 = None
    _HAVE_WIN = False


# --- Win32 constants -----------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_KEYDOWN = 0x0000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

SM_CXSCREEN = 0
SM_CYSCREEN = 1


# --- INPUT structs -------------------------------------------------------


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


# --- Helpers -------------------------------------------------------------


def _screen_size() -> tuple[int, int]:
    if not _HAVE_WIN:
        return (1280, 800)
    return (int(_user32.GetSystemMetrics(SM_CXSCREEN)),
            int(_user32.GetSystemMetrics(SM_CYSCREEN)))


def _send(inp: INPUT) -> None:
    if not _HAVE_WIN:
        return
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _abs(nx: float, ny: float) -> tuple[int, int]:
    """Convert 0..1 -> Win32 absolute 0..65535."""
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    return (int(nx * 65535), int(ny * 65535))


# --- Mouse ---------------------------------------------------------------


_BTN = {
    "left":  (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "other": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def inject_mouse(event_type: str, nx: float, ny: float, button: str = "left") -> None:
    if not _HAVE_WIN:
        return
    ax, ay = _abs(nx, ny)
    down_flag, up_flag = _BTN.get(button, _BTN["left"])

    if event_type == "move":
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
        inp = INPUT(type=INPUT_MOUSE,
                    u=_INPUT_UNION(mi=MOUSEINPUT(ax, ay, 0, flags, 0, None)))
        _send(inp)
    elif event_type == "down":
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | down_flag
        inp = INPUT(type=INPUT_MOUSE,
                    u=_INPUT_UNION(mi=MOUSEINPUT(ax, ay, 0, flags, 0, None)))
        _send(inp)
    elif event_type == "up":
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | up_flag
        inp = INPUT(type=INPUT_MOUSE,
                    u=_INPUT_UNION(mi=MOUSEINPUT(ax, ay, 0, flags, 0, None)))
        _send(inp)
    elif event_type == "click":
        for flag in (down_flag, up_flag):
            flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | flag
            inp = INPUT(type=INPUT_MOUSE,
                        u=_INPUT_UNION(mi=MOUSEINPUT(ax, ay, 0, flags, 0, None)))
            _send(inp)
    elif event_type == "drag":
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
        inp = INPUT(type=INPUT_MOUSE,
                    u=_INPUT_UNION(mi=MOUSEINPUT(ax, ay, 0, flags, 0, None)))
        _send(inp)


def inject_scroll(dy: int, dx: int = 0) -> None:
    if not _HAVE_WIN:
        return
    # Normalize: master sends Qt's angleDelta (8 units per degree, 15 deg/notch
    # = 120 units per notch); Win32 wants WHEEL_DELTA=120 per notch.
    if dy:
        inp = INPUT(type=INPUT_MOUSE,
                    u=_INPUT_UNION(mi=MOUSEINPUT(0, 0, int(dy), MOUSEEVENTF_WHEEL, 0, None)))
        _send(inp)
    if dx:
        inp = INPUT(type=INPUT_MOUSE,
                    u=_INPUT_UNION(mi=MOUSEINPUT(0, 0, int(dx), MOUSEEVENTF_HWHEEL, 0, None)))
        _send(inp)


# --- Keyboard ------------------------------------------------------------


# Virtual-key codes for named keys from the protocol's KEYMAP vocabulary.
VK = {
    "return": 0x0D, "enter": 0x0D, "tab": 0x09, "space": 0x20,
    "delete": 0x08, "backspace": 0x08, "escape": 0x1B, "esc": 0x1B,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
    "shift": 0x10, "control": 0x11, "ctrl": 0x11,
    "alt": 0x12, "option": 0x12,
    "cmd": 0x5B, "command": 0x5B, "meta": 0x5B, "capslock": 0x14,
}


def _press_vk(vk: int, pressed: bool) -> None:
    flags = KEYEVENTF_KEYDOWN if pressed else KEYEVENTF_KEYUP
    inp = INPUT(type=INPUT_KEYBOARD,
                u=_INPUT_UNION(ki=KEYBDINPUT(vk, 0, flags, 0, None)))
    _send(inp)


def _type_unicode(text: str) -> None:
    for ch in text:
        for flag in (KEYEVENTF_UNICODE | KEYEVENTF_KEYDOWN,
                     KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT(type=INPUT_KEYBOARD,
                        u=_INPUT_UNION(ki=KEYBDINPUT(0, ord(ch), flag, 0, None)))
            _send(inp)


def inject_key(
    key: str,
    pressed: bool = True,
    text: Optional[str] = None,
    modifiers: Optional[list[str]] = None,
) -> None:
    if not _HAVE_WIN:
        return
    if text:
        _type_unicode(text)
        return
    vk = VK.get((key or "").lower())
    if vk is None:
        return
    mods = [m.lower() for m in (modifiers or [])]
    if pressed:
        # press modifiers, then the key
        for m in mods:
            mvk = VK.get(m)
            if mvk:
                _press_vk(mvk, True)
        _press_vk(vk, True)
    else:
        _press_vk(vk, False)
        for m in reversed(mods):
            mvk = VK.get(m)
            if mvk:
                _press_vk(mvk, False)
