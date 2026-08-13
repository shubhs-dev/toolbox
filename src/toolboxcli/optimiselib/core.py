"""Filename parsing, encoder selection and duplicate verdicts for optimiselib (no filesystem I/O).

Every function here is pure so the dry-run output and the real run are driven by exactly the
same logic — with no test framework in this repo, `optimiselib -n` is the way these get checked.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta

# ── Resolution ────────────────────────────────────────────────────────

# Standard rungs, descending. A file is tagged with the highest rung at or below its height,
# so a 4:3 1440x1080 camcorder file reads "1080p" rather than an odd "1080p"-adjacent value.
RESOLUTION_RUNGS = (2160, 1440, 1080, 720, 576, 480, 360)

# Tokens optimiselib owns in the bracket group and will replace rather than duplicate.
RESOLUTION_TOKEN_RE = re.compile(r"^\d{3,4}[pi]$", re.IGNORECASE)

# The trailing "[...]" group of a stem.
BRACKET_RE = re.compile(r"^(?P<base>.*?)\s*\[(?P<tags>[^\]]*)\]\s*$")

FIELD_SEP = " - "

# `addsub -u` appends this as its own " - " field, which would otherwise be read as the trip.
SUB_FIELD = "sub"


def resolution_label(height: int) -> str:
    """Bucket a pixel height to the nearest standard rung at or below it."""
    if height <= 0:
        return ""
    for rung in RESOLUTION_RUNGS:
        if height >= rung:
            return f"{rung}p"
    return f"{height}p"


def resolution_height(label: str) -> int:
    """Inverse of resolution_label, for comparing against a tag on an existing file."""
    m = RESOLUTION_TOKEN_RE.match(label or "")
    return int(label[:-1]) if m else 0


# ── Stem parsing ──────────────────────────────────────────────────────

@dataclass
class ParsedStem:
    """A filename stem split into its base name and its bracketed tag group."""

    base: str                                    # "People - Title - Trip"
    tags: list[str] = field(default_factory=list)  # ["Restored", "1080p"]

    @property
    def fields(self) -> list[str]:
        return [f.strip() for f in self.base.split(FIELD_SEP) if f.strip()]

    @property
    def trip(self) -> str:
        """The last " - " field, or "" when the stem carries no trip.

        The convention is "People - Title - Trip", so all three fields are required: a
        two-field stem is an incomplete name, not a video whose title happens to be a
        trip, and parking it beats filing it under the wrong folder.

        A final field of exactly "Sub" is `addsub -u`'s marker, not a trip, so the field
        before it is used instead. Parsing guard only — no Sub tag is written in this pass.
        """
        parts = self.fields
        if parts and parts[-1].lower() == SUB_FIELD:
            parts = parts[:-1]
        return parts[-1] if len(parts) >= 3 else ""

    def identity(self) -> str:
        """What makes two files 'the same video': the base name, tags stripped, normalized."""
        return re.sub(r"\s+", " ", self.base).strip().lower()

    def render(self) -> str:
        """Rebuild the stem, tag group included."""
        if not self.tags:
            return self.base
        return f"{self.base} [{' '.join(self.tags)}]"


def parse_stem(stem: str) -> ParsedStem:
    m = BRACKET_RE.match(stem)
    if not m:
        return ParsedStem(base=stem.strip())
    return ParsedStem(
        base=m.group("base").strip(),
        tags=m.group("tags").split(),
    )


def apply_resolution_tag(stem: str, height: int) -> str:
    """Return *stem* with its resolution token set to match *height*.

    Tokens optimiselib doesn't own survive verbatim in their original order, with the
    resolution appended after them — so "[Restored 720p]" becomes "[Restored 1080p]"
    rather than growing a second resolution.
    """
    parsed = parse_stem(stem)
    label = resolution_label(height)
    kept = [t for t in parsed.tags if not RESOLUTION_TOKEN_RE.match(t)]
    parsed.tags = kept + ([label] if label else [])
    return parsed.render()


# ── Preset selection ──────────────────────────────────────────────────

def select_preset(height: int, preset_1080: str, preset_720: str) -> str:
    """<=720p sources get the 720 preset, everything else the 1080 one.

    Neither preset upscales (PictureAllowUpscaling is false in both), so a 480p source
    re-encodes at 480p rather than being blown up to 720.
    """
    return preset_720 if 0 < height <= 720 else preset_1080


# ── Encoder backends ──────────────────────────────────────────────────

@dataclass(frozen=True)
class Backend:
    name: str
    encoder: str
    inverted_quality: bool = False  # True when the scale runs 0-100, higher = better

    def quality_for(self, preset_cq: float) -> float | None:
        """The -q value to pass, or None when the preset's own value already applies.

        VideoToolbox is the one backend whose quality scale differs: 0-100 with higher
        meaning better, so a CQ of 25 would mean near-worst quality while the encode still
        appears to succeed. Everything else shares the 0-51 lower-is-better scale the
        preset was authored against.
        """
        if not self.inverted_quality:
            return None
        return round((1 - preset_cq / 51) * 100)


BACKENDS = {
    "amd": Backend("amd", "vce_h265"),
    "nvidia": Backend("nvidia", "nvenc_h265"),
    "intel": Backend("intel", "qsv_h265"),
    "apple": Backend("apple", "vt_h265", inverted_quality=True),
    "cpu": Backend("cpu", "x265"),
}

GPU_CHOICES = ("auto", "nvidia", "amd", "apple", "intel", "cpu")

# Intel QSV is included here although _common/encoders.py deliberately omits it: HandBrake
# supports QSV first-class, and an Intel iGPU is a very common Jellyfin server setup.
_PLATFORM_ORDER = {
    "darwin": ("apple", "nvidia", "cpu"),
    "win32": ("nvidia", "amd", "intel", "cpu"),
}
_DEFAULT_ORDER = ("nvidia", "amd", "intel", "cpu")


def platform_order(platform: str | None = None) -> tuple[str, ...]:
    return _PLATFORM_ORDER.get(platform or sys.platform, _DEFAULT_ORDER)


def select_backend(preference: str, available: set[str],
                   preset_encoder: str, platform: str | None = None):
    """Pick a backend. Returns (Backend, auto_selected) or (None, _) if nothing works.

    The preset's own encoder always wins when it's available, so nothing is overridden on
    hardware the preset was authored for. `auto_selected` is False for an explicitly
    requested backend, which is what decides whether a mid-encode failure retries on CPU.
    """
    if preference != "auto":
        backend = BACKENDS.get(preference)
        if backend is None or backend.encoder not in available:
            return None, False
        return backend, False

    for backend in BACKENDS.values():
        if backend.encoder == preset_encoder and preset_encoder in available:
            return backend, True

    for name in platform_order(platform):
        backend = BACKENDS[name]
        if backend.encoder in available:
            return backend, True
    return None, True


def encoder_args(backend: Backend, preset_encoder: str, preset_cq: float,
                 quality_override: float | None = None) -> list[str]:
    """HandBrake CLI args that override the preset's encoder/quality — empty when the
    preset already targets this backend, so an AMD box runs exactly as it does today."""
    args: list[str] = []
    if backend.encoder != preset_encoder:
        args += ["-e", backend.encoder]

    quality = quality_override
    if quality is None and backend.encoder != preset_encoder:
        quality = backend.quality_for(preset_cq)
    if quality is not None:
        args += ["-q", str(quality)]
    return args


# ── Duplicate verdicts ────────────────────────────────────────────────

UPGRADE = "upgrade"
MORE_COMPLETE = "more_complete"
DOWNGRADE = "downgrade"
AMBIGUOUS = "ambiguous"

SHORTER_TOLERANCE = 0.98   # a candidate may be this much shorter and still count as an upgrade
LONGER_THRESHOLD = 1.02    # ...and must be this much longer to count as "more complete"


def duplicate_verdict(new_height: int, new_duration: float,
                      old_height: int, old_duration: float) -> str:
    """Compare an incoming file against the copy already in the library.

    Duration is the only "is this more complete" signal available, so anything that isn't
    an unambiguous win lands in `_review/` for a human rather than being decided here.
    """
    new_rung = resolution_height(resolution_label(new_height))
    old_rung = resolution_height(resolution_label(old_height))

    # Missing duration on either side means we can't judge completeness at all.
    if not new_duration or not old_duration:
        return UPGRADE if new_rung > old_rung else AMBIGUOUS

    long_enough = new_duration >= old_duration * SHORTER_TOLERANCE

    if new_rung > old_rung:
        # Higher resolution but noticeably shorter is a different cut, or truncated.
        return UPGRADE if long_enough else AMBIGUOUS
    if new_rung == old_rung:
        return MORE_COMPLETE if new_duration > old_duration * LONGER_THRESHOLD else AMBIGUOUS
    # Lower resolution. Only a clear downgrade if it isn't also substantially more complete —
    # a longer-but-worse copy is a judgement call, not something to decide unattended.
    return AMBIGUOUS if new_duration > old_duration * LONGER_THRESHOLD else DOWNGRADE


def wins(verdict: str) -> bool:
    return verdict in (UPGRADE, MORE_COMPLETE)


# ── Off-hours window ──────────────────────────────────────────────────

_HOURS_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


def parse_hours(spec: str) -> tuple[dtime, dtime]:
    """Parse "01:00-07:00" into (start, end). Raises ValueError on a bad spec."""
    m = _HOURS_RE.match(spec.strip())
    if not m:
        raise ValueError(f"expected HH:MM-HH:MM, got {spec!r}")
    sh, sm, eh, em = (int(g) for g in m.groups())
    if not (0 <= sh < 24 and 0 <= eh < 24 and 0 <= sm < 60 and 0 <= em < 60):
        raise ValueError(f"hours out of range: {spec!r}")
    return dtime(sh, sm), dtime(eh, em)


def within_hours(window: tuple[dtime, dtime] | None, now: dtime | None = None) -> bool:
    """True when *now* falls inside the window. Handles wraparound (22:00-06:00)."""
    if window is None:
        return True
    start, end = window
    now = now or datetime.now().time()
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def seconds_until(window: tuple[dtime, dtime], now: datetime | None = None) -> int:
    """Seconds until the window next opens, so a closed window sleeps instead of polling."""
    now = now or datetime.now()
    start, _end = window
    target = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1, int((target - now).total_seconds()))
