"""
Windows kiosk-mode helpers — the equivalent of macOS's
``NSApplicationPresentationOptions``. Called by ``client/overlay.py``
when the master sends ``LOCK`` with ``strict=True``.

What this gives you when active:

  * **Low-level keyboard hook** that swallows the most common escape
    shortcuts: the Win key (both), ``Alt+Tab``, ``Alt+F4``, ``Ctrl+Esc``,
    and ``Alt+Esc``. The student physically can't switch away.
  * **Taskbar hidden** so they can't click their way out via the Start
    button or pinned apps.
  * **Task Manager disabled** via the per-user
    ``Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System``
    DisableTaskMgr policy, so even Ctrl+Shift+Esc / right-click-taskbar
    paths are dead.

What this does NOT block:

  * ``Ctrl+Alt+Del`` itself — that's a Secure Attention Sequence handled
    by winlogon at kernel level; only Group Policy on Pro / Enterprise
    can disable it. Modern Windows doesn't allow user-space apps to
    intercept it (by design — it's the "I trust this" anchor).
  * Pulling the power cord. :)

All effects are reversed by :func:`exit_kiosk`.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import sys

LOG = logging.getLogger("classcontrol.client.kiosk")

# ---------------------------------------------------------------------------
# Win32 bindings (no-ops on non-Windows so the module is import-safe)
# ---------------------------------------------------------------------------

try:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _HAVE_WIN = True
except Exception:
    # No ctypes.windll on non-Windows — the module still has to import
    # cleanly though (it's referenced unconditionally by client/overlay.py).
    _user32 = None
    _kernel32 = None
    _HAVE_WIN = False


WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104

VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_F4 = 0x73
VK_CONTROL = 0x11
VK_MENU = 0x12   # Alt

SW_HIDE = 0
SW_SHOW = 5


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",      ctypes.wintypes.DWORD),
        ("scanCode",    ctypes.wintypes.DWORD),
        ("flags",       ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


# LRESULT CALLBACK LowLevelKeyboardProc(int, WPARAM, LPARAM)
# WINFUNCTYPE only exists on Windows. Fall back to CFUNCTYPE elsewhere
# so the module still imports cleanly on Linux dev boxes / CI — none of
# the kiosk functions actually run there (they're gated on _HAVE_WIN).
if _HAVE_WIN:
    _HOOK_PROC_TYPE = ctypes.WINFUNCTYPE(
        ctypes.c_long,
        ctypes.c_int,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    )
else:
    _HOOK_PROC_TYPE = ctypes.CFUNCTYPE(
        ctypes.c_long,
        ctypes.c_int,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    )


_hook_id = None
_hook_proc = None         # keep a ref so it isn't GC'd
_kiosk_active = False


def _is_pressed(vk: int) -> bool:
    """Async key-state probe (high-order bit = currently down)."""
    if not _HAVE_WIN:
        return False
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def _on_key(nCode, wParam, lParam):  # noqa: N803 - Win32 calling convention
    """Low-level keyboard hook procedure.

    Return 1 to swallow the event, anything else to pass it on via
    ``CallNextHookEx``.
    """
    try:
        if nCode == HC_ACTION and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            kbd = ctypes.cast(
                lParam, ctypes.POINTER(KBDLLHOOKSTRUCT),
            ).contents
            vk = kbd.vkCode

            # Windows key (left or right) — always swallow while kiosk
            if vk in (VK_LWIN, VK_RWIN):
                return 1

            # Alt + Tab, Alt + F4, Alt + Esc
            if vk == VK_TAB and _is_pressed(VK_MENU):
                return 1
            if vk == VK_F4 and _is_pressed(VK_MENU):
                return 1
            if vk == VK_ESCAPE and _is_pressed(VK_MENU):
                return 1

            # Ctrl + Esc (Start menu fallback)
            if vk == VK_ESCAPE and _is_pressed(VK_CONTROL):
                return 1
    except Exception:
        # Never let an exception in here kill the hook chain — Windows
        # would silently uninstall the hook if we took too long anyway.
        pass
    return _user32.CallNextHookEx(None, nCode, wParam, lParam)


def install_keyboard_hook() -> bool:
    """Install the low-level keyboard hook. Safe to call repeatedly."""
    global _hook_id, _hook_proc
    if not _HAVE_WIN:
        return False
    if _hook_id is not None:
        return True
    _hook_proc = _HOOK_PROC_TYPE(_on_key)
    hmod = _kernel32.GetModuleHandleW(None)
    _hook_id = _user32.SetWindowsHookExW(
        WH_KEYBOARD_LL, _hook_proc, hmod, 0,
    )
    if not _hook_id:
        err = ctypes.get_last_error()
        LOG.warning("SetWindowsHookExW failed (err=%s)", err)
        _hook_proc = None
        return False
    LOG.info("kiosk keyboard hook installed (handle=%s)", _hook_id)
    return True


def uninstall_keyboard_hook() -> None:
    global _hook_id, _hook_proc
    if _hook_id and _HAVE_WIN:
        try:
            _user32.UnhookWindowsHookEx(_hook_id)
            LOG.info("kiosk keyboard hook removed")
        except Exception:
            LOG.exception("UnhookWindowsHookEx failed")
    _hook_id = None
    _hook_proc = None


def _show_taskbar(hidden: bool) -> None:
    if not _HAVE_WIN:
        return
    flag = SW_HIDE if hidden else SW_SHOW
    for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        try:
            hwnd = _user32.FindWindowW(cls, None)
            if hwnd:
                _user32.ShowWindow(hwnd, flag)
        except Exception:
            LOG.exception("ShowWindow(%s, %s) failed", cls, flag)


def _set_input_blocked(blocked: bool) -> bool:
    """Disable all mouse and keyboard input via ``user32.BlockInput``.

    The student physically cannot move the mouse or type until
    BlockInput(FALSE) is called. Requires the calling process to be
    elevated (we're built with ``uac_admin=True`` so this works) and
    won't survive Ctrl+Alt+Del (which auto-clears the block — by
    design, so the OS can never be DOS'd permanently).

    Returns True on success, False otherwise.
    """
    if not _HAVE_WIN:
        return False
    try:
        # Win32 BOOL — non-zero means it returned successfully.
        result = _user32.BlockInput(1 if blocked else 0)
        LOG.info("BlockInput(%s) = %s", blocked, bool(result))
        return bool(result)
    except Exception:
        LOG.exception("BlockInput(%s) raised", blocked)
        return False


def _set_task_manager_disabled(disabled: bool) -> None:
    """Toggle the per-user DisableTaskMgr policy."""
    if sys.platform != "win32":
        return
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            if disabled:
                winreg.SetValueEx(key, "DisableTaskMgr", 0, winreg.REG_DWORD, 1)
            else:
                try:
                    winreg.DeleteValue(key, "DisableTaskMgr")
                except FileNotFoundError:
                    pass
    except Exception:
        LOG.exception(
            "DisableTaskMgr policy toggle failed (disabled=%s)", disabled,
        )


# ---------------------------------------------------------------------------
# Public API used by client/overlay.py
# ---------------------------------------------------------------------------


def enter_kiosk() -> None:
    """Enable kiosk restrictions (idempotent)."""
    global _kiosk_active
    if not _HAVE_WIN or _kiosk_active:
        return
    install_keyboard_hook()
    _show_taskbar(True)
    _set_task_manager_disabled(True)
    # Final layer: completely block mouse + keyboard at the driver level.
    # The overlay covers the screens; this makes sure no input gets through
    # to anything else (even modal Windows dialogs underneath).
    _set_input_blocked(True)
    _kiosk_active = True
    LOG.info("Windows kiosk mode ENABLED (with input block)")


def exit_kiosk() -> None:
    """Disable kiosk restrictions (idempotent)."""
    global _kiosk_active
    if not _HAVE_WIN or not _kiosk_active:
        return
    # Restore in reverse order so a thread context switch mid-shutdown
    # can never leave input blocked with no way back.
    _set_input_blocked(False)
    uninstall_keyboard_hook()
    _show_taskbar(False)
    _set_task_manager_disabled(False)
    _kiosk_active = False
    LOG.info("Windows kiosk mode DISABLED")


def is_active() -> bool:
    return bool(_kiosk_active)
