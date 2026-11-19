"""jellyname's filename parser, pinned against real-world release names.

The fixture file holds input → (folder, filename) pairs; add a row whenever a new naming
pattern turns up, rather than reasoning about the regexes in isolation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import load_fixture

from toolboxcli.jellyname.core import (
    MediaTags,
    apply_probed_tags,
    parse_movie,
    parse_tv,
    probed_tags,
    process_file,
)

CASES = load_fixture("jellyname.json")


def _run(name: str) -> tuple[str, str] | None:
    result = process_file(Path(name), Path("lib"))
    if result is None:
        return None
    target_dir, new_name = result
    return target_dir.relative_to("lib").as_posix(), new_name


@pytest.mark.parametrize("case", CASES, ids=[c["input"] for c in CASES])
def test_process_file(case):
    expected = (case["dir"], case["name"]) if case["name"] else None
    assert _run(case["input"]) == expected


@pytest.mark.parametrize("case", [c for c in CASES if c["name"]],
                         ids=[c["input"] for c in CASES if c["name"]])
def test_rerun_on_own_output_is_a_no_op(case):
    # Re-running over an organised library must not rename anything again.
    assert _run(case["name"]) == (case["dir"], case["name"])


def test_tv_anchor_is_a_whole_token():
    # "S01E01" inside a word is not an episode marker.
    assert parse_tv("XS01E01Y 1080p") is None


def test_year_that_would_empty_the_title_is_not_an_anchor():
    title, year, _ = parse_movie("1917")
    assert (title, year) == ("1917", "")


def test_probed_tags_from_stream_data():
    payload = {
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "height": 2160,
             "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084"},
            {"codec_type": "audio", "codec_name": "eac3"},
        ],
        "format": {"tags": {"TITLE": "Some.Movie.2020.BluRay.REMASTERED"}},
    }
    assert probed_tags(payload) == {
        "resolution": "2160p", "video_codec": "HEVC", "bit_depth": "10bit",
        "hdr": "HDR10", "audio": "EAC3", "source": ["BluRay"], "edition": ["REMASTERED"],
    }


def test_probed_tags_detect_dolby_vision_and_interlacing():
    payload = {"streams": [{
        "codec_type": "video", "codec_name": "h264", "height": 1080, "field_order": "tt",
        "pix_fmt": "yuv420p", "side_data_list": [{"side_data_type": "DOVI configuration record"}],
    }]}
    tags = probed_tags(payload)
    assert tags["resolution"] == "1080i"
    assert tags["hdr"] == "Dolby Vision"
    assert "bit_depth" not in tags  # 8-bit carries no tag


def test_apply_probed_tags_never_overrides_the_filename():
    tags = MediaTags(resolution="720p", audio=["AAC"])
    apply_probed_tags(tags, {"resolution": "1080p", "audio": "EAC3", "video_codec": "HEVC"})
    assert tags.resolution == "720p"
    assert tags.audio == ["AAC"]
    assert tags.video_codec == "HEVC"
