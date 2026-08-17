"""Filename parsing/classification for kavitaname (no filesystem I/O).

Mirrors the tokenize-then-classify approach in jellyname/core.py, but the
vocabulary here comes from Kavita's own scanner
(Kavita.Services/Scanner/Parser.cs) rather than from release-group conventions.

Two things are worth knowing before touching the anchor patterns:

* Kavita's *only* trigger for a special is the ``SP\\d+`` filename marker
  (``Parser.IsSpecial`` -> ``HasSpecialMarker``). Keywords like "Omake" carry no
  weight on their own, which is why emitting an explicit SP marker is the whole
  point of the specials path below.
* Kavita re-parses whatever we write. So the output vocabulary is deliberately
  narrow (``c001``, ``(v01)``, ``SP01``) even though the *input* vocabulary is
  wide — every form produced here was checked back through Kavita's real
  MangaVolumeRegex/MangaChapterRegex.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from toolboxcli._common.pathsafe import sanitize_for_path

# Kavita's ArchiveFileExtensions, minus the ones it lists but nobody ships
# manga in. Book covers LightNovel too — Kavita parses both with the manga
# regexes, so they share this module's parser entirely.
ARCHIVE_EXTS = {"cbz", "cbr", "cb7", "cbt", "zip", "rar", "7z"}
BOOK_EXTS = {"epub", "pdf"}
IMAGE_EXTS = {"png", "jpeg", "jpg", "webp", "gif", "avif"}

EXTS_FOR_KIND = {
    "manga": ARCHIVE_EXTS,
    "manhwa": ARCHIVE_EXTS,
    "book": BOOK_EXTS,
}

# Kavita's CoverImageRegex, in the narrow form that actually matters here: we
# only need to *avoid renaming* a cover, not classify one. "backcover" is
# deliberately not a cover to Kavita, so it isn't one here either.
COVER_STEMS = {"cover", "!cover", "folder"}

SPECIALS_DIR = "Specials"


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------

# Underscores are separators in every scanlator convention Kavita handles
# (``Kenichi_v11_c90-98``); Kavita's own ReplaceUnderscores does the same.
# Dots are *not* touched here, unlike jellyname: a dot is significant in
# ``c012.5`` and in ``Vol.4``, and manga filenames don't use dot-separation the
# way scene video releases do.
def normalize_name(raw: str) -> str:
    name = raw.replace("_", " ")
    name = re.sub(r"\s{2,}", " ", name)
    return name.strip()


_TOKEN_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)|\S+")


def tokenize(norm: str) -> list[str]:
    """Splits on whitespace, keeping bracket/paren groups as single tokens.

    Standalone "-" tokens are *kept*, unlike jellyname, because a dash is part
    of the series name often enough to matter here: "86 - Eighty Six" and
    "Goblin Slayer Side Story - Year One" both lose their identity without it.
    No anchor pattern matches a bare dash, so keeping it costs nothing.
    """
    return [t for t in _TOKEN_RE.findall(norm) if t]


def _is_wrapped(token: str) -> bool:
    return len(token) >= 2 and token[0] in "[(" and token[-1] in "])"


def _unwrap(token: str) -> str:
    return token[1:-1] if _is_wrapped(token) else token


# ---------------------------------------------------------------------------
# number grammar
# ---------------------------------------------------------------------------

# Kavita's NumberRange, with unbounded decimal places. Ranges and decimals are
# one grammar because Kavita's FormatValue treats them as one value too.
_NUM = r"\d+(?:\.\d+)?"
_NUM_RANGE = rf"{_NUM}(?:-(?:c|ch|v|vol)?\.?{_NUM})?"

# Markers, longest-first within each alternation so "chapter" wins over "ch"
# and "volume" over "vol" over "v".
_VOL_WORDS = r"volume|vol|tome|v|t"
_CH_WORDS = r"chapters|chapter|chps|chp|chs|ch|c"

# Latin markers glued to (or separated from) their number: v01, Vol. 4, tome 1.
#
# The trailing `(?(rng).*|)$` mirrors the conditional in Kavita's own chapter
# regex: once a *range* has matched, whatever follows is allowed to be junk
# ("c001-006x1" is chapter 1-6 with a scanlator version suffix). Without a
# range the token must end cleanly, which is what stops "c1fi7"-style ripper
# tags from being read as chapter 1.
_RANGE_TAIL = rf"(?P<rng>-(?:c|ch|v|vol)?\.?{_NUM})?"
VOL_TOKEN_RE = re.compile(
    rf"^(?:{_VOL_WORDS})\.?\s*(?P<num>{_NUM}{_RANGE_TAIL})(?(rng).*|)$", re.IGNORECASE
)
CH_TOKEN_RE = re.compile(
    rf"^(?:{_CH_WORDS})\.?\s*(?P<num>{_NUM}{_RANGE_TAIL})(?(rng).*|)$", re.IGNORECASE
)
# "Tower Of God S01 014" — Kavita reads a bare S-marker as a volume/season.
SEASON_TOKEN_RE = re.compile(rf"^s(?P<num>{_NUM_RANGE})$", re.IGNORECASE)
# "#02" as an issue/chapter marker.
HASH_TOKEN_RE = re.compile(rf"^#(?P<num>{_NUM_RANGE})$")
BARE_NUM_RE = re.compile(rf"^(?P<num>{_NUM_RANGE})$")

# A marker whose number lives in the *next* token: "Vol. 4", "Chapter 27".
VOL_WORD_ONLY_RE = re.compile(rf"^(?:{_VOL_WORDS})\.?$", re.IGNORECASE)
CH_WORD_ONLY_RE = re.compile(rf"^(?:{_CH_WORDS})\.?$", re.IGNORECASE)

# Non-Latin markers, ported from Kavita's MangaVolumeRegex/MangaChapterRegex.
# These attach directly to digits with no separator, so they're searched inside
# a token rather than matched against the whole of it.
CJK_VOL_RES = [
    re.compile(rf"第(?P<num>{_NUM})[卷册]"),          # Chinese: 第03卷
    re.compile(rf"[卷册](?P<num>{_NUM})"),             # Chinese: 卷3
    re.compile(rf"(?P<num>{_NUM})巻"),                 # Japanese: 3巻
    re.compile(rf"제?(?P<num>{_NUM})[권장]"),          # Korean: 제3권
    re.compile(rf"시즌(?P<num>{_NUM})"),               # Korean season: 시즌2
    re.compile(rf"(?:เล่มที่|เล่ม)\s?\.?\s?(?P<num>{_NUM})"),  # Thai
    re.compile(rf"Тома?\.?\s?(?P<num>{_NUM})", re.IGNORECASE),  # Russian
]
CJK_CH_RES = [
    re.compile(rf"第(?P<num>{_NUM})[话話]"),           # Chinese/Japanese: 第25话
    re.compile(rf"(?P<num>{_NUM})[话話]"),
    re.compile(rf"제?(?P<num>{_NUM})[화회]"),          # Korean: 제7화
    re.compile(rf"(?:บทที่|ตอนที่)\s?\.?\s?(?P<num>{_NUM})"),  # Thai
    re.compile(rf"Глав[аы]\.?\s?(?P<num>{_NUM})", re.IGNORECASE),  # Russian
]

# Glued forms a token-level match would miss because the series name runs into
# the marker without a space: "Kenichi_v11_c90-98" survives normalization as
# "Kenichi v11 c90-98" (fine), but "SeriesName-c012" does not.
GLUED_VOL_RE = re.compile(rf"(?:^|[\s\-])(?:{_VOL_WORDS})\.?\s*(?P<num>{_NUM_RANGE})(?![a-z0-9])", re.IGNORECASE)
GLUED_CH_RE = re.compile(rf"(?:^|[\s\-])(?:{_CH_WORDS})\.?\s*(?P<num>{_NUM_RANGE})(?![a-z0-9])", re.IGNORECASE)

SPECIAL_MARKER_RE = re.compile(r"^sp(?P<num>\d+)$", re.IGNORECASE)
SPECIAL_MARKER_SEARCH_RE = re.compile(r"\bSP(?P<num>\d+)\b", re.IGNORECASE)

PROLOGUE_RE = re.compile(rf"^prologue\.?\s*(?P<num>{_NUM})?$", re.IGNORECASE)

# Kavita's FormatTagSpecialKeywords, plus the manga-side terms it recognizes
# elsewhere (Omake, Extra, Side Story). Matched as whole normalized phrases.
SPECIAL_KEYWORDS = {
    "special", "specials", "reference", "director's cut", "directors cut",
    "box set", "box-set", "boxset", "annual", "anthology", "epilogue",
    "one shot", "one-shot", "oneshot", "prologue", "tpb", "trade paper back",
    "omnibus", "compendium", "absolute", "graphic novel", "gn", "fcbd",
    "giant size", "omake", "omakes", "extra", "extras", "bonus", "side story",
    "side stories", "interlude", "artbook", "art book", "databook",
    "data book", "fanbook", "character book", "short story", "afterword",
}
_MAX_KEYWORD_WORDS = max(len(k.split()) for k in SPECIAL_KEYWORDS)

# ---------------------------------------------------------------------------
# duplicate-marker guard
# ---------------------------------------------------------------------------

# Ported from Kavita's RemoveDuplicateVolumeIfExists / RemoveDuplicateChapterIfExists.
# "One Piece - Vol 4 ch 2 - vol 6 omakes" must yield volume 4, not 6: the
# trailing marker describes the *contents*, not the file. Ranges written as
# "v1-v2" / "c1-c4" are exempt, since those are one value, not two markers.
_DUP_VOL_RE = re.compile(rf"(?:{_VOL_WORDS})\.?[\s_]*\d+.*?(?:{_VOL_WORDS})\.?[\s_]*\d+", re.IGNORECASE)
_DUP_CH_RE = re.compile(rf"(?:{_CH_WORDS})\.?[\s_]*\d+.*?(?:{_CH_WORDS})\.?[\s_]*\d+", re.IGNORECASE)
_RANGE_VOL_RE = re.compile(r"(?:vol|v)\.?[\s_]?\d+(?:\.\d+)?-(?:vol|v)\.?[\s_]?\d+", re.IGNORECASE)
_RANGE_CH_RE = re.compile(r"(?:ch|c)\.?[\s_]?\d+(?:\.\d+)?-(?:ch|c)\.?[\s_]?\d+", re.IGNORECASE)


def _truncate_at_duplicate(name: str, dup_re: re.Pattern, range_re: re.Pattern, marker_re: re.Pattern) -> str:
    if range_re.search(name):
        return name
    if not dup_re.search(name):
        return name
    first = marker_re.search(name)
    if first is None:
        return name
    second = marker_re.search(name, first.end())
    if second is None:
        return name
    return name[: second.start()].rstrip(" -_")


def drop_duplicate_markers(name: str) -> str:
    name = _truncate_at_duplicate(name, _DUP_VOL_RE, _RANGE_VOL_RE, GLUED_VOL_RE)
    name = _truncate_at_duplicate(name, _DUP_CH_RE, _RANGE_CH_RE, GLUED_CH_RE)
    return name


# ---------------------------------------------------------------------------
# parsed result
# ---------------------------------------------------------------------------


@dataclass
class ChapterInfo:
    """What a filename turned out to describe. Empty string means "unknown"."""

    series: str = ""
    volume: str = ""
    chapter: str = ""
    is_special: bool = False
    special_index: int = 0
    special_title: str = ""
    # Trailing text like "Omake" on a file that *is* numbered, so isn't a
    # special. Kept verbatim in the output: dropping it would rename
    # "One Piece v01 Omake" onto "One Piece v01" and collide with the real
    # volume archive.
    descriptor: str = ""

    def has_number(self) -> bool:
        return bool(self.volume or self.chapter)


def _format_number(value: str, width: int) -> str:
    """Zero-pads each side of a possibly-decimal, possibly-range number.

    ``12`` -> ``012``, ``12.5`` -> ``012.5``, ``1-6`` -> ``001-006``. Kavita's
    own PadZeros does the same for display; doing it in the filename is what
    makes a directory listing sort correctly past 100 chapters.
    """

    def pad_one(part: str) -> str:
        # A range's right-hand side may carry a redundant marker (c001-c006);
        # Kavita's FormatValue strips it, so it never reaches the output.
        part = re.sub(r"^(?:c|ch|v|vol)\.?", "", part, flags=re.IGNORECASE)
        whole, _, frac = part.partition(".")
        padded = whole.zfill(width) if whole.isdigit() else whole
        return f"{padded}.{frac}" if frac else padded

    return "-".join(pad_one(p) for p in value.split("-"))


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def _match_any(token: str, patterns: list[re.Pattern]) -> str:
    for pattern in patterns:
        m = pattern.search(token)
        if m:
            return m.group("num")
    return ""


def _find_special_keyword(tokens: list[str], start: int) -> tuple[int, str]:
    """Finds the earliest special keyword at or after `start`.

    Returns (token index, matched phrase), or (-1, "") if none. Multi-word
    keywords ("side story") are matched across consecutive tokens, longest
    phrase first, so "Side Story" doesn't half-match "story".
    """
    for i in range(start, len(tokens)):
        for span in range(min(_MAX_KEYWORD_WORDS, len(tokens) - i), 0, -1):
            phrase = " ".join(_unwrap(t) for t in tokens[i : i + span])
            if phrase.lower().strip(".,-") in SPECIAL_KEYWORDS:
                return i, phrase
    return -1, ""


def _clean_series(tokens: list[str]) -> str:
    """Joins the tokens preceding the anchor into a series name.

    Bracket/paren groups are dropped wholesale — that's exactly what Kavita's
    CleanupRegex does, and it's where release noise ("(Digital)", "[LuCaZ]")
    actually lives. Bare words are deliberately *never* filtered, however
    noise-like they look: "True Beauty", "Final Fantasy Lost Stranger" and
    "Raw Hero" are real series, and a stopword list would silently mangle them.
    Everything unwrapped survives verbatim, so "86 - Eighty Six" and
    "Rent-a-Girlfriend" come through intact.
    """
    kept = [tok for tok in tokens if not _is_wrapped(tok)]
    return sanitize_for_path(" ".join(kept).strip(" -_,"))


def parse_stem(stem: str) -> ChapterInfo:
    """Parses a filename stem into a ChapterInfo. Never returns None; an
    unparseable stem yields a ChapterInfo with no volume/chapter set."""
    info = ChapterInfo()
    norm = drop_duplicate_markers(normalize_name(stem))
    tokens = tokenize(norm)

    # An explicit SP marker outranks everything, exactly as it does in Kavita's
    # BasicParser: it forces IsSpecial and clears volume/chapter outright.
    for i, tok in enumerate(tokens):
        m = SPECIAL_MARKER_RE.match(_unwrap(tok)) or SPECIAL_MARKER_SEARCH_RE.search(tok)
        if m:
            info.is_special = True
            info.special_index = int(m.group("num"))
            info.series = _clean_series(tokens[:i])
            info.special_title = _special_title(tokens, i + 1)
            return info

    vol_idx = ch_idx = -1
    # Indices already spoken for, so a "Vol. 10" pair can't have its "10" read
    # a second time by the bare-number fallback below.
    consumed: set[int] = set()

    for i, tok in enumerate(tokens):
        inner = _unwrap(tok)

        if info.chapter == "":
            m = CH_TOKEN_RE.match(inner) or HASH_TOKEN_RE.match(inner)
            if m:
                info.chapter, ch_idx = m.group("num"), i
                consumed.add(i)
                continue
            if CH_WORD_ONLY_RE.match(inner) and i + 1 < len(tokens):
                nxt = BARE_NUM_RE.match(_unwrap(tokens[i + 1]))
                if nxt:
                    info.chapter, ch_idx = nxt.group("num"), i
                    consumed.update((i, i + 1))
                    continue

        if info.volume == "":
            m = VOL_TOKEN_RE.match(inner) or SEASON_TOKEN_RE.match(inner)
            if m:
                info.volume, vol_idx = m.group("num"), i
                consumed.add(i)
                continue
            if VOL_WORD_ONLY_RE.match(inner) and i + 1 < len(tokens):
                nxt = BARE_NUM_RE.match(_unwrap(tokens[i + 1]))
                if nxt:
                    info.volume, vol_idx = nxt.group("num"), i
                    consumed.update((i, i + 1))
                    continue

        if info.chapter == "":
            num = _match_any(inner, CJK_CH_RES)
            if num:
                info.chapter, ch_idx = num, i
                consumed.add(i)
                continue
        if info.volume == "":
            num = _match_any(inner, CJK_VOL_RES)
            if num:
                info.volume, vol_idx = num, i
                consumed.add(i)
                continue

    # Prologue: "Prologue 2" -> chapter 0.2, bare "Prologue" -> chapter 0.
    # Kept as a chapter rather than promoted to a special because that's what
    # this tool has always produced, and c000.N sorts ahead of chapter 1. The
    # two-token form is tried first — "Prologue" alone matches the one-token
    # pattern too, and checking it first would swallow the number.
    if not info.has_number():
        for i, tok in enumerate(tokens):
            inner = _unwrap(tok)
            if not PROLOGUE_RE.match(inner):
                continue
            num = PROLOGUE_RE.match(inner).group("num")
            if num is None and i + 1 < len(tokens):
                nxt = BARE_NUM_RE.match(_unwrap(tokens[i + 1]))
                if nxt:
                    num = nxt.group("num")
                    consumed.add(i + 1)
            info.chapter = f"0.{num}" if num else "0"
            ch_idx = i
            consumed.add(i)
            break

    # A bare number as the chapter. Runs both when nothing at all was found
    # ("Hinowa ga CRUSH! 018") and when only a volume was found
    # ("Tower Of God S01 014"), which Kavita reads as volume 1 chapter 14.
    # Never the first token — that would swallow a series that simply starts
    # with a number ("86 - Eighty Six").
    if not info.chapter:
        for i in range(len(tokens) - 1, 0, -1):
            if i in consumed or i < vol_idx or _is_wrapped(tokens[i]):
                continue
            m = BARE_NUM_RE.match(tokens[i])
            if m:
                info.chapter, ch_idx = m.group("num"), i
                break

    anchor = min(x for x in (vol_idx, ch_idx) if x >= 0) if (vol_idx >= 0 or ch_idx >= 0) else len(tokens)
    info.series = _clean_series(tokens[:anchor])

    # A special keyword only counts when nothing numbered it. This guard is
    # Kavita's (BasicParser: IsDefaultChapter && IsLooseLeafVolume && isSpecial)
    # and it is load-bearing: "v20 c171-180 Omake" is a chapter, not a special.
    if not info.has_number():
        kw_idx, _ = _find_special_keyword(tokens, 0)
        if kw_idx >= 0:
            info.is_special = True
            info.series = _clean_series(tokens[:kw_idx])
            info.special_title = _special_title(tokens, kw_idx)
    else:
        # Numbered, but a keyword trails the number ("v01 Omake"). Kavita reads
        # this as volume 1 and so do we — but the word is kept as a descriptor
        # so the name stays distinct from the plain volume file. Only text
        # *after* the anchor qualifies; "Goblin Slayer Side Story - Year One
        # 025.5" has its keyword in the series name, where it belongs.
        kw_idx, _ = _find_special_keyword(tokens, max(vol_idx, ch_idx) + 1)
        if kw_idx >= 0:
            info.descriptor = _special_title(tokens, kw_idx)

    return info


def _special_title(tokens: list[str], start: int) -> str:
    """The human-readable remainder of a special's filename."""
    kept = [t for t in tokens[start:] if not _is_wrapped(t)]
    return sanitize_for_path(" ".join(kept).strip(" -_,"))


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def build_name(info: ChapterInfo, series: str, ext: str) -> str | None:
    """Renders the Kavita-facing filename, or None if there's nothing to say.

    Output vocabulary (each verified back through Kavita's own regexes):
        chapter + volume   Series c001 (v01).cbz
        chapter only       Series c001.cbz
        volume only        Series v01.cbz
        special            Series SP01 - Title.cbz
    """
    if info.is_special:
        idx = f"SP{info.special_index:02d}"
        title = f" - {info.special_title}" if info.special_title else ""
        return f"{series} {idx}{title}.{ext}"

    suffix = f" - {info.descriptor}" if info.descriptor else ""

    if info.chapter:
        chapter = _format_number(info.chapter, 3)
        if info.volume:
            return f"{series} c{chapter} (v{_format_number(info.volume, 2)}){suffix}.{ext}"
        return f"{series} c{chapter}{suffix}.{ext}"

    if info.volume:
        return f"{series} v{_format_number(info.volume, 2)}{suffix}.{ext}"

    return None


