"""Synthesize mouse and keyboard events on macOS using CGEvent.

Coordinates from the master are normalized 0.0-1.0 so they work across
displays of different resolution. We scale them to the local main display
just before posting the event.
"""

from __future__ import annotations

from typing import Optional

try:
    import Quartz
    from Quartz import CoreGraphics as CG  # noqa: F401
    _HAVE_QUARTZ = True
except Exception:  # pragma: no cover
    Quartz = None
    _HAVE_QUARTZ = False


# Mac virtual key codes for common keys; the master sends a key *name*
# which we translate to a virtual key. For printable characters we fall
# back to CGEventCreateKeyboardEvent + CGEventKeyboardSetUnicodeString.
KEYMAP = {
    "return": 0x24, "enter": 0x24, "tab": 0x30, "space": 0x31,
    "delete": 0x33, "backspace": 0x33, "escape": 0x35, "esc": 0x35,
    "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
    "home": 0x73, "end": 0x77, "pageup": 0x74, "pagedown": 0x79,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60,
    "f6": 0x61, "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6D,
    "f11": 0x67, "f12": 0x6F,
    "shift": 0x38, "control": 0x3B, "ctrl": 0x3B, "option": 0x3A,
    "alt": 0x3A, "command": 0x37, "cmd": 0x37, "capslock": 0x39,
}


def _screen_size() -> tuple[int, int]:
    if not _HAVE_QUARTZ:
        return (1280, 800)
    did = Quartz.CGMainDisplayID()
    return (int(Quartz.CGDisplayPixelsWide(did)), int(Quartz.CGDisplayPixelsHigh(did)))


def _denorm(nx: float, ny: float) -> tuple[float, float]:
    w, h = _screen_size()
    return (max(0.0, min(1.0, nx)) * w, max(0.0, min(1.0, ny)) * h)


def inject_mouse(event_type: str, nx: float, ny: float, button: str = "left") -> None:
    """event_type: move | down | up | click | scroll"""
    if not _HAVE_QUARTZ:
        return
    x, y = _denorm(nx, ny)
    btn_map = {
        "left":  (Quartz.kCGEventLeftMouseDown,  Quartz.kCGEventLeftMouseUp,
                  Quartz.kCGEventLeftMouseDragged, Quartz.kCGMouseButtonLeft),
        "right": (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp,
                  Quartz.kCGEventRightMouseDragged, Quartz.kCGMouseButtonRight),
        "other": (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp,
                  Quartz.kCGEventOtherMouseDragged, Quartz.kCGMouseButtonCenter),
    }
    down_ev, up_ev, drag_ev, mouse_btn = btn_map.get(button, btn_map["left"])

    if event_type == "move":
        ev = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, (x, y), 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
    elif event_type == "down":
        ev = Quartz.CGEventCreateMouseEvent(None, down_ev, (x, y), mouse_btn)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
    elif event_type == "up":
        ev = Quartz.CGEventCreateMouseEvent(None, up_ev, (x, y), mouse_btn)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
    elif event_type == "click":
        ev = Quartz.CGEventCreateMouseEvent(None, down_ev, (x, y), mouse_btn)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        ev = Quartz.CGEventCreateMouseEvent(None, up_ev, (x, y), mouse_btn)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
    elif event_type == "drag":
        ev = Quartz.CGEventCreateMouseEvent(None, drag_ev, (x, y), mouse_btn)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def inject_scroll(dy: int, dx: int = 0) -> None:
    if not _HAVE_QUARTZ:
        return
    ev = Quartz.CGEventCreateScrollWheelEvent(
        None, Quartz.kCGScrollEventUnitPixel, 2, int(dy), int(dx)
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _modifier_flags(modifiers: list[str]) -> int:
    flags = 0
    for m in modifiers or []:
        m = m.lower()
        if m in ("shift",):
            flags |= Quartz.kCGEventFlagMaskShift
        elif m in ("ctrl", "control"):
            flags |= Quartz.kCGEventFlagMaskControl
        elif m in ("alt", "option"):
            flags |= Quartz.kCGEventFlagMaskAlternate
        elif m in ("cmd", "command", "meta"):
            flags |= Quartz.kCGEventFlagMaskCommand
    return flags


def inject_key(
    key: str,
    pressed: bool = True,
    text: Optional[str] = None,
    modifiers: Optional[list[str]] = None,
) -> None:
    """Inject a keyboard event.

    If ``text`` is provided, the literal Unicode string is typed
    (used for arbitrary characters that don't map to virtual keys).
    Otherwise ``key`` should be a named key from ``KEYMAP``.
    """
    if not _HAVE_QUARTZ:
        return
    if text:
        ev = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
        Quartz.CGEventKeyboardSetUnicodeString(ev, len(text), text)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        ev_up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
        Quartz.CGEventKeyboardSetUnicodeString(ev_up, len(text), text)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev_up)
        return
    vk = KEYMAP.get((key or "").lower())
    if vk is None:
        return
    ev = Quartz.CGEventCreateKeyboardEvent(None, vk, pressed)
    if modifiers:
        Quartz.CGEventSetFlags(ev, _modifier_flags(modifiers))
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
