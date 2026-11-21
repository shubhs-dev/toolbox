"""Finding videos that are parts of one (vidlib/parts.py).

tests/fixtures/vidlib_parts.json pins how part markers are read from titles. When a new way
of numbering parts turns up, add a row there.
"""

from __future__ import annotations

import pytest
from conftest import load_fixture

from toolboxcli.vidlib.parts import Video, find_groups, parse_part

MARKERS = load_fixture("vidlib_parts.json")["markers"]


@pytest.mark.parametrize("row", MARKERS, ids=[r["title"] for r in MARKERS])
def test_markers(row):
    part = parse_part(row["title"])
    if row["base"] is None:
        assert part is None
        return
    assert part is not None
    assert (part.base, part.marker.first, part.marker.last) == (
        row["base"], row["first"], row["last"])
    assert part.marker.total == row.get("total")
    assert part.marker.bare == row.get("bare", False)


def _videos(*titles, people=(), collection=""):
    return [Video(i, t, tuple(people), collection) for i, t in enumerate(titles, 1)]


def test_differently_written_parts_are_one_sure_group_in_order():
    videos = _videos("Lemonade Stand Finale - Part 2", "lemonade-stand finale P1",
                     "Lemonade Stand Finale (Ep 3)", "Something else")
    [group] = find_groups(videos)
    assert group.sure and [m.video.id for m in group.members] == [2, 1, 3]
    assert group.title == "lemonade-stand finale" and group.gaps == [] and group.doubled == []


def test_people_and_collection_dont_have_to_match():
    videos = [Video(1, "Finale P1", ("DougDoug",), "Lemonade Stand"),
              Video(2, "Finale P2", ("DougDoug", "Atrioc"), "Crossovers"),
              Video(3, "Finale P3", ("Atrioc",), "Lemonade Stand")]
    [group] = find_groups(videos)
    assert group.people == ("DougDoug", "Atrioc")
    assert group.collection == "Lemonade Stand"                  # most parts share it


def test_known_names_in_a_badly_named_title_are_ignored():
    videos = [Video(1, "Lemonade Stand Finale P1", ("DougDoug",)),
              Video(2, "DougDoug lemonade stand finale (Ep 3)"),
              Video(3, "DougDoug's Lemonade Stand Finale Part 2")]
    [group] = find_groups(videos, people=["DougDoug"])
    assert group.sure and [m.video.id for m in group.members] == [1, 3, 2]
    assert len(find_groups(videos)) <= 1                         # without the names: not sure


def test_mostly_shared_words_make_a_maybe_group():
    videos = _videos("Big lake trip with friends P1", "Big lake trip with friends again P2",
                     "Totally unrelated P1")
    groups = find_groups(videos)
    assert len(groups) == 1 and not groups[0].sure
    assert {m.video.id for m in groups[0].members} == {1, 2}


def test_gaps_ranges_and_doubles_are_reported():
    [group] = find_groups(_videos("Trip P1", "Trip P3", "Trip P4"))
    assert group.gaps == [2]
    [group] = find_groups(_videos("Trip Part 1-5", "Trip Part 6-10"))
    assert group.gaps == [] and group.doubled == []
    [group] = find_groups(_videos("Trip P1", "Trip P2", "Trip Part 2"))
    assert group.doubled == [2]


def test_bare_numbers_need_a_set_with_a_part_one():
    assert find_groups(_videos("Marketing Monday 12", "Marketing Monday 13")) == []
    assert find_groups(_videos("Mario 64")) == []
    assert find_groups(_videos("Day 1", "Day 1")) == []          # one video twice
    [group] = find_groups(_videos("Beach day 1", "Beach day 2"))
    assert [m.part.marker.first for m in group.members] == [1, 2]


def test_a_lone_part_is_not_a_group():
    assert find_groups(_videos("Trip P1", "Other P2")) == []


def test_a_dismissed_set_stays_away_until_a_new_part_joins():
    videos = _videos("Trip P1", "Trip P2")
    assert find_groups(videos, dismissed=[frozenset({1, 2})]) == []
    videos.append(Video(3, "Trip P3"))
    [group] = find_groups(videos, dismissed=[frozenset({1, 2})])
    assert group.ids == {1, 2, 3}
