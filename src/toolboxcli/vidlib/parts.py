"""Finding videos that are parts of one (no I/O).

A part marker is read from a video's *title* (a badly named file's whole name is its title):

    Day 1 Part 2 · Day 1 - P2 · Part.2 - Day 1 · Day 1 (Ep 2) · Day 1 Part II · Day 1 2/3
    Day 1 Part 1-5 (a range: the next file starts at 6)

It counts only at the start or end of the title, or as a " - " segment of its own — never in
the middle of a sentence ("Part 2 of the plan is…" is a title). A bare trailing number
("Day 1 2") counts only for a set that includes a part 1 and shares the rest of the title,
or "Marketing Monday 112" and "… 113" would be parts.

Grouping ignores people and collection (crossovers and half-credited parts exist) and
compares what's left of the titles: equal after naming.loose_key is a *sure* group; most
words shared is a *maybe*. The user picks what to merge from either, so this errs towards
offering; `Group.gaps` and `Group.doubled` say what looks wrong.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from toolboxcli.vidlib.core import normalize
from toolboxcli.vidlib.naming import clean_title, loose_key

# ── Markers ──────────────────────────────────────────────────────────

_KEYWORD = (r"(?:part|pt|p|episode|ep|chapter|ch|volume|vol|cd|disc|disk|no|nr|#)")
_ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii",
          "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx"]
_WORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
          "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
          "eighteen", "nineteen", "twenty"]
_NUMBER = (r"(?:\d{1,3}|" + "|".join(sorted(_ROMAN, key=len, reverse=True)) + "|"
           + "|".join(_WORDS) + r")")
# "Part 2", "P2", "Ep.3", "#4", "Part II", "Part Two", "Part 1-5", "Pt 2 of 3"
_KEYWORD_MARKER = (rf"{_KEYWORD}\.?[\s_]*(?P<a>{_NUMBER})"
                   rf"(?:\s*(?:-|–|to)\s*(?P<b>{_NUMBER}))?"
                   rf"(?:\s*(?:/|of)\s*(?P<t>\d{{1,3}}))?")
# "2/3", "2 of 3"
_FRACTION_MARKER = r"(?P<fa>\d{1,3})\s*(?:/|of)\s*(?P<ft>\d{1,3})"
_MARKER = rf"(?:{_KEYWORD_MARKER}|{_FRACTION_MARKER})"
_OPEN, _CLOSE = r"[\(\[]?", r"[\)\]]?"
_EDGE = r"[\s\-–—|:,._]"

_WHOLE = re.compile(rf"^{_OPEN}\s*{_MARKER}\s*{_CLOSE}$", re.IGNORECASE)
_AT_END = re.compile(rf"(?:^|{_EDGE}+){_OPEN}\s*{_MARKER}\s*{_CLOSE}\s*$", re.IGNORECASE)
_AT_START = re.compile(rf"^\s*{_OPEN}\s*{_MARKER}\s*{_CLOSE}(?:{_EDGE}+|$)", re.IGNORECASE)
_SEGMENT_SPLIT = re.compile(r"\s+[-–—|]\s+")
_SENTENCE_GOES_ON = re.compile(
    r"\s*(?:of|the|a|an|is|are|was|in|on|and|to|for|with|from|at|by)\b", re.IGNORECASE)
_BARE = re.compile(r"^(?P<base>.*\D)\s+(?P<n>\d{1,2})$")


def _number(text: str) -> int:
    t = text.lower()
    if t.isdigit():
        return int(t)
    if t in _ROMAN:
        return _ROMAN.index(t) + 1
    return _WORDS.index(t) + 1


@dataclass(frozen=True)
class Marker:
    first: int
    last: int                 # = first, unless a range ("Part 1-5")
    total: int | None = None  # "2/3", "2 of 3"
    bare: bool = False        # a plain trailing number, which needs a set to count


@dataclass(frozen=True)
class PartTitle:
    base: str                 # the title without its marker, tidied
    key: str                  # loose_key(base): what parts of one video share
    marker: Marker


def _marker(m: re.Match[str]) -> Marker:
    if m.group("fa") is not None:
        return Marker(int(m.group("fa")), int(m.group("fa")), int(m.group("ft")))
    first = _number(m.group("a"))
    last = _number(m.group("b")) if m.group("b") else first
    total = int(m.group("t")) if m.group("t") else None
    return Marker(first, max(first, last), total)


def _tidy(text: str) -> str:
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—|:,._")
    return clean_title(text)


def parse_part(title: str) -> PartTitle | None:
    """The part marker in *title*, and the title without it; None when there isn't one
    (a bare trailing number is returned with `bare` set — it only counts within a set)."""
    text = title.replace("_", " ").strip()
    segments = _SEGMENT_SPLIT.split(text)
    if len(segments) > 1:
        for i, segment in enumerate(segments):
            m = _WHOLE.match(segment.strip())
            if m:
                base = " - ".join(s for j, s in enumerate(segments) if j != i)
                return _result(base, _marker(m))
    for pattern in (_AT_END, _AT_START):
        m = pattern.search(text)
        if m:
            base = text[:m.start()] + " " + text[m.end():]
            # A marker opening the title followed by a small word starts a sentence
            # ("Part 2 of the plan is…"), not a part.
            if pattern is _AT_START and _SENTENCE_GOES_ON.match(text[m.end():]):
                continue
            if _tidy(base):
                return _result(base, _marker(m))
    bare = _BARE.match(text)
    if bare and not re.search(r"\b(19|20)\d\d$", text):
        return _result(bare.group("base"), Marker(int(bare.group("n")),
                                                  int(bare.group("n")), bare=True))
    return None


def _result(base: str, marker: Marker) -> PartTitle | None:
    tidy = _tidy(base)
    key = loose_key(tidy)
    if not key:
        return None
    return PartTitle(tidy, key, marker)


# ── Grouping ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Video:
    """What grouping needs to know about one library video."""

    id: int
    title: str
    people: tuple[str, ...] = ()
    collection: str = ""


@dataclass(frozen=True)
class Member:
    video: Video
    part: PartTitle


@dataclass
class Group:
    members: list[Member]      # in part order
    sure: bool
    gaps: list[int] = field(default_factory=list[int])
    doubled: list[int] = field(default_factory=list[int])

    @property
    def ids(self) -> frozenset[int]:
        return frozenset(m.video.id for m in self.members)

    @property
    def title(self) -> str:
        """The shared title, as the first part writes it."""
        return self.members[0].part.base

    @property
    def people(self) -> tuple[str, ...]:
        """Everyone named in any part, in order of first appearance."""
        seen: dict[str, str] = {}
        for m in self.members:
            for p in m.video.people:
                seen.setdefault(normalize(p), p)
        return tuple(seen.values())

    @property
    def collection(self) -> str:
        """The collection most parts share; part 1's on a tie."""
        named = [m.video.collection for m in self.members if m.video.collection]
        if not named:
            return ""
        counts = Counter(normalize(c) for c in named)
        best = max(counts.values())
        return next(c for c in named if counts[normalize(c)] == best)


