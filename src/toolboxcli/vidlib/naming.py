"""Turning a pasted page title into a People - Title - Collection name (no I/O).

The title is split into *segments* on " - " (also a spaced en/em dash) and "|". Each segment
gets a role — people, title, collection or skip — first guessed from the names the library
already knows and from position, then corrected by the user:

    DougDoug - Atrioc - The Conspiracy Episode - Part 1 - Lemonade Stand | YouTube
    people     people   title                    title    collection       skip
    → DougDoug, Atrioc - The Conspiracy Episode - Part 1 - Lemonade Stand

People segments are split into names on "," "&" "and" "x" "+"; title segments are joined
with " - " in their original order (core.parse_name reads every middle field back as one
title); there is one collection. The order of segments in the pasted title doesn't matter:
roles decide where each piece goes.

Names are matched against a Vocabulary two ways. An *exact* match (case and spaces, even
spaces missing or added: "LemonadeStand" is "Lemonade Stand") rewrites the name to the
library's spelling. A *loose* match (also ignoring
punctuation, inner spaces and accents: "Doug-Doug" ~ "DougDoug") only counts for guessing
roles and offers the library's spelling as a suggestion — it's never applied silently.

The Vocabulary also holds the segments the user has skipped before (by loose key), so a
recurring "Official Video" or channel name is guessed skip next time (`skip_changes`).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from toolboxcli._common.pathsafe import sanitize_for_path
from toolboxcli.vidlib.core import FIELD_SEP, normalize, parse_name

Role = Literal["people", "title", "collection", "skip"]
ROLES: tuple[Role, ...] = ("people", "title", "collection", "skip")
MatchKind = Literal["exact", "loose", "new"]

# " - " and its typographic cousins split anywhere they stand alone; "|" splits anywhere.
# A dash inside a word (Rent-a-Girlfriend, Day-1) is never a separator.
_DASH_SPLIT = re.compile(r"\s+[-–—]+\s+")
_PEOPLE_SPLIT = re.compile(r"\s*(?:,|&|\+|\band\b|\bx\b|×)\s*", re.IGNORECASE)


# ── Segments ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Segment:
    text: str
    site: bool = False   # after the title's last "|": a site or channel name


def split_segments(title: str) -> list[Segment]:
    """The pasted title's segments, in order. Everything after the last "|" is marked site."""
    parts = title.split("|")
    segments: list[Segment] = []
    for i, part in enumerate(parts):
        site = len(parts) > 1 and i == len(parts) - 1
        for piece in _DASH_SPLIT.split(f" {part} "):
            text = re.sub(r"\s+", " ", piece).strip()
            if text:
                segments.append(Segment(text, site))
    return segments


def split_names(text: str) -> list[str]:
    """The people in a segment: "Alice, Bob and Carol x Dee" → [Alice, Bob, Carol, Dee]."""
    return [n.strip() for n in _PEOPLE_SPLIT.split(text) if n.strip()]


# ── Matching against the library ─────────────────────────────────────


