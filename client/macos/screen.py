"""macOS screen capture using Quartz / CoreGraphics.

Returns a JPEG-encoded byte string. The capture is downscaled to a reasonable
streaming resolution and the JPEG quality is configurable so the master can
ask for thumbnails (small + low quality) or remote-control frames (large +
medium quality).
"""

from __future__ import annotations

import io
from typing import Optional

try:  # macOS-only deps
    import Quartz
    from Quartz import CoreGraphics as CG  # noqa: F401
    _HAVE_QUARTZ = True
except Exception:  # pragma: no cover - non-macOS dev machines
    Quartz = None
    _HAVE_QUARTZ = False

try:
    from AppKit import NSEvent, NSScreen  # noqa: F401
    _HAVE_APPKIT = True
except Exception:
    NSEvent = None
    NSScreen = None
    _HAVE_APPKIT = False

try:
    from PIL import Image, ImageDraw
    _HAVE_PIL = True
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None
    _HAVE_PIL = False


def get_cursor_pos() -> Optional[tuple[int, int]]:
    """Return the current mouse cursor position in display pixel
    coordinates (origin top-left), or ``None`` if unavailable."""
    if not _HAVE_APPKIT:
        return None
    try:
        loc = NSEvent.mouseLocation()
        screen = NSScreen.mainScreen()
        h = screen.frame().size.height
        # NSEvent gives bottom-left origin; convert to top-left.
        return int(loc.x), int(h - loc.y)
    except Exception:
        return None


def _draw_pointer(img, x: int, y: int, scale: float = 1.0) -> None:
    """Paint a big, obvious cursor marker at (x,y) on ``img`` (PIL Image).

    Yellow outer ring + red inner dot — designed to be visible on any
    background, much more so than the real OS cursor. Used during demo
    broadcast so students always see where the teacher is pointing.
    """
    if ImageDraw is None or img is None:
        return
    x = int(x * scale); y = int(y * scale)
    # Draw directly on the image (ImageDraw.Draw mutates in place)
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


def _capture_cgimage():
    """Capture the main display as a CGImage, or None if unavailable."""
    if not _HAVE_QUARTZ:
        return None
    display_id = Quartz.CGMainDisplayID()
    image_ref = Quartz.CGDisplayCreateImage(display_id)
    return image_ref


def screen_size() -> tuple[int, int]:
    if not _HAVE_QUARTZ:
        return (1280, 800)
    display_id = Quartz.CGMainDisplayID()
    w = Quartz.CGDisplayPixelsWide(display_id)
    h = Quartz.CGDisplayPixelsHigh(display_id)
    return (int(w), int(h))


def capture_screen_jpeg(
    max_width: int = 0,
    quality: int = 92,
    fmt: str = "JPEG",
    draw_cursor: bool = False,
) -> Optional[bytes]:
    """Capture and encode the main display.

    Parameters
    ----------
    max_width: 0 (default) means **no downscaling** — captures native
        resolution. Pass a positive value to cap the long side.
    quality:   1-100. JPEG: 90 is visually loss-free for most content;
        WebP: 80-90 is roughly equivalent. Default 90.
    fmt:       "WEBP" (default — much sharper than JPEG at the same
        bitrate, especially on UI text) or "JPEG" for compatibility.

    Returns the encoded byte string. The function name is kept for
    backward compatibility — the format is now configurable.
    """
    if not (_HAVE_QUARTZ and _HAVE_PIL):
        return _capture_screen_fallback(max_width, quality)

    image_ref = _capture_cgimage()
    if image_ref is None:
        return None

    width = Quartz.CGImageGetWidth(image_ref)
    height = Quartz.CGImageGetHeight(image_ref)
    bytes_per_row = Quartz.CGImageGetBytesPerRow(image_ref)
    data_provider = Quartz.CGImageGetDataProvider(image_ref)
    data = Quartz.CGDataProviderCopyData(data_provider)
    buffer = bytes(data)

    img = Image.frombuffer(
        "RGBA", (width, height), buffer, "raw", "BGRA", bytes_per_row, 1
    )
    if max_width and width > max_width:
        new_h = int(height * (max_width / width))
        img = img.resize((max_width, new_h), Image.LANCZOS)
        scale = max_width / width
    else:
        scale = 1.0
    rgb = img.convert("RGB")
    # Optionally paint a big high-contrast marker at the cursor — used
    # by the demo broadcaster so students see where the teacher is pointing.
    if draw_cursor:
        pos = get_cursor_pos()
        if pos is not None:
            _draw_pointer(rgb, pos[0], pos[1], scale=scale)
    out = io.BytesIO()
    fmt = (fmt or "JPEG").upper()
    try:
        if fmt == "WEBP":
            rgb.save(out, format="WEBP", quality=quality, method=4)
        else:
            rgb.save(out, format="JPEG", quality=quality, optimize=False)
    except Exception:
        # Any encoder failure (Pillow without WebP, OOM, etc.) —
        # fall back to plain JPEG, which is always available.
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=quality, optimize=False)
    return out.getvalue()


def _capture_screen_fallback(max_width: int, quality: int) -> Optional[bytes]:
    """Last-resort capture via the `screencapture` CLI (always present on macOS)."""
    if not _HAVE_PIL:
        return None
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        subprocess.run(["screencapture", "-x", "-T0", path], check=True)
        img = Image.open(path)
        w, h = img.size
        if w > max_width:
            img = img.resize((max_width, int(h * max_width / w)), Image.LANCZOS)
        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=quality)
        return out.getvalue()
    except Exception:
        return None
    finally:
        try:
            import os
            os.remove(path)
        except OSError:
            pass