def _words(key_text: str) -> frozenset[str]:
    return frozenset(w for w in (loose_key(t) for t in key_text.split()) if w)


def _similar(a: frozenset[str], b: frozenset[str], threshold: float) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= threshold


def _check(members: list[Member]) -> tuple[list[int], list[int]]:
    covered: Counter[int] = Counter()
    for m in members:
        covered.update(range(m.part.marker.first, m.part.marker.last + 1))
    top = max(covered)
    gaps = [n for n in range(1, top + 1) if n not in covered]
    doubled = sorted(n for n, c in covered.items() if c > 1)
    return gaps, doubled


def _names_pattern(people: list[str]) -> re.Pattern[str] | None:
    names = sorted({p.strip() for p in people if p.strip()}, key=len, reverse=True)
    if not names:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in names) + r")(?:['’]s)?(?!\w)",
                      re.IGNORECASE)


def _without_names(part: PartTitle, pattern: re.Pattern[str] | None) -> PartTitle:
    """*part* compared without known people's names: a badly named file usually carries
    them in its title ("DougDoug lemonade stand finale (Ep 3)") where its named siblings
    have them in the People field. The base shown stays as written."""
    if pattern is None:
        return part
    key = loose_key(pattern.sub(" ", part.base))
    return PartTitle(part.base, key, part.marker) if key else part


def find_groups(videos: list[Video], similarity: float = 0.8,
                dismissed: list[frozenset[int]] | None = None,
                people: list[str] | None = None) -> list[Group]:
    """Sets of videos that look like parts of one, sure groups first.

    *people* are the library's known names, ignored when titles are compared. *dismissed*
    are sets the user said aren't parts: a group whose videos all lie within one of them is
    left out, so it only comes back if a new video joins it."""
    names = _names_pattern(people or [])
    parsed = [(v, _without_names(p, names)) for v in videos
              if (p := parse_part(v.title)) is not None]
    by_key: dict[str, list[Member]] = {}
    for video, part in parsed:
        by_key.setdefault(part.key, []).append(Member(video, part))

    # A bare number only counts in a set that has a part 1 and shares the rest of the title.
    for key, members in list(by_key.items()):
        if any(m.part.marker.bare for m in members):
            bare = [m for m in members if m.part.marker.bare]
            keep = [m for m in members if not m.part.marker.bare]
            numbers = {m.part.marker.first for m in bare}
            # Two copies of "Day 1" are one video twice, not parts: it takes two different
            # numbers, one of them 1.
            if len(numbers) >= 2 and 1 in numbers:
                keep += bare
            if keep:
                by_key[key] = keep
            else:
                del by_key[key]

    # Keys whose words mostly overlap form one "maybe" cluster (union-find over keys).
    keys = list(by_key)
    words = {k: _words(names.sub(" ", by_key[k][0].part.base) if names
                       else by_key[k][0].part.base) for k in keys}
    parent = {k: k for k in keys}

    def root(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if _similar(words[a], words[b], similarity):
                parent[root(a)] = root(b)
    clusters: dict[str, list[str]] = {}
    for k in keys:
        clusters.setdefault(root(k), []).append(k)

    groups: list[Group] = []
    for cluster in clusters.values():
        members = [m for k in cluster for m in by_key[k]]
        if len(members) < 2:
            continue
        members.sort(key=lambda m: (m.part.marker.first, m.part.marker.last, m.video.id))
        gaps, doubled = _check(members)
        group = Group(members, sure=len(cluster) == 1, gaps=gaps, doubled=doubled)
        if any(group.ids <= d for d in dismissed or []):
            continue
        groups.append(group)
    groups.sort(key=lambda g: (not g.sure, g.title.casefold()))
    return groups


def proposal(videos: list[Video]) -> tuple[tuple[str, ...], str, str]:
    """(people, title, collection) for merging *videos* in this order — the same rules as a
    found group, for a set picked by hand: everyone named, the most shared collection, and
    the first title with its marker taken off."""
    members: list[Member] = []
    for v in videos:
        part = parse_part(v.title) or PartTitle(_tidy(v.title) or v.title,
                                                loose_key(v.title), Marker(0, 0))
        members.append(Member(v, part))
    group = Group(members, sure=True)
    return group.people, group.title, group.collection
