"""kavitaname's parser and renderer, pinned against real manga filenames.

What matters is the round trip: the name kavitaname *emits* has to parse back to the same
volume/chapter/special, so it is checked in both directions.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from conftest import load_fixture

from toolboxcli.kavitaname.cli import load_volume_map
from toolboxcli.kavitaname.core import (
    ChapterInfo,
    build_name,
    drop_duplicate_markers,
    is_cover_image,
    parse_stem,
    process_file,
)

CASES = load_fixture("kavitaname.json")
IDS = [c["input"] for c in CASES]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_parse_stem(case):
    info = parse_stem(case["input"])
    assert info.__dict__ == case["parsed"]


def test_process_file():
    # One counter across the whole table, as a run over a folder would use: specials
    # without their own SP number are numbered in the order they're met.
    counter = itertools.count(1)
    for case in CASES:
        series = case["parsed"]["series"] or "Series"
        result = process_file(Path("lib") / f"{case['input']}.cbz", series, "manga",
                              special_index=lambda: next(counter))
        got = (result[0].relative_to("lib").as_posix(), result[1]) if result else None
        expected = (case["dir"], case["name"]) if case["name"] else None
        assert got == expected, case["input"]


@pytest.mark.parametrize("case", [c for c in CASES if c["name"]],
                         ids=[c["input"] for c in CASES if c["name"]])
def test_output_parses_back_to_the_same_numbers(case):
    original = parse_stem(case["input"])
    reparsed = parse_stem(Path(case["name"]).stem)
    assert reparsed.volume.lstrip("0") == original.volume.lstrip("0").replace("v", "")
    assert reparsed.chapter.lstrip("0") == original.chapter.lstrip("0").replace("c", "")
    assert reparsed.is_special == original.is_special


@pytest.mark.parametrize("case", [c for c in CASES if c["name"]],
                         ids=[c["input"] for c in CASES if c["name"]])
def test_rerun_on_own_output_is_a_no_op(case):
    path = Path("lib") / case["dir"] / case["name"]
    series = case["parsed"]["series"] or "Series"
    result = process_file(path, series, "manga", special_index=lambda: 99)
    assert result is not None and result[1] == case["name"]


def test_volume_map_fills_only_a_missing_volume():
    vmap = {1045: 105}
    _, name = process_file(Path("One Piece c1045.cbz"), "One Piece", "manga", volume_map=vmap)
    assert name == "One Piece c1045 (v105).cbz"
    _, name = process_file(Path("One Piece v99 c1045.cbz"), "One Piece", "manga", volume_map=vmap)
    assert name == "One Piece c1045 (v99).cbz"


def test_a_file_already_in_specials_is_not_nested_again():
    target, _ = process_file(Path("lib/Specials/One Piece SP01.cbz"), "One Piece", "manga")
    assert target == Path("lib/Specials")


def test_duplicate_markers_truncate_but_ranges_survive():
    assert drop_duplicate_markers("Series v01 c001 v02") == "Series v01 c001"
    assert drop_duplicate_markers("Series v01-v02") == "Series v01-v02"


def test_nothing_to_say_renders_nothing():
    assert build_name(ChapterInfo(series="X"), "X", "cbz") is None


@pytest.mark.parametrize("name, expected", [
    ("cover.jpg", True), ("!Cover.PNG", True), ("folder.webp", True),
    ("cover.cbz", False), ("page01.jpg", False),
])
def test_is_cover_image(name, expected):
    assert is_cover_image(name) is expected


def test_volume_map_accepts_json_and_pipe_formats(tmp_path):
    # Both formats must keep working: the docs once described the pipe format as
    # mergemanga's, while mergemanga's real file is JSON — following them silently
    # produced an empty map.
    as_json = tmp_path / "map.json"
    as_json.write_text(json.dumps([{"volume": 1, "start": 1, "end": 3, "title": "A"}]))
    as_pipe = tmp_path / "map.txt"
    as_pipe.write_text("# comment\n1|1|3|A\n")
    expected = {1: 1, 2: 1, 3: 1}
    assert load_volume_map(as_json) == expected
    assert load_volume_map(as_pipe) == expected


def test_mergemanga_bundled_map_loads_as_a_kavitaname_volume_map():
    from importlib.resources import files
    bundled = files("toolboxcli.mergemanga") / "data" / "volume_map.json"
    vmap = load_volume_map(Path(str(bundled)))
    assert vmap[1] == 1 and vmap[9] == 2