def loose_key(text: str) -> str:
    """normalize() and then some: accents, punctuation and every space dropped."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", "", plain.casefold())


@dataclass(frozen=True)
class Match:
    kind: MatchKind
    spelling: str = ""   # the library's spelling, for exact and loose matches


def _spaceless(text: str) -> str:
    return re.sub(r"\s+", "", normalize(text))


class _Names:
    """One kind of name (people or collections): exact and loose lookups to a spelling."""

    def __init__(self, names: dict[str, str]) -> None:
        self.exact = names
        # Names that differ only in where (or whether) there are spaces are the same name:
        # "LemonadeStand" is the library's "Lemonade Stand" — applied, not asked about.
        self.spaced: dict[str, str | None] = {}
        for spelling in names.values():
            key = _spaceless(spelling)
            existing = self.spaced.get(key, spelling)
            self.spaced[key] = spelling if existing == spelling else None
        self.loose: dict[str, str | None] = {}
        for spelling in names.values():
            key = loose_key(spelling)
            if not key:
                continue
            # Two library spellings sharing a loose key make it ambiguous: suggest neither.
            existing = self.loose.get(key, spelling)
            self.loose[key] = spelling if existing == spelling else None

    def match(self, name: str) -> Match:
        spelling = self.exact.get(normalize(name)) or self.spaced.get(_spaceless(name))
        if spelling is not None:
            return Match("exact", spelling)
        loose = self.loose.get(loose_key(name))
        if loose is not None:
            return Match("loose", loose)
        return Match("new")

    def suggest(self, query: str, limit: int) -> list[str]:
        """Spellings containing *query* loosely, those starting with it first."""
        q = loose_key(query)
        if not q:
            return []
        hits = [s for s in self.exact.values() if q in loose_key(s)]
        hits.sort(key=lambda s: (not loose_key(s).startswith(q), s.casefold()))
        return hits[:limit]


@dataclass
class Vocabulary:
    """People and collections the library already uses: normalised key → spelling. And
    the segments the user has skipped before, by `loose_key`."""

    people: dict[str, str] = field(default_factory=dict[str, str])
    collections: dict[str, str] = field(default_factory=dict[str, str])
    skips: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        self._people = _Names(self.people)
        self._collections = _Names(self.collections)

    @classmethod
    def from_spellings(cls, people: list[str], collections: list[str],
                       skipped: list[str] | None = None) -> Vocabulary:
        """From names as written; the first spelling of each key wins."""
        p: dict[str, str] = {}
        c: dict[str, str] = {}
        for name in people:
            if name.strip():
                p.setdefault(normalize(name), name.strip())
        for name in collections:
            if name.strip():
                c.setdefault(normalize(name), name.strip())
        skips = frozenset(k for k in map(loose_key, skipped or []) if k)
        return cls(p, c, skips)

    def person(self, name: str) -> Match:
        return self._people.match(name)

    def collection(self, name: str) -> Match:
        return self._collections.match(name)

    def skipped(self, text: str) -> bool:
        """The user has skipped this segment before."""
        key = loose_key(text)
        return bool(key) and key in self.skips

    def suggest(self, kind: Literal["people", "collections"], query: str,
                limit: int = 8) -> list[str]:
        names = self._people if kind == "people" else self._collections
        return names.suggest(query, limit)


# ── Guessing roles ───────────────────────────────────────────────────


def _all_known_people(text: str, vocab: Vocabulary) -> bool:
    names = split_names(text)
    return bool(names) and all(vocab.person(n).kind != "new" for n in names)


def guess_roles(segments: list[Segment], vocab: Vocabulary | None = None) -> list[Role]:
    """A first guess at each segment's role, for the user to correct.

    - Site segments (after the last "|") are skipped.
    - A segment whose names are all known people (exactly or loosely) is people.
    - A segment that is a known collection is the collection — the last one, if several.
    - A segment the user has skipped before is skipped. Known people and collections come
      first: a creator's name skipped where it was a channel name is still their name.
    - What's left goes by position, among the segments not skipped: the first is people if no segment was recognised as
      people, the last is the collection if none was recognised and at least three
      segments remain to share out, everything else is title. A lone segment is title.
    """
    vocab = vocab or Vocabulary()
    roles: list[Role | None] = [("skip" if s.site else None) for s in segments]
    for i, seg in enumerate(segments):
        if roles[i] is None and _all_known_people(seg.text, vocab):
            roles[i] = "people"
    collections = [i for i, seg in enumerate(segments)
                   if roles[i] is None and vocab.collection(seg.text).kind != "new"]
    if collections:
        roles[collections[-1]] = "collection"
    for i, seg in enumerate(segments):
        if roles[i] is None and vocab.skipped(seg.text):
            roles[i] = "skip"

    rest = [i for i, r in enumerate(roles) if r is None]
    content = [i for i, r in enumerate(roles) if r != "skip"]
    if "people" not in roles and len(content) >= 2 and rest:
        roles[rest.pop(0)] = "people"
    if "collection" not in roles and len(content) >= 3 and len(rest) >= 2:
        roles[rest.pop()] = "collection"
    return [r if r is not None else "title" for r in roles]


def skip_changes(segments: list[Segment], guessed: list[Role],
                 chosen: list[Role]) -> tuple[list[str], list[str]]:
    """What to remember and forget from the roles a name was saved with, against the guess:
    a segment the user skipped that wasn't guessed skip is remembered; one guessed skip
    (remembered before) that the user gave another role is forgotten. Site segments (after
    the last "|") are skipped by position, not by choice, so they're never remembered."""
    remember: list[str] = []
    forget: list[str] = []
    for seg, guess, role in zip(segments, guessed, chosen, strict=True):
        if seg.site:
            continue
        if role == "skip" and guess != "skip":
            remember.append(seg.text)
        elif role != "skip" and guess == "skip":
            forget.append(seg.text)
    return remember, forget