def is_cover_image(name: str) -> bool:
    """Kavita treats these as the series cover, not a readable file."""
    path = Path(name)
    return path.suffix.lstrip(".").lower() in IMAGE_EXTS and path.stem.lower() in COVER_STEMS


def process_file(
    filepath: Path,
    series: str,
    kind: str,
    volume_map: dict[int, int] | None = None,
    augment: Callable[[Path, ChapterInfo], None] | None = None,
    special_index: Callable[[], int] | None = None,
) -> tuple[Path, str] | None:
    """Returns (target_dir, new_filename), or None if the file can't be named.

    `augment`, if given, is called with (filepath, info) after the filename has
    been parsed but before the name is rendered — its job is to fill in fields
    the filename didn't state (from ComicInfo.xml). It's injected rather than
    called directly so this module stays free of filesystem I/O; cli.py owns
    the archive reading, exactly as it owns ffprobe in jellyname.

    `special_index` supplies the next SP number for a special that didn't carry
    one; the caller owns that counter so numbering is stable across a run.
    """
    ext = filepath.suffix.lstrip(".").lower()
    info = parse_stem(filepath.stem)

    if augment is not None:
        augment(filepath, info)

    if info.is_special:
        if info.special_index == 0:
            info.special_index = special_index() if special_index else 1
        target_dir = filepath.parent
        # Already inside a Specials/ folder — Kavita reads the series name from
        # that folder's parent, so don't nest a second one.
        if target_dir.name.lower() != SPECIALS_DIR.lower():
            target_dir = target_dir / SPECIALS_DIR
        name = build_name(info, series, ext)
        return (target_dir, name) if name else None

    # A volume map only ever fills a gap — a volume stated in the filename or
    # in ComicInfo.xml is authoritative and is never overridden.
    if kind == "manga" and volume_map and info.chapter and not info.volume:
        whole = info.chapter.split("-", 1)[0].split(".", 1)[0]
        if whole.isdigit():
            mapped = volume_map.get(int(whole))
            if mapped is not None:
                info.volume = str(mapped)

    name = build_name(info, series, ext)
    return (filepath.parent, name) if name else None
