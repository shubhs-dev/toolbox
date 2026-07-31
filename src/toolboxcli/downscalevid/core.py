"""Resolution parsing and GPU/CPU encoder selection for downscalevid (no filesystem I/O)."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache

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

CODECS = ("h264", "h265")

# Software (CPU) encoders — the always-available fallback.
CPU_ENCODERS = {"h264": "libx264", "h265": "libx265"}
CPU_ARGS = ["-crf", "18", "-preset", "medium"]

# Hardware encoders, per vendor backend. Each entry maps a codec to its ffmpeg
# encoder name plus the rate-control args that give roughly CRF-18-like quality
# on that vendor's silicon (each vendor exposes its own quality knob — there is
# no portable -crf for hardware encoders).
#
# NVIDIA/AMD/Apple only: those are the desktop GPUs this repo targets. Intel
# QSV and VAAPI are deliberately not probed.
BACKENDS = {
    "nvidia": {
        "label": "NVIDIA NVENC",
        "encoders": {"h264": "h264_nvenc", "h265": "hevc_nvenc"},
        "args": ["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "20", "-b:v", "0"],
    },
    "amd": {
        "label": "AMD AMF",
        "encoders": {"h264": "h264_amf", "h265": "hevc_amf"},
        "args": ["-quality", "quality", "-rc", "cqp", "-qp_i", "20", "-qp_p", "20"],
    },
    "apple": {
        "label": "Apple VideoToolbox",
        "encoders": {"h264": "h264_videotoolbox", "h265": "hevc_videotoolbox"},
        # -q:v is VideoToolbox's constant-quality mode (1-100, higher is better);
        # it is only supported on Apple silicon, so the probe below rejects this
        # backend on Intel Macs and we fall through to the CPU encoder.
        "args": ["-q:v", "60"],
    },
}

# Which vendors are even worth probing, and in what order, per platform.
# (macOS has had no NVENC/AMF support for years — VideoToolbox is the only path.)
_PLATFORM_ORDER = {
    "darwin": ("apple",),
    "win32": ("nvidia", "amd"),
}
_DEFAULT_ORDER = ("nvidia", "amd")

_WXH_RE = re.compile(r"^(\d+)x(\d+)$", re.IGNORECASE)
_HEIGHT_RE = re.compile(r"^(\d+)p?$", re.IGNORECASE)


@dataclass(frozen=True)
class Encoder:
    """A resolved encoder: which ffmpeg encoder to use and how to drive it."""

    backend: str  # "nvidia" | "amd" | "apple" | "cpu"
    label: str
    name: str
    args: list[str]

    @property
    def is_hardware(self) -> bool:
        return self.backend != "cpu"


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


def cpu_encoder(codec: str) -> Encoder:
    return Encoder(backend="cpu", label="CPU", name=CPU_ENCODERS[codec], args=list(CPU_ARGS))


def _backend_encoder(backend: str, codec: str) -> Encoder:
    spec = BACKENDS[backend]
    return Encoder(
        backend=backend,
        label=spec["label"],
        name=spec["encoders"][codec],
        args=list(spec["args"]),
    )


@lru_cache(maxsize=None)
def _encoder_works(name: str, args: tuple[str, ...]) -> bool:
    """Encode one throwaway frame to check the GPU actually accepts these settings.

    `ffmpeg -encoders` lists every encoder the build was compiled with, present
    hardware or not, so it can't answer this — only a real encode can.
    """
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-v", "error", "-nostdin",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25",
            "-frames:v", "1",
            "-c:v", name, *args,
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def select_encoder(codec: str, preference: str) -> Encoder:
    """Resolve -g/--gpu into a concrete encoder.

    `preference` is "auto" (first working GPU, else CPU), "cpu" (never probe), or
    a vendor name (that GPU or nothing — dies if it isn't usable).
    """
    if preference == "cpu":
        return cpu_encoder(codec)

    if preference == "auto":
        # Probed in platform preference order, stopping at the first hit — a
        # probe costs an ffmpeg spawn, so don't run the ones we'd never use.
        for backend in _PLATFORM_ORDER.get(sys.platform, _DEFAULT_ORDER):
            spec = BACKENDS[backend]
            if _encoder_works(spec["encoders"][codec], tuple(spec["args"])):
                return _backend_encoder(backend, codec)
        return cpu_encoder(codec)

    spec = BACKENDS[preference]
    name = spec["encoders"][codec]
    if not _encoder_works(name, tuple(spec["args"])):
        die(
            f"{spec['label']} ({name}) isn't usable on this machine — no such GPU, "
            "no driver, or an ffmpeg build without it. Use -g auto or -g cpu."
        )
    return _backend_encoder(preference, codec)