def set_role(roles: list[Role], index: int, role: Role) -> list[Role]:
    """*roles* with segment *index* changed to *role*. There's only one collection: choosing
    a new one turns the previous one into title."""
    updated: list[Role] = list(roles)
    if role == "collection":
        updated = [("title" if r == "collection" else r) for r in updated]
    updated[index] = role
    return updated


# ── Building the name ────────────────────────────────────────────────


def clean_field(text: str) -> str:
    """Safe inside one field of People - Title - Collection: no path characters, no " - ".
    A slash reads as a dash ("Part 2/3" → "Part 2-3") rather than vanishing."""
    text = sanitize_for_path(re.sub(r"\s*[/\\]\s*", "-", text))
    return re.sub(r"\s+[-–—]+\s+", " ", text).strip(" -")


def clean_title(text: str) -> str:
    """A title may hold " - " (several title segments); each part is cleaned on its own."""
    parts = (clean_field(p) for p in _DASH_SPLIT.split(f" {text} "))
    return FIELD_SEP.join(p for p in parts if p)


@dataclass(frozen=True)
class NamedPart:
    """A person or the collection as it'll be written, and how it matched the library."""

    text: str
    match: MatchKind = "new"
    suggestion: str = ""   # the library's spelling, offered for a loose match


@dataclass(frozen=True)
class Draft:
    people: tuple[NamedPart, ...]
    title: str
    collection: NamedPart | None

    @property
    def stem(self) -> str:
        """The filename without extension: People - Title - Collection, missing parts left out."""
        parts = [", ".join(p.text for p in self.people), self.title,
                 self.collection.text if self.collection else ""]
        return FIELD_SEP.join(p for p in parts if p)

    def problems(self) -> list[str]:
        """Why vidlib couldn't sort this name; empty when it can."""
        missing = [label for label, value in (
            ("people", self.people), ("title", self.title), ("collection", self.collection),
        ) if not value]
        if missing:
            return [f"no {', '.join(missing)} — vidlib won't be able to sort it"]
        assert self.collection is not None
        parsed = parse_name(self.stem)
        if (not parsed.complete or tuple(parsed.people) != tuple(p.text for p in self.people)
                or parsed.title != self.title or parsed.collection != self.collection.text):
            return ["vidlib would read this name differently"]
        return []

    @property
    def complete(self) -> bool:
        return not self.problems()


def _named(text: str, match: Match) -> NamedPart:
    if match.kind == "exact":
        return NamedPart(match.spelling, "exact")
    if match.kind == "loose":
        return NamedPart(text, "loose", match.spelling)
    return NamedPart(text, "new")


def _people_parts(names: list[str], vocab: Vocabulary) -> tuple[NamedPart, ...]:
    parts: list[NamedPart] = []
    seen: set[str] = set()
    for raw in names:
        name = clean_field(raw)
        if not name:
            continue
        part = _named(name, vocab.person(name))
        if normalize(part.text) not in seen:
            seen.add(normalize(part.text))
            parts.append(part)
    return tuple(parts)


def _collection_part(text: str, vocab: Vocabulary) -> NamedPart | None:
    name = clean_field(text)
    return _named(name, vocab.collection(name)) if name else None


def compose(segments: list[Segment], roles: list[Role],
            vocab: Vocabulary | None = None) -> Draft:
    """The name the segments' roles describe."""
    vocab = vocab or Vocabulary()
    people: list[str] = []
    titles: list[str] = []
    collection = ""
    for seg, role in zip(segments, roles, strict=True):
        if role == "people":
            people.extend(split_names(seg.text))
        elif role == "title":
            titles.append(seg.text)
        elif role == "collection":
            collection = seg.text   # set_role keeps it to one; the last wins regardless
    return Draft(
        people=_people_parts(people, vocab),
        title=clean_title(FIELD_SEP.join(titles)),
        collection=_collection_part(collection, vocab),
    )


UNKNOWN = "Unknown"


def fill_unknown(people: str, title: str, collection: str) -> tuple[str, str, str]:
    """Each blank field as "Unknown" — the user's own fill-in for a part they can't name
    yet, which still lets the video be sorted. Renaming applies it; a download doesn't."""
    def fill(field: str) -> str:
        return field if field.strip() else UNKNOWN
    return fill(people), fill(title), fill(collection)


