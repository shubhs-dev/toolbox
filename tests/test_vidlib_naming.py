"""Naming a download from a pasted page title (vidlib/naming.py).

tests/fixtures/vidlib_titles.json pins how page titles are split into segments, which role
each segment is guessed to have, and the name that makes. To change a rule, change the code
and update the affected rows — the file is the spec.
"""

from __future__ import annotations

import pytest
from conftest import load_fixture

from toolboxcli.vidlib import naming
from toolboxcli.vidlib.core import parse_name
from toolboxcli.vidlib.naming import Vocabulary, compose, guess_roles, split_segments

FIXTURE = load_fixture("vidlib_titles.json")
VOCAB = Vocabulary.from_spellings(FIXTURE["known"]["people"], FIXTURE["known"]["collections"],
                                  FIXTURE["known"]["skipped"])
CASES = FIXTURE["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["title"] for c in CASES])
def test_split_segments(case):
    assert [s.text for s in split_segments(case["title"])] == case["segments"]


@pytest.mark.parametrize("case", CASES, ids=[c["title"] for c in CASES])
def test_guess_roles(case):
    assert guess_roles(split_segments(case["title"]), VOCAB) == case["roles"]


@pytest.mark.parametrize("case", CASES, ids=[c["title"] for c in CASES])
def test_compose(case):
    segments = split_segments(case["title"])
    draft = compose(segments, guess_roles(segments, VOCAB), VOCAB)
    assert draft.stem == case["name"]


@pytest.mark.parametrize("case", CASES, ids=[c["title"] for c in CASES])
def test_a_complete_name_reads_back_as_composed(case):
    segments = split_segments(case["title"])
    draft = compose(segments, guess_roles(segments, VOCAB), VOCAB)
    if draft.complete:
        parsed = parse_name(draft.stem)
        assert parsed.people == tuple(p.text for p in draft.people)
        assert parsed.title == draft.title
        assert draft.collection is not None and parsed.collection == draft.collection.text


def test_dashes_inside_words_and_unspaced_dashes_dont_split():
    assert [s.text for s in split_segments("Rent-a-Girlfriend Day-1 -Lakes")] == [
        "Rent-a-Girlfriend Day-1 -Lakes"]


def test_only_what_follows_the_last_pipe_is_the_site():
    segments = split_segments("A | B - C | Site")
    assert [(s.text, s.site) for s in segments] == [
        ("A", False), ("B", False), ("C", False), ("Site", True)]


def test_the_users_order_of_segments_doesnt_matter():
    segments = split_segments("Lemonade Stand - The Episode - DougDoug")
    draft = compose(segments, ["collection", "title", "people"])
    assert draft.stem == "DougDoug - The Episode - Lemonade Stand"


def test_several_title_segments_keep_their_order_and_dash():
    segments = split_segments("A - Part 1 - B - The Return - C")
    draft = compose(segments, ["people", "title", "people", "title", "collection"])
    assert draft.stem == "A, B - Part 1 - The Return - C"
    assert parse_name(draft.stem).title == "Part 1 - The Return"


def test_there_is_only_one_collection():
    roles = naming.set_role(["people", "title", "collection"], 1, "collection")
    assert roles == ["people", "collection", "title"]
    assert naming.set_role(roles, 0, "skip") == ["skip", "collection", "title"]


def test_exact_matches_take_the_library_spelling_and_loose_ones_only_suggest():
    draft = naming.from_fields("dougdoug, Doug Doug2, Atri-oc, Zed", "Ep", "lemonade-stand", VOCAB)
    assert [(p.text, p.match, p.suggestion) for p in draft.people] == [
        ("DougDoug", "exact", ""), ("Doug Doug2", "new", ""),
        ("Atri-oc", "loose", "Atrioc"), ("Zed", "new", "")]
    assert draft.collection == naming.NamedPart("lemonade-stand", "loose", "Lemonade Stand")


def test_accents_match_loosely():
    assert VOCAB.person("jose") == naming.Match("loose", "José")
    assert VOCAB.person("JOSÉ") == naming.Match("exact", "José")


def test_an_ambiguous_loose_key_suggests_nothing():
    vocab = Vocabulary.from_spellings(["Doug Doug", "DougDoug"], [])
    assert vocab.person("doug-doug").kind == "new"
    assert vocab.person("dougdoug") == naming.Match("exact", "DougDoug")


def test_duplicate_people_are_dropped():
    draft = naming.from_fields("Alice, alice and ALICE", "T", "C", VOCAB)
    assert [p.text for p in draft.people] == ["Alice"]


def test_suggestions_contain_the_query_loosely_prefix_first():
    vocab = Vocabulary.from_spellings(["Bob", "Alice", "Mary Jane", "Jane Doe"], ["Lakes"])
    assert vocab.suggest("people", "jane") == ["Jane Doe", "Mary Jane"]
    assert vocab.suggest("people", "") == []
    assert vocab.suggest("collections", "LAK") == ["Lakes"]


@pytest.mark.parametrize("people, title, collection, problem", [
    ("", "Day 1", "Lakes", "no people"),
    ("Alice", "Day 1", "", "no collection"),
    ("Alice", "", "", "no title, collection"),
])
def test_problems_name_what_vidlib_would_miss(people, title, collection, problem):
    assert naming.from_fields(people, title, collection).problems()[0].startswith(problem)


def test_fields_are_made_safe_for_the_convention():
    draft = naming.from_fields("Alice and Bob", "Day 1 - Part 2/3: the end?", "Lakes | Peaks - X")
    assert [p.text for p in draft.people] == ["Alice", "Bob"]
    assert draft.title == "Day 1 - Part 2-3 the end"         # " - " is allowed in a title
    assert draft.collection is not None and draft.collection.text == "Lakes Peaks X"
    assert draft.complete


# ── Names hiding in a title (the forms' pills) ─────────────────────────

FIND_VOCAB = Vocabulary.from_spellings(["Alice", "Bob", "Mary Jane", "DougDoug"],
                                       ["Lakes", "Lemonade Stand"])


@pytest.mark.parametrize("title, have, expected", [
    # The title a known name leaves behind, separators tidied.
    ("Alice & Bob hike to the lakes (Lakes)", [], [
        ("people", "Alice", "Bob hike to the lakes (Lakes)"),
        ("people", "Bob", "Alice hike to the lakes (Lakes)"),
        ("collection", "Lakes", "Alice & Bob hike to the lakes")]),     # the last mention
    ("Day 1 - Alice - Lakes", [], [
        ("people", "Alice", "Day 1 - Lakes"), ("collection", "Lakes", "Day 1 - Alice")]),
    ("Sunset with Bob", [], [("people", "Bob", "Sunset")]),
    ("dougdoug's Lemonade Stand finale", [], [
        ("people", "DougDoug", "Lemonade Stand finale"),
        ("collection", "Lemonade Stand", "dougdoug's finale")]),
    ("Mary Jane and Alice swim", ["alice"], [("people", "Mary Jane", "Alice swim")]),
    ("Bobby goes home", [], []),                                  # whole words only
])
def test_known_names_are_found_in_a_title(title, have, expected):
    found = naming.find_in_title(title, FIND_VOCAB, have)
    assert [(f.kind, f.name, f.title_without) for f in found] == expected


def test_a_collection_already_chosen_isnt_suggested_again():
    found = naming.find_in_title("Alice at the Lakes", FIND_VOCAB, [], "lakes")
    assert [f.name for f in found] == ["Alice"]


def test_a_difference_only_in_spaces_takes_the_library_spelling_without_asking():
    vocab = Vocabulary.from_spellings(["DougDoug", "Mary Jane"], ["Lemonade Stand"])
    assert vocab.collection("LemonadeStand") == naming.Match("exact", "Lemonade Stand")
    assert vocab.collection("lemonade   stand") == naming.Match("exact", "Lemonade Stand")
    assert vocab.person("Doug Doug") == naming.Match("exact", "DougDoug")
    assert vocab.person("maryjane") == naming.Match("exact", "Mary Jane")
    assert vocab.collection("Lemonade-Stand").kind == "loose"      # punctuation still asks
    draft = naming.from_fields("doug doug", "Ep", "lemonadestand", vocab)
    assert draft.stem == "DougDoug - Ep - Lemonade Stand"


def test_skip_changes_remembers_choices_and_forgets_reversals():
    segments = split_segments("Official Video - Carol - Hike - Peaks - Promo | YouTube")
    guessed = guess_roles(segments, VOCAB)
    assert guessed == ["skip", "people", "title", "title", "collection", "skip"]
    # The user skips "Hike" and gives the remembered "Official Video" a role. "YouTube" is a
    # site segment, skipped by position rather than by choice, so it isn't remembered.
    chosen: list[naming.Role] = ["title", "people", "skip", "title", "collection", "skip"]
    remember, forget = naming.skip_changes(segments, guessed, chosen)
    assert remember == ["Hike"]
    assert forget == ["Official Video"]
