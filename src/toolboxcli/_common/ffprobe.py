"""Shared ffprobe JSON probing, used by any script that needs real stream info
rather than what a filename claims."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# Pixel formats name their depth as "<layout>p<depth><endian>" — yuv420p10le. The endian
# suffix only appears above 8 bits, which is what keeps 8-bit layouts whose *name* contains
# digits (yuv410p, yuv411p) from being misread as high bit depth.
_PIX_FMT_DEPTH_RE = re.compile(r"p(\d{1,2})(?:le|be)$")
# Semi-planar formats spell it differently and don't fit the rule above.
_SEMIPLANAR_DEPTHS = {"p010": 10, "p012": 12, "p016": 16}


def probe(path: Path) -> dict | None:
    """Raw `ffprobe -show_streams -show_format` JSON payload, or None if the
    file can't be read (missing binary, corrupt file, timeout)."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_streams", "-show_format",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def bit_depth(pix_fmt: str, bits_per_raw_sample: str | int | None = None) -> int:
    """Source bit depth from ffprobe stream fields. Defaults to 8 when it can't be determined."""
    if bits_per_raw_sample:
        try:
            return int(bits_per_raw_sample)
        except (TypeError, ValueError):
            pass

    fmt = (pix_fmt or "").lower()
    for prefix, depth in _SEMIPLANAR_DEPTHS.items():
        if fmt.startswith(prefix):
            return depth

    m = _PIX_FMT_DEPTH_RE.search(fmt)
    return int(m.group(1)) if m else 8
