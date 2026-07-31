"""Resolution parsing for downscalevid (no filesystem I/O).

Encoder selection lives in `toolboxcli._common.encoders` — it's shared with
`concatvid`, which re-encodes mismatched parts before concatenating them.
"""

from __future__ import annotations

import re

from toolboxcli._common.console import die

PRESET_HEIGHTS = {
    "8k": 4320,
    "4k": 2160,
    "2160p": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}

_WXH_RE = re.compile(r"^(\d+)x(\d+)$", re.IGNORECASE)
_HEIGHT_RE = re.compile(r"^(\d+)p?$", re.IGNORECASE)


def parse_resolution(spec: str) -> tuple[int | None, int]:
    """Parse a -r/--resolution spec into (width_or_None, height)."""
    key = spec.strip().lower()
    if key in PRESET_HEIGHTS:
        return None, PRESET_HEIGHTS[key]

    m = _WXH_RE.match(key)
    if m:
        return int(m.group(1)), int(m.group(2))

    m = _HEIGHT_RE.match(key)
    if m:
        return None, int(m.group(1))

    die(
        f"invalid resolution '{spec}' — use a preset ({', '.join(PRESET_HEIGHTS)}), "
        "a height (e.g. 900), or WIDTHxHEIGHT (e.g. 1920x1080)"
    )
