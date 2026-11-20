"""Title labelling, filename building and the saved queue for video-download (no I/O).

A pasted page title is split into tokens; each token gets a set of labels — people, title
and/or collection, or none (skipped) — first guessed (auto_label) and then corrected by hand
in the TUI; compose() turns the labelled tokens into the optimiselib naming convention,
People - Title - Collection.

    Alice & Bob hike to the lakes – Day 1 (Lakes) | SomeSite
    → Alice, Bob - hike to the lakes Day 1 - Lakes

A token can carry two labels: a name in the middle of a sentence ("sunrise summit with Bob")
is a person *and* stays in the title, so the title still reads as a sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from toolboxcli._common.pathsafe import sanitize_for_path
from toolboxcli.optimiselib.core import FIELD_SEP, normalize, parse_name

# ── Tokens ───────────────────────────────────────────────────────────

Kind = Literal["word", "separator", "connector"]
Field = Literal["people", "title", "collection"]
FIELDS: tuple[Field, ...] = ("people", "title", "collection")
# A token's labels. Empty means skipped: it doesn't appear in the name.
Labels = frozenset[Field]
SKIP: Labels = frozenset()
PEOPLE: Labels = frozenset({"people"})
TITLE: Labels = frozenset({"title"})
COLLECTION: Labels = frozenset({"collection"})

# Marks that divide a title into parts. These never occur inside a real name, so they split a
# word wherever they appear ("1.5|Site").
_HARD_SEPARATORS = "|–—·•"
# These do occur inside words (12:30, AC/DC, f(x)), so they only split at a word's edges.
# A dash only counts standing alone ("Day 1 - Lakes"): Rent-a-Girlfriend, Day-1 stay whole.
_SEPARATOR_CHARS = _HARD_SEPARATORS + ":/()[]{}"
_STANDALONE_SEPARATORS = {"-", "--"}
# What joins names: "Alice & Bob", "Alice, Bob and Carol".
_CONNECTOR_WORDS = {"&", "and", "+", ",", "x", "×"}
# Trailing punctuation that's never part of a name or collection.
_TRAILING_PUNCT = ",;.!?"


@dataclass(frozen=True)
class Token:
    text: str
    kind: Kind


def tokenize(title: str) -> list[Token]:
    """Split a pasted title into words, separators and connectors.

    Whitespace divides tokens; separator characters and a trailing comma are also peeled
    off the edges of a word ("Lakes|Site", "(Lakes)", "Alice,") so the word itself can be
    labelled, while punctuation *inside* a word (O'Brien, 1.5, Rent-a-Girlfriend) stays.
    """
    tokens: list[Token] = []
    spaced = re.sub(f"([{re.escape(_HARD_SEPARATORS)}])", r" \1 ", title)
    for chunk in spaced.split():
        if chunk in _STANDALONE_SEPARATORS:
            tokens.append(Token(chunk, "separator"))
            continue
        lead: list[Token] = []
        while chunk and chunk[0] in _SEPARATOR_CHARS:
            lead.append(Token(chunk[0], "separator"))
            chunk = chunk[1:]
        trail: list[Token] = []
        while chunk and (chunk[-1] in _SEPARATOR_CHARS or chunk[-1] == ","):
            mark = chunk[-1]
            trail.insert(0, Token(mark, "connector" if mark == "," else "separator"))
            chunk = chunk[:-1]
        tokens.extend(lead)
        if chunk:
            kind: Kind = "connector" if chunk.lower() in _CONNECTOR_WORDS else "word"
            tokens.append(Token(chunk, kind))
        tokens.extend(trail)
    return tokens


# ── Guessing roles ───────────────────────────────────────────────────

_MAX_NAME_WORDS = 4


@dataclass
class KnownNames:
    """People and collections the library (or earlier downloads) already uses, normalised."""

    people: set[str] = field(default_factory=set[str])
    collections: set[str] = field(default_factory=set[str])


_POSSESSIVE_RE = re.compile(r"['’]s$", re.IGNORECASE)


def _name_text(token: Token) -> str:
    """A word as it would be written in a name: trailing punctuation and a possessive gone."""
    return _POSSESSIVE_RE.sub("", token.text.rstrip(_TRAILING_PUNCT))


def _find_runs(tokens: list[Token], labels: list[Labels],
               names: set[str]) -> list[tuple[int, int]]:
    """(start, length) of every run of words spelling a known name, longest names first."""
    runs: list[tuple[int, int]] = []
    if not names:
        return runs
    i = 0
    while i < len(tokens):
        matched = 0
        for span in range(_MAX_NAME_WORDS, 0, -1):
            run = tokens[i:i + span]
            if len(run) < span or any(t.kind != "word" for t in run):
                continue
            if labels[i] & {"people", "collection"}:
                break
            if normalize(" ".join(_name_text(t) for t in run)) in names:
                matched = span
                break
        if matched:
            runs.append((i, matched))
            i += matched
        else:
            i += 1
    return runs


def _segments(tokens: list[Token]) -> list[tuple[int, int]]:
    """(start, end) of each stretch of words between separators."""
    spans: list[tuple[int, int]] = []
    start = None
    for i, token in enumerate(tokens):
        if token.kind == "separator":
            if start is not None:
                spans.append((start, i))
                start = None
        elif start is None:
            start = i
    if start is not None:
        spans.append((start, len(tokens)))
    return spans


def _leading_group(tokens: list[Token], runs: list[tuple[int, int]]) -> set[int]:
    """Starts of the people runs at the very beginning of the title, before any other word —
    "Alice & Bob hike…", "Mary Jane, Alice and Bob: …" — where a page puts who it's about."""
    starts = {start: length for start, length in runs}
    group: set[int] = set()
    i = 0
    while i < len(tokens):
        if i in starts:
            group.add(i)
            i += starts[i]
        elif tokens[i].kind == "connector":
            i += 1
        else:
            break
    return group


def auto_label(tokens: list[Token], known: KnownNames | None = None) -> list[Labels]:
    """A first guess at each token's labels, for the user to correct.

    - Separators are skipped, and so is everything after the last "|" (a site or channel).
    - Words default to title.
    - A run of words spelling a known person or collection gets that label. It *moves* out of
      the title when it's clearly not part of the sentence — at the very start ("Alice & Bob
      hike…") or standing as its own segment ("(Lakes)", "| Japan 2024 |"). Anywhere else
      ("summit with Bob") it keeps the title label too, so the title still reads properly.
      A possessive ("Bob's") always stays in the title.
    - Only one collection is labelled: the one standing in its own segment if there is one,
      else the last mention.
    - A connector between two people joins them ("Alice & Bob", "Alice x Carol").
    """
    known = known or KnownNames()
    labels: list[Labels] = [SKIP if t.kind == "separator" else TITLE for t in tokens]
    pipes = [i for i, t in enumerate(tokens) if t.text == "|"]
    site_from = pipes[-1] if pipes else len(tokens)
    for j in range(site_from, len(tokens)):
        labels[j] = SKIP

    whole_segments = set(_segments(tokens))
    people_runs = [r for r in _find_runs(tokens, labels, known.people) if r[0] < site_from]
    leading = _leading_group(tokens, people_runs)

    def apply(start: int, length: int, field: Field) -> None:
        own_segment = (start, start + length) in whole_segments
        possessive = any(_POSSESSIVE_RE.search(t.text) for t in tokens[start:start + length])
        moves = (own_segment or start in leading) and not possessive
        for j in range(start, start + length):
            labels[j] = frozenset({field}) if moves else frozenset({field, "title"})

    for start, length in people_runs:
        apply(start, length, "people")

    runs = [r for r in _find_runs(tokens, labels, known.collections) if r[0] < site_from]
    if runs:
        alone = [r for r in runs if (r[0], r[0] + r[1]) in whole_segments]
        apply(*(alone or runs)[-1], "collection")

    for i, token in enumerate(tokens):
        if token.kind == "connector" and 0 < i < len(tokens) - 1:
            before, after = labels[i - 1], labels[i + 1]
            if "people" in before and "people" in after:
                # Joins two names; still part of the title only if both names are.
                labels[i] = PEOPLE | (TITLE if "title" in before and "title" in after else SKIP)
    return labels


# ── Composing a name ─────────────────────────────────────────────────


@dataclass(frozen=True)
class NameParts:
    people: tuple[str, ...]
    title: str
    collection: str

    @property
    def stem(self) -> str:
        """The filename without extension: People - Title - Collection, missing parts left out."""
        parts = [", ".join(self.people), self.title, self.collection]
        return FIELD_SEP.join(p for p in parts if p)

    def problems(self) -> list[str]:
        """Why optimiselib couldn't sort this name; empty when it can."""
        missing = [label for label, value in (
            ("people", self.people), ("title", self.title), ("collection", self.collection),
        ) if not value]
        if missing:
            return [f"no {', '.join(missing)} — optimiselib won't be able to sort it"]
        parsed = parse_name(self.stem)
        if not parsed.complete or tuple(parsed.people) != self.people:
            return ["optimiselib would read this name differently"]
        return []


def _clean_field(text: str) -> str:
    """Safe inside a People - Title - Collection field: no path characters, no " - ".
    A slash reads as a dash ("Part 2/3" → "Part 2-3") rather than vanishing."""
    text = sanitize_for_path(re.sub(r"\s*[/\\]\s*", "-", text))
    return re.sub(r"\s+-\s+", " ", text).strip(" -")


def _join(tokens: list[Token]) -> str:
    text = " ".join(t.text for t in tokens)
    text = re.sub(r"\s+([,;:.!?)\]])", r"\1", text)   # "Part 1 :" → "Part 1:"
    text = re.sub(r"([(\[])\s+", r"\1", text)          # "( Lakes" → "(Lakes"
    return text


def compose(tokens: list[Token], labels: list[Labels]) -> NameParts:
    """Build the name from labelled tokens.

    People are split into names wherever anything but a people-labelled word comes between
    them, so "Alice & Bob" and "Alice, Bob and Carol" are separate people. Title and
    collection keep their tokens' text in order.
    """
    people: list[str] = []
    current: list[str] = []
    for token, label in zip(tokens, labels, strict=True):
        if "people" in label and token.kind == "word":
            current.append(_name_text(token))
            continue
        if current:
            people.append(" ".join(current))
            current = []
    if current:
        people.append(" ".join(current))

    title = _join([t for t, lb in zip(tokens, labels, strict=True) if "title" in lb])
    collection = _join([t for t, lb in zip(tokens, labels, strict=True)
                        if "collection" in lb and t.kind == "word"])
    return NameParts(
        people=tuple(p for p in (_clean_field(n) for n in people) if p),
        title=_clean_field(title.strip(" ,&")),
        collection=_clean_field(collection),
    )


def parts_from_fields(people: str, title: str, collection: str) -> NameParts:
    """NameParts from hand-edited fields (people comma/&/and-separated, as optimiselib reads)."""
    from toolboxcli.optimiselib.core import split_people

    return NameParts(
        people=tuple(p for p in (_clean_field(n) for n in split_people(people)) if p),
        title=_clean_field(title),
        collection=_clean_field(collection),
    )


# ── Saved queue ──────────────────────────────────────────────────────

Status = Literal["queued", "downloading", "done", "failed", "cancelled"]
STATUSES: tuple[Status, ...] = ("queued", "downloading", "done", "failed", "cancelled")
QUEUE_VERSION = 1


@dataclass
class QueueItem:
    url: str
    name: str                      # filename stem; "" lets yt-dlp use the video's title
    status: Status = "queued"
    error: str = ""
    added: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"url": self.url, "name": self.name, "status": self.status,
                "error": self.error, "added": self.added}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> QueueItem | None:
        url = data.get("url")
        if not isinstance(url, str) or not url:
            return None
        status = data.get("status")
        if status not in STATUSES:
            status = "queued"
        # A download that was running when the app stopped is queued again: yt-dlp resumes
        # its partial file.
        if status == "downloading":
            status = "queued"
        return cls(url=url, name=str(data.get("name") or ""), status=status,
                   error=str(data.get("error") or ""), added=str(data.get("added") or ""))


