"""video-download's title labelling, name building and saved queue (videodownload/core.py).

tests/fixtures/videodownload_titles.json pins how real-looking page titles are split,
labelled and named. To change a rule, change the code and update the affected rows — the
file is the spec of what a pasted title turns into.
"""

from __future__ import annotations

import pytest
from conftest import load_fixture

from toolboxcli.videodownload import core
from toolboxcli.videodownload.core import (
    PEOPLE,
    SKIP,
    TITLE,
    KnownNames,
    NameParts,
    QueueItem,
    auto_label,
    compose,
    tokenize,
)

FIXTURE = load_fixture("videodownload_titles.json")
KNOWN = KnownNames(set(FIXTURE["known"]["people"]), set(FIXTURE["known"]["collections"]))
CASES = FIXTURE["cases"]
_LETTER = {"P": "people", "T": "title", "C": "collection"}


def _labels(codes: list[str]) -> list[core.Labels]:
    return [frozenset(_LETTER[c] for c in code) for code in codes]


@pytest.mark.parametrize("case", CASES, ids=[c["title"] for c in CASES])
def test_tokenize(case):
    assert [[t.text, t.kind] for t in tokenize(case["title"])] == case["tokens"]


@pytest.mark.parametrize("case", CASES, ids=[c["title"] for c in CASES])
def test_auto_label(case):
    assert auto_label(tokenize(case["title"]), KNOWN) == _labels(case["labels"])


@pytest.mark.parametrize("case", CASES, ids=[c["title"] for c in CASES])
def test_compose(case):
    tokens = tokenize(case["title"])
    assert compose(tokens, auto_label(tokens, KNOWN)).stem == case["name"]


def test_nothing_known_means_everything_is_title_except_separators_and_site():
    tokens = tokenize("Alice & Bob hike – Day 1 | SomeSite")
    labels = auto_label(tokens)
    assert all(lb == SKIP for t, lb in zip(tokens, labels, strict=True) if t.kind == "separator")
    assert labels[-1] == SKIP                                  # the site name
    assert compose(tokens, labels).stem == "Alice & Bob hike Day 1"


def test_hand_labels_override_the_guess():
    tokens = tokenize("Alice & Bob hike to the lakes")
    labels = [PEOPLE, PEOPLE, PEOPLE, TITLE, TITLE, SKIP, frozenset({"collection"})]
    parts = compose(tokens, labels)
    assert parts == NameParts(("Alice", "Bob"), "hike to", "lakes")
    assert parts.stem == "Alice, Bob - hike to - lakes"
    assert parts.problems() == []


@pytest.mark.parametrize("parts, problem", [
    (NameParts((), "Day 1", "Lakes"), "no people"),
    (NameParts(("Alice",), "Day 1", ""), "no collection"),
    (NameParts(("Alice",), "", ""), "no title, collection"),
])
def test_problems_name_what_optimiselib_would_miss(parts, problem):
    assert parts.problems()[0].startswith(problem)


def test_fields_are_made_safe_for_the_convention():
    parts = core.parts_from_fields("Alice and Bob", "Day 1 - Part 2/3: the end?", "Lakes | Peaks")
    assert parts.people == ("Alice", "Bob")
    assert " - " not in parts.title and "/" not in parts.title and "?" not in parts.title
    assert parts.title == "Day 1 Part 2-3 the end"
    assert parts.collection == "Lakes Peaks"


# ── Saved queue ────────────────────────────────────────────────────────


def test_queue_round_trip_requeues_interrupted_downloads():
    items = [
        QueueItem("https://x/a", "Alice - Day 1 - Lakes", status="done"),
        QueueItem("https://x/b", "", status="downloading"),
        QueueItem("https://x/c", "C", status="failed", error="HTTP 403"),
    ]
    loaded = core.load_queue(core.dump_queue(items))
    assert [(i.url, i.status) for i in loaded] == [
        ("https://x/a", "done"), ("https://x/b", "queued"), ("https://x/c", "failed")]
    assert loaded[2].error == "HTTP 403"


@pytest.mark.parametrize("data", [None, [], {"items": "x"}, {"items": [1, {"name": "no url"}]}])
def test_unreadable_queue_data_is_dropped_not_fatal(data):
    assert core.load_queue(data) == []


def test_known_names_are_learned_from_earlier_downloads():
    known = core.known_from_queue([
        QueueItem("u1", "Alice, Bob - Day 1 - Lakes"),
        QueueItem("u2", "Just a title"),
    ])
    assert known.people == {"alice", "bob"} and known.collections == {"lakes"}
