"""Stream inspection, grouping and normalization planning for concatvid (no filesystem I/O).

ffmpeg's concat demuxer with `-c copy` only works when every part agrees on
resolution, video codec, pixel format and audio layout. When they don't, one
part-normalizing re-encode is planned per offending part: the group's dominant
format becomes the target, matching parts stay untouched (bit-for-bit), and a
part whose video already matches gets `-c:v copy` so only its audio is redone.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from toolboxcli._common.encoders import Encoder

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".webm", ".wmv"}

# ffprobe's codec_name → the short codec name `select_encoder` understands.
# Anything else can't be reproduced by our encoders, so it becomes H.264.
VIDEO_CODEC_NAMES = {"h264": "h264", "hevc": "h265"}
FALLBACK_VIDEO_CODEC = "h264"

# The codec_name ffprobe will report back once we've encoded with that codec.
ENCODED_VCODEC = {"h264": "h264", "h265": "hevc"}

# ffprobe's codec_name → the ffmpeg encoder that produces it. Audio codecs we
# can't re-encode to are normalized to AAC instead.
AUDIO_ENCODERS = {
    "aac": "aac",
    "ac3": "ac3",
    "eac3": "eac3",
    "mp3": "libmp3lame",
    "flac": "flac",
    "opus": "libopus",
    "vorbis": "libvorbis",
}
FALLBACK_AUDIO_ENCODER = "aac"

# Encoder name → the codec_name ffprobe reports for it, where the two differ.
ENCODED_ACODEC = {"libmp3lame": "mp3", "libopus": "opus", "libvorbis": "vorbis"}

# Encoders that need an explicit bitrate; ffmpeg's defaults are tuned for stereo
# and quietly gut a 5.1 track.
LOSSY_AUDIO_ENCODERS = {"aac", "ac3", "eac3", "libmp3lame", "libopus", "libvorbis"}
AUDIO_KBPS_PER_CHANNEL = 96

_KEYWORD = r"(?:part|pt|cd|disc|disk)"
_SEQUENCE_RE = re.compile(
    rf"^(?P<base>.+?)[\s_.-]*\(?(?:{_KEYWORD}[\s_.-]*)?(?P<num>\d{{1,3}})\)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StreamInfo:
    """The properties of a file that decide whether it can be stream-copy concatenated."""

    vcodec: str
    width: int
    height: int
    pix_fmt: str
    acodec: str | None
    sample_rate: str | None
    channels: int | None
    nb_video: int = 1
    nb_audio: int = 0
    nb_subtitle: int = 0

    @property
    def has_extra_streams(self) -> bool:
        """True if the file carries more than one video/audio stream, or subtitles.

        Normalizing keeps only the primary video and audio stream, so a group
        where some parts carry extras has to have *every* part normalized —
        otherwise the concatenated parts wouldn't agree on stream layout.
        """
        return self.nb_video > 1 or self.nb_audio > 1 or self.nb_subtitle > 0

    def describe(self) -> str:
        """One-line summary of every property the compatibility check compares."""
        if self.acodec is None:
            audio = "none"
        else:
            audio = f"{self.acodec} {self.sample_rate or '?'}Hz {self.channels or '?'}ch"
        extras = " (+extra streams)" if self.has_extra_streams else ""
        return f"{self.width}x{self.height} {self.vcodec}/{self.pix_fmt}, audio {audio}{extras}"


@dataclass(frozen=True)
class TargetProfile:
    """The format every part of a group gets normalized to before concatenating."""

    width: int
    height: int
    vcodec: str  # codec_name the encode will produce, for comparing against parts
    pix_fmt: str
    codec: str  # short name handed to select_encoder ("h264" | "h265")
    acodec: str | None
    aencoder: str | None
    sample_rate: str | None
    channels: int | None

    def describe(self) -> str:
        if self.acodec is None:
            audio = "none"
        else:
            audio = f"{self.acodec} {self.sample_rate}Hz {self.channels}ch"
        return f"{self.width}x{self.height} {self.vcodec}/{self.pix_fmt}, audio {audio}"


#: Properties that must match across parts for a stream-copy concat to work, other
#: than resolution (which is reported separately). Order is the reporting order.
_COPY_FIELDS: tuple[tuple[str, str], ...] = (
    ("vcodec", "video codec"),
    ("pix_fmt", "pixel format"),
    ("acodec", "audio codec"),
    ("sample_rate", "audio sample rate"),
    ("channels", "audio channels"),
)


def parse_streams(payload: dict[str, Any]) -> StreamInfo | None:
    """Build a StreamInfo from parsed `ffprobe -show_streams -of json` output."""
    streams = payload.get("streams")
    if not streams:
        return None

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None or "width" not in video or "height" not in video:
        return None
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    kinds = Counter(s.get("codec_type") for s in streams)
    return StreamInfo(
        vcodec=video.get("codec_name", ""),
        width=video["width"],
        height=video["height"],
        pix_fmt=video.get("pix_fmt", ""),
        acodec=audio.get("codec_name") if audio else None,
        sample_rate=audio.get("sample_rate") if audio else None,
        channels=audio.get("channels") if audio else None,
        nb_video=kinds["video"],
        nb_audio=kinds["audio"],
        nb_subtitle=kinds["subtitle"],
    )


def find_incompatibilities(parts: list[StreamInfo]) -> tuple[bool, list[str]]:
    """Return (resolution_mismatch, labels of other mismatched properties)."""
    ref = parts[0]
    resolution_mismatch = any((p.width, p.height) != (ref.width, ref.height) for p in parts)
    mismatched = [
        label
        for attr, label in _COPY_FIELDS
        if any(getattr(p, attr) != getattr(ref, attr) for p in parts)
    ]
    return resolution_mismatch, mismatched


def audio_presence_mismatch(parts: list[StreamInfo]) -> bool:
    """True if some parts have an audio track and others don't.

    Re-encoding can't fix this — the silent parts would need a synthesized track
    to keep the stream layout consistent — so such groups are skipped.
    """
    return len({p.acodec is None for p in parts}) > 1


def _majority(values: list[Any]) -> Any:
    """The most common value, ties broken by whichever appears in the earliest part."""
    counts = Counter(values)
    best = max(counts.values())
    return next(v for v in values if counts[v] == best)


def choose_target(parts: list[StreamInfo]) -> TargetProfile:
    """Pick the format a group gets normalized to, in part order.

    Resolution is the group's smallest, so parts are only ever downscaled. Every
    other property is a majority vote. Codecs we have no encoder for are swapped
    for ones we do (H.264 / AAC), which can mean re-encoding parts that already
    agreed with each other.
    """
    smallest = min(parts, key=lambda p: p.width * p.height)
    codec = VIDEO_CODEC_NAMES.get(_majority([p.vcodec for p in parts]), FALLBACK_VIDEO_CODEC)

    with_audio = [p for p in parts if p.acodec is not None]
    if with_audio:
        aencoder = AUDIO_ENCODERS.get(
            _majority([p.acodec for p in with_audio]), FALLBACK_AUDIO_ENCODER
        )
        acodec = ENCODED_ACODEC.get(aencoder, aencoder)
        sample_rate = _majority([p.sample_rate for p in with_audio])
        channels = _majority([p.channels for p in with_audio])
    else:
        aencoder = acodec = sample_rate = channels = None

    return TargetProfile(
        width=smallest.width,
        height=smallest.height,
        vcodec=ENCODED_VCODEC[codec],
        pix_fmt=_majority([p.pix_fmt for p in parts]),
        codec=codec,
        acodec=acodec,
        aencoder=aencoder,
        sample_rate=sample_rate,
        channels=channels,
    )


def video_matches(info: StreamInfo, target: TargetProfile) -> bool:
    return (info.width, info.height, info.vcodec, info.pix_fmt) == (
        target.width, target.height, target.vcodec, target.pix_fmt,
    )


def audio_matches(info: StreamInfo, target: TargetProfile) -> bool:
    return (info.acodec, info.sample_rate, info.channels) == (
        target.acodec, target.sample_rate, target.channels,
    )


def needs_normalizing(info: StreamInfo, target: TargetProfile, drop_extras: bool) -> bool:
    if drop_extras and info.has_extra_streams:
        return True
    return not video_matches(info, target) or not audio_matches(info, target)


def normalize_command(
    src: Path,
    dst: Path,
    info: StreamInfo,
    target: TargetProfile,
    encoder: Encoder | None,
) -> list[str]:
    """Build the ffmpeg command that rewrites one part into the target format.

    Whichever of video/audio already matches is stream-copied, so a part that
    only disagrees on, say, channel count never gets its video re-encoded.
    `encoder` may be None only when the video is copyable.
    """
    cmd = ["ffmpeg", "-y", "-nostdin", "-i", str(src), "-map", "0:v:0"]
    if target.acodec is not None:
        cmd += ["-map", "0:a:0"]

    if video_matches(info, target):
        cmd += ["-c:v", "copy"]
    else:
        if encoder is None:
            raise ValueError(f"'{src.name}' needs a video re-encode but no encoder was selected")
        if (info.width, info.height) != (target.width, target.height):
            cmd += ["-vf", f"scale={target.width}:{target.height}"]
        cmd += ["-c:v", encoder.name, *encoder.args, "-pix_fmt", target.pix_fmt]

    if target.acodec is None:
        cmd += ["-an"]
    elif audio_matches(info, target):
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", target.aencoder, "-ar", str(target.sample_rate), "-ac", str(target.channels)]
        if target.aencoder in LOSSY_AUDIO_ENCODERS:
            cmd += ["-b:a", f"{AUDIO_KBPS_PER_CHANNEL * target.channels}k"]

    return [*cmd, str(dst)]


def split_sequence(stem: str) -> tuple[str, int] | None:
    """Split a filename stem into (base_name, sequence_number), or None if no marker."""
    m = _SEQUENCE_RE.match(stem)
    if not m:
        return None
    base = re.sub(r"[\s_.-]+$", "", m.group("base"))
    if not base:
        return None
    return base, int(m.group("num"))