def dump_queue(items: list[QueueItem]) -> dict[str, Any]:
    return {"version": QUEUE_VERSION, "items": [i.to_json() for i in items]}


def load_queue(data: Any) -> list[QueueItem]:
    """Items from a saved queue's parsed JSON; anything unreadable is dropped, not fatal."""
    if not isinstance(data, dict):
        return []
    items: Any = data.get("items")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(items, list):
        return []
    loaded: list[QueueItem] = []
    for raw in items:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(raw, dict):
            item = QueueItem.from_json(raw)  # pyright: ignore[reportUnknownArgumentType]
            if item is not None:
                loaded.append(item)
    return loaded


def known_from_queue(items: list[QueueItem]) -> KnownNames:
    """People and collections used by earlier downloads, so they're recognised next time."""
    known = KnownNames()
    for item in items:
        name = parse_name(item.name)
        if name.complete:
            known.people.update(normalize(p) for p in name.people)
            known.collections.add(normalize(name.collection))
    return known


# ── Progress lines ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Progress:
    """Where a running download is, as read from one line of its output."""

    percent: float
    speed: str = ""     # as the tool printed it: "4.2MiB/s"
    eta: str = ""       # "00:05", "4m50s"


# yt-dlp's own downloader (with --newline):
#   [download]  45.3% of ~  10.00MiB at    1.23MiB/s ETA 00:05 (frag 3/10)
_YTDLP_RE = re.compile(
    r"\[download\]\s+(?P<pct>\d+(?:\.\d+)?)%"
    r"(?:.*?\bat\s+(?P<speed>\S+/s))?(?:.*?\bETA\s+(?P<eta>[\d:]+))?"
)
# aria2c, which yt-dlp hands the download to here:
#   [#2089b0 400.0KiB/33.2MiB(1%) CN:16 DL:4.2MiB ETA:4m50s]
_ARIA2C_RE = re.compile(
    r"\[#\w+\s+\S+?\((?P<pct>\d+(?:\.\d+)?)%\)"
    r"(?:.*?\bDL:(?P<speed>[\d.]+\w*))?(?:.*?\bETA:(?P<eta>\w+))?"
)


def parse_progress(line: str) -> Progress | None:
    """A Progress from a yt-dlp or aria2c status line, or None for any other line."""
    for pattern, per_second in ((_YTDLP_RE, ""), (_ARIA2C_RE, "/s")):
        m = pattern.search(line)
        if m:
            speed = m.group("speed") or ""
            return Progress(
                percent=min(float(m.group("pct")), 100.0),
                speed=f"{speed}{per_second}" if speed and per_second else speed,
                eta=m.group("eta") or "",
            )
    return None
