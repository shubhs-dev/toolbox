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

Names are matched against a Vocabulary two ways. An *exact* match (core.normalize: case and
spacing) rewrites the name to the library's spelling. A *loose* match (also ignoring
punctuation, inner spaces and accents: "Doug-Doug" ~ "DougDoug") only counts for guessing
roles and offers the library's spelling as a suggestion — it's never applied silently.
"""

from __future__ import annotations

import re
import unicodedata
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


class _Names:
    """One kind of name (people or collections): exact and loose lookups to a spelling."""

    def __init__(self, names: dict[str, str]) -> None:
        self.exact = names
        self.loose: dict[str, str | None] = {}
        for spelling in names.values():
            key = loose_key(spelling)
            if not key:
                continue
            # Two library spellings sharing a loose key make it ambiguous: suggest neither.
            existing = self.loose.get(key, spelling)
            self.loose[key] = spelling if existing == spelling else None

    def match(self, name: str) -> Match:
        spelling = self.exact.get(normalize(name))
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
    """People and collections the library already uses: normalised key → spelling."""

    people: dict[str, str] = field(default_factory=dict[str, str])
    collections: dict[str, str] = field(default_factory=dict[str, str])

    def __post_init__(self) -> None:
        self._people = _Names(self.people)
        self._collections = _Names(self.collections)

    @classmethod
    def from_spellings(cls, people: list[str], collections: list[str]) -> Vocabulary:
        """From names as written; the first spelling of each key wins."""
        p: dict[str, str] = {}
        c: dict[str, str] = {}
        for name in people:
            if name.strip():
                p.setdefault(normalize(name), name.strip())
        for name in collections:
            if name.strip():
                c.setdefault(normalize(name), name.strip())
        return cls(p, c)

    def person(self, name: str) -> Match:
        return self._people.match(name)

    def collection(self, name: str) -> Match:
        return self._collections.match(name)

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
    - What's left goes by position: the first is people if no segment was recognised as
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

    rest = [i for i, r in enumerate(roles) if r is None]
    content = [i for i, s in enumerate(segments) if not s.site]
    if "people" not in roles and len(content) >= 2 and rest:
        roles[rest.pop(0)] = "people"
    if "collection" not in roles and len(content) >= 3 and len(rest) >= 2:
        roles[rest.pop()] = "collection"
    return [r if r is not None else "title" for r in roles]


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


def from_fields(people: str, title: str, collection: str,
                vocab: Vocabulary | None = None) -> Draft:
    """The name from hand-edited fields (people separated as split_names reads them)."""
    vocab = vocab or Vocabulary()
    return Draft(
        people=_people_parts(split_names(people), vocab),
        title=clean_title(title),
        collection=_collection_part(collection, vocab),
    )
