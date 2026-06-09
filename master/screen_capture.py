"""Cross-platform screen capture for the teacher (used by demo mode).

Delegates to the platform-appropriate backend so demo broadcast works
whether the teacher's Mac is running the master, or it's been installed
on a Windows machine."""

from __future__ import annotations

from client import platform as _p

capture_screen_jpeg = _p.screen.capture_screen_jpeg
screen_size = _p.screen.screen_size
