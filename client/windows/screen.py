"""Windows screen capture.

Primary path uses the ``mss`` library (fast, BitBlt-based). If ``mss``
isn't installed we fall back to ``PIL.ImageGrab`` which uses the GDI
``GetDesktopWindow`` API and is always available on Windows when Pillow
is installed.

Returns a JPEG-encoded ``bytes`` object exactly like the macOS backend.
"""

from __future__ import annotations

import io
from typing import Optional

try:
    import mss              # type: ignore
    _HAVE_MSS = True
except Exception:           # pragma: no cover
    mss = None
    _HAVE_MSS = False

try:
    from PIL import Image, ImageGrab, ImageDraw
    _HAVE_PIL = True
except Exception:           # pragma: no cover
    Image = None
    ImageGrab = None
    ImageDraw = None
    _HAVE_PIL = False


def get_cursor_pos() -> Optional[tuple[int, int]]:
    """Return the mouse cursor's screen coordinates, or None."""
    try:
        import ctypes
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        p = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(p)):
            return int(p.x), int(p.y)
    except Exception:
        pass
    return None


def _draw_pointer(img, x: int, y: int, scale: float = 1.0) -> None:
    """Paint a yellow ring + red dot at (x,y) so the cursor is visible
    on every background during demo broadcast."""
    if ImageDraw is None or img is None:
        return
    x = int(x * scale); y = int(y * scale)
    d = ImageDraw.Draw(img)
    outer_r, inner_r = 36, 18
    d.ellipse(
        [(x - outer_r - 2, y - outer_r - 2),
         (x + outer_r + 2, y + outer_r + 2)],
        outline=(255, 255, 255), width=2,
    )
    d.ellipse(
        [(x - outer_r, y - outer_r), (x + outer_r, y + outer_r)],
        outline=(255, 230, 50), width=6,
    )
    d.ellipse(
        [(x - inner_r, y - inner_r), (x + inner_r, y + inner_r)],
        fill=(220, 30, 30),
    )


def screen_size() -> tuple[int, int]:
    if _HAVE_MSS:
        with mss.mss() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            return int(mon["width"]), int(mon["height"])
    if _HAVE_PIL:
        try:
            img = ImageGrab.grab()
            return img.size
        except Exception:
            pass
    # Last-resort: query Win32 user32.GetSystemMetrics
    try:
        import ctypes
        u32 = ctypes.windll.user32
        return int(u32.GetSystemMetrics(0)), int(u32.GetSystemMetrics(1))
    except Exception:
        return (1280, 800)


def capture_screen_jpeg(
    max_width: int = 0,
    quality: int = 92,
    fmt: str = "JPEG",
    draw_cursor: bool = False,
) -> Optional[bytes]:
    """Capture and encode the primary monitor.

    Function name is kept for backwards compatibility; ``fmt`` lets the
    caller pick WebP (default — much sharper at the same bitrate) or
    JPEG. ``max_width=0`` means no downscaling (captures at native
    resolution).
    """
    img: Optional["Image.Image"] = None

    if _HAVE_MSS and _HAVE_PIL:
        try:
            with mss.mss() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                raw = sct.grab(mon)
                img = Image.frombytes("RGB", raw.size, raw.rgb)
        except Exception:
            img = None

    if img is None and _HAVE_PIL:
        try:
            img = ImageGrab.grab()
        except Exception:
            img = None

    if img is None:
        return None

    w, h = img.size
    if max_width and w > max_width:
        img = img.resize((max_width, int(h * max_width / w)), Image.LANCZOS)
        scale = max_width / w
    else:
        scale = 1.0
    img = img.convert("RGB")
    if draw_cursor:
        pos = get_cursor_pos()
        if pos is not None:
            _draw_pointer(img, pos[0], pos[1], scale=scale)

    fmt = (fmt or "JPEG").upper()
    try:
        if fmt == "WEBP":
            img.save(out, format="WEBP", quality=quality, method=4)
        else:
            img.save(out, format="JPEG", quality=quality, optimize=False)
    except Exception:
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=False)
    return out.getvalue()
