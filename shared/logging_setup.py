"""Minimal logging configuration shared by both apps."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def configure(name: str, log_file: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=3
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    logger.propagate = False
    return logger
