"""macOS-specific backends for the ClassControl client.

These modules wrap Quartz / AppKit / shell commands so the cross-platform
client code can call uniform functions like ``capture_screen_jpeg`` or
``inject_mouse``. Each module degrades gracefully if its dependency is
missing, so the project still imports on a developer Linux machine.
"""
