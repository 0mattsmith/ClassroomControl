"""Tiny text helpers shared between client and master."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_hostname(raw: str) -> str:
    """Strip scheme / path / port / case so an entry suitable for the
    /etc/hosts blocklist comes out the other end.

    Examples::

        "https://www.youtube.com/feed"   -> "www.youtube.com"
        "http://example.com:8080/"       -> "example.com"
        " YouTube.COM "                  -> "youtube.com"
        ""                               -> ""
        None                             -> ""
    """
    s = (raw or "").strip().lower()
    if not s:
        return ""
    if "://" in s:
        parsed = urlparse(s)
        s = parsed.hostname or ""
    # Strip anything past the first '/' that may have leaked through
    s = s.split("/", 1)[0]
    # Strip explicit port
    s = s.split(":", 1)[0]
    return s.strip()
