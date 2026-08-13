"""Filename parsing/classification for jellyname (no filesystem I/O)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from toolboxcli._common.console import warn
from toolboxcli._common.ffprobe import bit_depth as _probe_bit_depth

VIDEO_EXTS = {"mkv", "mp4", "avi", "mov", "m4v", "ts", "webm"}

# ---- canonicalization: runs on the raw stem, before dots/underscores become
# spaces, so multi-word/dotted tag spellings survive as a single token later.
_COMPOUND_TAG_SUBS = [
    (re.compile(r"\bH[.\-_ ]?26([45])\b", re.IGNORECASE), r"x26\1"),
    (re.compile(r"\bBlu[.\-_ ]?Ray\b", re.IGNORECASE), "BluRay"),
    (re.compile(r"\bDolby[.\-_ ]?Vision\b", re.IGNORECASE), "DolbyVision"),
    (re.compile(r"\bWEB[.\-_ ]?DL\b", re.IGNORECASE), "WEB-DL"),
    (re.compile(r"\bWEB[.\-_ ]?Rip\b", re.IGNORECASE), "WEBRip"),
    (re.compile(r"\bDDP5[.\-_ ]1\b", re.IGNORECASE), "DDP5-1"),
    (re.compile(r"\bDD5[.\-_ ]1\b", re.IGNORECASE), "DD5-1"),
    (re.compile(r"\b10[.\-_ ]?bit\b", re.IGNORECASE), "10bit"),
    (re.compile(r"\b8[.\-_ ]?bit\b", re.IGNORECASE), "8bit"),
]


def _canonicalize_compound_tags(raw: str) -> str:
    for pattern, repl in _COMPOUND_TAG_SUBS:
        raw = pattern.sub(repl, raw)
    return raw


def normalize_name(name: str) -> str:
    name = _canonicalize_compound_tags(name)
    name = re.sub(r"[._]", " ", name)
    name = re.sub(r" +", " ", name).strip()
    return name


# ---- tag dictionaries: lowercase token -> canonical display string.
# First dictionary (in _TAG_CATEGORIES order) that matches a token wins.

RESOLUTION_TAGS = {
    "2160p": "2160p", "2160i": "2160i",
    "1080p": "1080p", "1080i": "1080i",
    "720p": "720p", "720i": "720i",
    "480p": "480p", "480i": "480i",
    "4k": "2160p",
}
HDR_TAGS = {
    "hdr10+": "HDR10+", "hdr10": "HDR10",
    "dolbyvision": "Dolby Vision", "dv": "Dolby Vision",
    "hdr": "HDR", "sdr": "SDR",
}
AUDIO_TAGS = {
    "atmos": "Atmos", "truehd": "TrueHD", "dts": "DTS",
    "ddp5-1": "DDP5.1", "dd5-1": "DD5.1",
    "eac3": "EAC3", "ac3": "AC3", "aac": "AAC", "flac": "FLAC",
}
VIDEO_CODEC_TAGS = {
    "x264": "x264", "x265": "x265", "hevc": "HEVC", "avc": "AVC",
    "xvid": "XviD", "divx": "DivX",
}
BIT_DEPTH_TAGS = {
    "10bit": "10bit", "8bit": "8bit", "hi10p": "Hi10P",
}
SOURCE_TAGS = {
    "bluray": "BluRay", "bdrip": "BDRip", "brrip": "BRRip",
    "web-dl": "WEB-DL", "webrip": "WEBRip", "web": "WEB",
    "hdrip": "HDRip", "dvdrip": "DVDRip", "hdtv": "HDTV",
    "amzn": "AMZN", "hulu": "HULU", "dsnp": "DSNP", "remux": "REMUX",
}
EDITION_TAGS = {
    "proper": "PROPER", "repack": "REPACK", "extended": "EXTENDED",
    "theatrical": "THEATRICAL", "remastered": "REMASTERED", "imax": "IMAX",
}

_TAG_CATEGORIES = (
    ("resolution", RESOLUTION_TAGS),
    ("hdr", HDR_TAGS),
    ("audio", AUDIO_TAGS),
    ("video_codec", VIDEO_CODEC_TAGS),
    ("bit_depth", BIT_DEPTH_TAGS),
    ("source", SOURCE_TAGS),
    ("edition", EDITION_TAGS),
)


@dataclass
class MediaTags:
    resolution: str = ""
    video_codec: str = ""
    bit_depth: str = ""
    hdr: str = ""
    audio: list[str] = field(default_factory=list)
    source: list[str] = field(default_factory=list)
    edition: list[str] = field(default_factory=list)

    def add(self, category: str, value: str) -> None:
        if category in ("audio", "source", "edition"):
            getattr(self, category).append(value)
        else:
            setattr(self, category, value)

    def bracket(self) -> str:
        """Renders every field except resolution (which stays the Jellyfin
        version-label suffix) as a single bracketed, space-separated group."""
        parts = [*self.source, *self.edition]
        if self.video_codec:
            parts.append(self.video_codec)
        if self.bit_depth:
            parts.append(self.bit_depth)
        if self.hdr:
            parts.append(self.hdr)
        parts.extend(self.audio)
        return f" [{' '.join(parts)}]" if parts else ""


# ---- ffprobe fallback: source/edition (BluRay, PROPER, ...) describe release
# provenance, not stream *properties* — decoded video/audio characteristics
# can never say "this was an IMAX release" — so they're derived differently
# from the four fields below (plus audio codec): those come straight from the
# stream, source/edition instead come from the container's own embedded
# title tag, when present (common for scene releases and disc rips, and
# untouched by a plain filesystem rename — see _title_source_edition below),
# run through the same tokenizer/classifier filenames get. Probing always
# runs, even when the filename already specifies every field it can —
# bit_depth/HDR follow the filename parser's own convention of leaving the
# unremarkable case untagged (8bit, SDR carry no token), so a filename that's
# "fully tagged" in every other respect still can't be trusted to mean "no
# HDR/10bit to report" without actually checking the stream.
_PROBE_RESOLUTIONS = {2160: "2160", 1080: "1080", 720: "720", 480: "480"}
_PROBE_VIDEO_CODECS = {"h264": "AVC", "hevc": "HEVC"}
_INTERLACED_FIELD_ORDERS = {"tt", "bb", "tb", "bt"}


def _title_source_edition(payload: dict) -> tuple[list[str], list[str]]:
    """Extracts source/edition tags (BluRay, PROPER, IMAX, ...) from the
    container's embedded title tag, by running it through the same
    tokenizer/classifier a filename gets. Looked up case-insensitively since
    muxers disagree on "title" vs "TITLE". Every other category found in the
    title is ignored — resolution/codec/bit-depth/HDR/audio always come from
    the stream itself via probed_tags(), which is authoritative; a stale
    embedded string should never compete with that.
    """
    tags_dict = payload.get("format", {}).get("tags") or {}
    title = next((v for k, v in tags_dict.items() if k.lower() == "title"), "")
    if not title or not isinstance(title, str):
        return [], []
    scratch = MediaTags()
    for tok in tokenize(normalize_name(title)):
        _classify_token(tok, scratch)
    return scratch.source, scratch.edition


def probed_tags(payload: dict) -> dict[str, str | list[str]]:
    """Pure mapping from an already-fetched ffprobe JSON payload to whichever
    MediaTags fields can be confidently derived from stream data, plus
    source/edition recovered from the embedded title tag if one is present."""
    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    result: dict[str, str | list[str]] = {}

    if video is not None:
        height = video.get("height")
        if isinstance(height, int) and height > 0:
            interlaced = video.get("field_order") in _INTERLACED_FIELD_ORDERS
            nearest = min(_PROBE_RESOLUTIONS, key=lambda h: abs(h - height))
            result["resolution"] = f"{_PROBE_RESOLUTIONS[nearest]}{'i' if interlaced else 'p'}"

        codec_name = (video.get("codec_name") or "").lower()
        if codec_name in _PROBE_VIDEO_CODECS:
            result["video_codec"] = _PROBE_VIDEO_CODECS[codec_name]

        depth = _probe_bit_depth(video.get("pix_fmt", ""), video.get("bits_per_raw_sample"))
        if depth > 8:
            result["bit_depth"] = f"{depth}bit"

        is_dolby_vision = any(
            "dovi" in (sd.get("side_data_type") or "").lower()
            for sd in video.get("side_data_list") or []
        )
        transfer = (video.get("color_transfer") or "").lower()
        if is_dolby_vision:
            result["hdr"] = "Dolby Vision"
        elif transfer == "smpte2084":
            result["hdr"] = "HDR10"
        elif transfer == "arib-std-b67":
            result["hdr"] = "HDR"

    if audio is not None:
        codec_name = (audio.get("codec_name") or "").lower()
        if codec_name in AUDIO_TAGS:
            result["audio"] = AUDIO_TAGS[codec_name]

    source, edition = _title_source_edition(payload)
    if source:
        result["source"] = source
    if edition:
        result["edition"] = edition

    return result


def apply_probed_tags(tags: MediaTags, probed: dict[str, str | list[str]]) -> None:
    """Fills only the MediaTags fields still empty; a tag already present from
    the filename is never overwritten."""
    for field_name in ("resolution", "video_codec", "bit_depth", "hdr"):
        if not getattr(tags, field_name) and probed.get(field_name):
            setattr(tags, field_name, probed[field_name])
    if not tags.audio and probed.get("audio"):
        tags.audio.append(probed["audio"])
    if not tags.source and probed.get("source"):
        tags.source.extend(probed["source"])
    if not tags.edition and probed.get("edition"):
        tags.edition.extend(probed["edition"])


TV_EP_RE = re.compile(r"^[Ss](\d{1,2})[Ee](\d{1,2})(?:-?[Ee](\d{1,2}))?$")
TV_NXN_RE = re.compile(r"^(\d{1,2})[xX](\d{1,2})$")
YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*]')

_TOKEN_RE = re.compile(r"\[[^\]]+\]|\([^)]+\)|\S+")


def tokenize(norm: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(norm) if t != "-"]


def _is_wrapped(token: str) -> bool:
    return len(token) >= 2 and token[0] in "[(" and token[-1] in "])"


def _unwrap(token: str) -> str:
    return token[1:-1] if _is_wrapped(token) else token


def _classify_token(token: str, tags: MediaTags) -> bool | None:
    """Classify a single token into `tags`.

    Returns True if it was a recognized tag, False if it's plain text (title
    material), or None if it was bracket/paren-wrapped content that matched no
    dictionary and should be silently dropped (fansub groups, hashes, etc).

    A wrapped token that fails as a single unit — e.g. jellyname's own
    multi-tag "[BluRay x265 10bit AAC]" group — is retried word-by-word
    before being given up on, so re-running jellyname over its own output
    (or any other bracketed multi-tag group) recovers every tag inside
    instead of discarding the whole group as unrecognized noise.
    """
    is_wrapped = _is_wrapped(token)
    inner = _unwrap(token)
    lowered = inner.lower()

    for category, table in _TAG_CATEGORIES:
        if lowered in table:
            tags.add(category, table[lowered])
            return True

    if is_wrapped:
        words = inner.split()
        if len(words) > 1:
            results = [_classify_token(word, tags) for word in words]
            if any(results):
                return True
        return None

    if "-" in token:
        prefix_lower = token.rpartition("-")[0].lower()
        for category, table in _TAG_CATEGORIES:
            if prefix_lower in table:
                tags.add(category, table[prefix_lower])
                return True

    return False


def sanitize_for_path(name: str) -> str:
    name = ILLEGAL_CHARS_RE.sub("", name)
    name = re.sub(r" +", " ", name)
    return name.strip(". ")


def _classify_leading(tokens: list[str], tags: MediaTags) -> str:
    """Tokens before the anchor: matched tags are extracted; the remaining
    tokens, joined, are the title/show name."""
    leftover = [tok for tok in tokens if _classify_token(tok, tags) is False]
    return sanitize_for_path(" ".join(leftover))


def _classify_trailing(tokens: list[str], tags: MediaTags) -> str:
    """Tokens after a TV anchor: leading unclassified tokens are the episode
    title; the first recognized tag permanently switches every remaining
    token into tag-or-noise mode (a token never re-enters the title)."""
    title_tokens = []
    trailing = False
    for tok in tokens:
        result = _classify_token(tok, tags)
        if result is True:
            trailing = True
        elif result is False and not trailing:
            title_tokens.append(tok)
    return sanitize_for_path(" ".join(title_tokens))


def find_tv_anchor(tokens: list[str]) -> tuple[int, str, str, str] | None:
    """Returns (index, season, episode, episode_end) for the first whole-token
    SxxExx (optionally SxxExx-Eyy / SxxExxEyy) or legacy NxN match."""
    for i, tok in enumerate(tokens):
        m = TV_EP_RE.match(tok)
        if m:
            season, ep, ep_end = m.group(1), m.group(2), m.group(3)
            return (
                i,
                f"{int(season):02d}",
                f"{int(ep):02d}",
                f"{int(ep_end):02d}" if ep_end else "",
            )
    for i, tok in enumerate(tokens):
        m = TV_NXN_RE.match(tok)
        if m:
            return i, f"{int(m.group(1)):02d}", f"{int(m.group(2)):02d}", ""
    return None


def find_year_anchor(tokens: list[str]) -> int | None:
    """Returns the index of the best year token, preferring the rightmost
    candidate that leaves a non-empty title. Returns None if no year token
    qualifies (including "the only candidate would empty the title")."""
    candidates = [i for i, tok in enumerate(tokens) if YEAR_RE.match(_unwrap(tok))]
    for i in reversed(candidates):
        if i > 0:
            return i
    return None


def parse_tv(stem: str) -> tuple[str, str, str, str, str, MediaTags] | None:
    """Returns (show_name, season, episode, episode_end, episode_title, tags),
    or None if no TV episode marker is present."""
    tokens = tokenize(normalize_name(stem))
    anchor = find_tv_anchor(tokens)
    if anchor is None:
        return None
    idx, season, episode, episode_end = anchor

    tags = MediaTags()
    show_name = _classify_leading(tokens[:idx], tags)
    episode_title = _classify_trailing(tokens[idx + 1:], tags)
    return show_name, season, episode, episode_end, episode_title, tags


def parse_movie(stem: str) -> tuple[str, str, MediaTags]:
    """Returns (title, year, tags) — year may be ""."""
    tokens = tokenize(normalize_name(stem))
    tags = MediaTags()

    year_idx = find_year_anchor(tokens)
    if year_idx is not None:
        year = _unwrap(tokens[year_idx])
        title = _classify_leading(tokens[:year_idx], tags)
        for tok in tokens[year_idx + 1:]:
            _classify_token(tok, tags)
    else:
        year = ""
        title = _classify_leading(tokens, tags)

    return title, year, tags


def process_file(
    filepath: Path,
    src_dir: Path,
    augment_tags: Callable[[Path, MediaTags], None] | None = None,
) -> tuple[Path, str] | None:
    """Returns (target_dir, new_filename), or None if the file should be skipped.

    `augment_tags`, if given, is called with (filepath, tags) once the filename
    has been parsed but before the tag group is rendered — its job is to fill
    in tags the filename didn't specify (e.g. from ffprobe). It's injected
    rather than called directly so this module stays free of filesystem/subprocess I/O;
    cli.py owns the actual probing.
    """
    filename = filepath.name
    ext = filepath.suffix.lstrip(".").lower()
    stem = filepath.stem

    parsed_tv = parse_tv(stem)
    if parsed_tv is not None:
        show_name, season, episode, episode_end, episode_title, tags = parsed_tv
        if not show_name:
            warn(f"Could not determine show name from: {filename}")
            return None
        if augment_tags is not None:
            augment_tags(filepath, tags)
        ep_tag = f"S{season}E{episode}" + (f"-E{episode_end}" if episode_end else "")
        ep_title_part = f" {episode_title}" if episode_title else ""
        ver_suffix = f" - {tags.resolution}" if tags.resolution else ""
        new_filename = f"{show_name} {ep_tag}{ep_title_part}{tags.bracket()}{ver_suffix}.{ext}"
        target_dir = src_dir / show_name / f"Season {season}"
        return target_dir, new_filename

    title, year, tags = parse_movie(stem)
    if not title:
        warn(f"Could not determine movie title from: {filename}")
        return None
    if augment_tags is not None:
        augment_tags(filepath, tags)
    folder_name = f"{title} ({year})" if year else title
    ver_suffix = f" - {tags.resolution}" if tags.resolution else ""
    new_filename = f"{folder_name}{tags.bracket()}{ver_suffix}.{ext}"
    target_dir = src_dir / folder_name
    return target_dir, new_filename