def from_fields(people: str, title: str, collection: str,
                vocab: Vocabulary | None = None) -> Draft:
    """The name from hand-edited fields (people separated as split_names reads them)."""
    vocab = vocab or Vocabulary()
    return Draft(
        people=_people_parts(split_names(people), vocab),
        title=clean_title(title),
        collection=_collection_part(collection, vocab),
    )


# ── Names hiding in a title ──────────────────────────────────────────

# What can be left dangling beside a name that's taken out of a title: a field separator
# (kept, once, if both sides had one) or a connector ("Alice & Bob", "with Carol" is left).
_JOIN = r"(?:[-–—|·:]+|,|&|\+|×|\band\b|\bx\b|\bwith\b|\bfeat\b\.?|\bft\b\.?|\bfeaturing\b)"
_EDGE_SEP = re.compile(rf"(?:\s*{_JOIN})+\s*$", re.IGNORECASE)
_LEAD_SEP = re.compile(rf"^\s*(?:{_JOIN}\s*)+", re.IGNORECASE)
_FIELD_MARK = re.compile(r"[-–—|·:]")
_WORD = re.compile(r"[^\W_][\w'’.]*", re.UNICODE)
_MAX_NAME_WORDS = 6


@dataclass(frozen=True)
class Found:
    """A known person or collection spotted in a title, and the title without it."""

    kind: Literal["people", "collection"]
    name: str            # the library's spelling
    title_without: str


def _without(title: str, start: int, end: int) -> str:
    """*title* with [start:end) taken out, tidying the separators around the gap."""
    left, right = title[:start], title[end:]
    left_mark = _EDGE_SEP.search(left)
    right_mark = _LEAD_SEP.search(right)
    # "Day 1 with Alice - Lakes" → "Day 1 - Lakes": a field separator on either side stays.
    kept_sep = bool((left_mark and _FIELD_MARK.search(left_mark.group()))
                    or (right_mark and _FIELD_MARK.search(right_mark.group())))
    left = left[:left_mark.start()] if left_mark else left
    right = right[right_mark.end():] if right_mark else right
    left, right = left.strip(), right.strip()
    joiner = FIELD_SEP if kept_sep and left and right else " "
    text = joiner.join(part for part in (left, right) if part)
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    return re.sub(r"\s+", " ", text).strip(" -–—|,&:")


def _spans(title: str, lookup: Callable[[str], Match]) -> list[tuple[int, int, str]]:
    """(start, end, spelling) of every run of whole words in *title* that is one of
    *names*, exactly or loosely; longest first, never overlapping."""
    words = list(_WORD.finditer(title))
    taken: set[int] = set()
    found: list[tuple[int, int, str]] = []
    for size in range(min(_MAX_NAME_WORDS, len(words)), 0, -1):
        for i in range(len(words) - size + 1):
            if taken & set(range(i, i + size)):
                continue
            start, end = words[i].start(), words[i + size - 1].end()
            text = title[start:end].rstrip(".'’")
            match = lookup(re.sub(r"['’]s$", "", text, flags=re.IGNORECASE))
            if match.kind != "new":
                found.append((start, start + len(text), match.spelling))
                taken |= set(range(i, i + size))
    return sorted(found)


def find_in_title(title: str, vocab: Vocabulary, have_people: list[str] | None = None,
                  have_collection: str = "") -> list[Found]:
    """Known people and collections mentioned in *title*, as suggestions: a badly named file
    often has everything in its title. Names already in the People or Collection field are
    left out. Each comes with the title it leaves once taken out of it."""
    have = {normalize(p) for p in have_people or []}
    results: list[Found] = []
    seen: set[tuple[str, str]] = set()
    for kind, lookup in (("people", vocab.person), ("collection", vocab.collection)):
        spans = _spans(title, lookup)
        # A name mentioned twice: people usually lead a title and the collection ends it
        # ("… to the lakes (Lakes)"), so that's the mention to take out.
        if kind == "collection":
            spans.reverse()
        for start, end, spelling in spans:
            key = (kind, normalize(spelling))
            if key in seen:
                continue
            if kind == "people" and normalize(spelling) in have:
                continue
            if kind == "collection" and normalize(spelling) == normalize(have_collection):
                continue
            seen.add(key)
            results.append(Found("people" if kind == "people" else "collection", spelling,
                                 clean_title(_without(title, start, end))))
    return sorted(results, key=lambda f: (f.kind != "people", title.casefold().find(
        f.name.casefold())))
