"""Shared ANSI/label helpers for local verify CLIs."""

from __future__ import annotations

import os
import sys
from typing import TextIO

_LABEL_WIDTH = 10
_KIBIBYTE = 1024.0
_SPARK_CHARS = "▁▂▃▄▅▆▇█"
_BAR_FILL = "█"
_BAR_EMPTY = "░"


def color_enabled(stream: TextIO | None = None) -> bool:
    if os.environ.get("NO_COLOR", "").strip():
        return False
    if os.environ.get("FORCE_COLOR", "").strip():
        return True
    target = sys.stdout if stream is None else stream
    return target.isatty()


def paint(text: str, *codes: str, stream: TextIO | None = None) -> str:
    if not codes or not color_enabled(stream):
        return text
    return f"{''.join(codes)}{text}\033[0m"


def bold(text: str) -> str:
    return paint(text, "\033[1m")


def dim(text: str) -> str:
    return paint(text, "\033[2m")


def green(text: str) -> str:
    return paint(text, "\033[32m")


def red(text: str) -> str:
    return paint(text, "\033[31m")


def yellow(text: str) -> str:
    return paint(text, "\033[33m")


def cyan(text: str) -> str:
    return paint(text, "\033[36m")


def status_color(status: object) -> str:
    text = str(status)
    if text == "complete":
        return green(text)
    if text == "failed":
        return red(text)
    if text == "processing":
        return yellow(text)
    if text == "pending":
        return dim(text)
    return text


def kv(label: str, value: str, *, width: int = _LABEL_WIDTH) -> str:
    return f"  {dim(label.ljust(width))} {value}"


def heading(title: str) -> str:
    return bold(title)


def human_bytes(n: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    size = float(n)
    for unit in units:
        if size < _KIBIBYTE or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= _KIBIBYTE
    return f"{n} B"


def ascii_bar(value: int, *, width: int, scale: int) -> str:
    """Horizontal bar for ``value`` against ``scale`` (at least 1)."""
    peak = max(1, scale)
    filled = min(width, max(0, round((value / peak) * width)))
    return f"{_BAR_FILL * filled}{_BAR_EMPTY * (width - filled)}"


def sparkline(samples: list[int], *, width: int | None = None) -> str:
    """ASCII sparkline; left-pads with zeros when fewer than ``width`` samples."""
    if width is None:
        series = list(samples)
    elif width <= 0:
        return ""
    else:
        series = ([0] * width + list(samples))[-width:]
    if not series:
        return ""
    peak = max(1, *series)
    last = len(_SPARK_CHARS) - 1
    return "".join(_SPARK_CHARS[min(last, round((v / peak) * last))] for v in series)


def size_sort_key(size: str) -> tuple[int, int | str]:
    try:
        return (0, int(size))
    except ValueError:
        return (1, size)


def eprint(message: str) -> None:
    print(message, file=sys.stderr)
