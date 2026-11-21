"""vidlib/core.py: tag management, the Collection (trip) field, subtitle matching,
encoder selection, duplicate verdicts and the off-hours window."""

from __future__ import annotations

from datetime import datetime, time

import pytest

from toolboxcli.vidlib import core


@pytest.mark.parametrize("height, label", [
    (2160, "2160p"), (1440, "1440p"), (1080, "1080p"), (1088, "1080p"),
    (1079, "720p"), (720, "720p"), (576, "576p"), (480, "480p"), (360, "360p"),
    (240, "240p"), (0, ""),
])
def test_resolution_label_buckets_down_to_a_standard_rung(height, label):
    assert core.resolution_label(height) == label


@pytest.mark.parametrize("stem, height, depth, sub, expected", [
    ("People - Title - Trip", 1080, 8, False, "People - Title - Trip [1080p]"),
    ("People - Title - Trip", 720, 10, False, "People - Title - Trip [720p 10bit]"),
    ("People - Title - Trip", 1080, 8, True, "People - Title - Trip [1080p Sub]"),
    # Owned tokens are replaced, never duplicated; foreign tokens keep their order.
    ("A - B - C [Restored HDR 720p]", 1080, 10, False, "A - B - C [Restored HDR 1080p 10bit]"),
    ("A - B - C [1080p 10bit]", 1080, 8, False, "A - B - C [1080p]"),
    ("A - B - C [720p Sub]", 720, 8, False, "A - B - C [720p]"),
    ("A - B - C [Restored]", 0, 8, False, "A - B - C [Restored]"),
    ("A - B - C [1080i]", 1080, 12, False, "A - B - C [1080p 12bit]"),
])
def test_apply_tags(stem, height, depth, sub, expected):
    assert core.apply_tags(stem, height, depth, sub) == expected


def test_apply_tags_is_idempotent():
    once = core.apply_tags("A - B - C [Restored]", 1080, 10, True)
    assert core.apply_tags(once, 1080, 10, True) == once


@pytest.mark.parametrize("stem, trip", [
    ("People - Title - Lakes", "Lakes"),
    ("People - Title - Lakes [1080p]", "Lakes"),
    ("Alice - Day 1 - Part 2 - Lakes", "Lakes"),
    ("People - Title - Lakes - Sub", "Lakes"),  # addsub -u's marker isn't a trip
    ("People - Title", ""),                      # two fields is an incomplete name
    ("Just a name", ""),
])
def test_trip_is_the_last_field_of_three_or_more(stem, trip):
    assert core.parse_stem(stem).trip == trip


def test_identity_ignores_tags_case_and_spacing():
    a = core.parse_stem("People - Title  - Trip [1080p]").identity()
    b = core.parse_stem("people - title - trip").identity()
    assert a == b


def test_render_round_trips():
    stem = "A - B - C [Restored 1080p]"
    assert core.parse_stem(stem).render() == stem


def test_match_subtitles_only_merges_unambiguous_pairs():
    videos = ["Alice - Day 1 - Lakes [1080p]", "Bob - Day 1 - Lakes [1080p]",
              "Carol - X - Peaks", "Carol2 - Y - Peaks"]
    subs = ["Alice.en.srt", "nobody.srt", "Carol2.srt", "Bob.srt", "Bob.forced.srt"]
    matches, skipped = core.match_subtitles(videos, subs)
    assert matches == {"Alice.en.srt": "Alice - Day 1 - Lakes [1080p]"}
    assert skipped["nobody.srt"] == "no matching video"
    assert skipped["Carol2.srt"] == "matches 2 videos"  # "Carol" is a substring too
    assert skipped["Bob.srt"].startswith("video also claimed")
    assert skipped["Bob.forced.srt"].startswith("video also claimed")


@pytest.mark.parametrize("height, preset", [
    (2160, "hw-1080"), (1080, "hw-1080"), (721, "hw-1080"),
    (720, "hw-720"), (480, "hw-720"), (0, "hw-1080"),
])
def test_select_preset(height, preset):
    assert core.select_preset(height, "hw-1080", "hw-720") == preset


@pytest.mark.parametrize("pref, depth, expected", [
    ("auto", 8, False), ("auto", 10, True), ("auto", 12, True),
    ("8", 10, False), ("10", 8, True),
])
def test_want_10bit(pref, depth, expected):
    assert core.want_10bit(pref, depth) is expected


ALL_ENCODERS = {"vce_h265", "vce_h265_10bit", "nvenc_h265", "nvenc_h265_10bit",
                "qsv_h265", "vt_h265", "vt_h265_10bit", "x265", "x265_10bit"}


def test_select_backend_prefers_the_presets_own_encoder():
    backend, auto = core.select_backend("auto", ALL_ENCODERS, "vce_h265", platform="win32")
    assert (backend.name, auto) == ("amd", True)


def test_select_backend_falls_back_in_platform_order():
    available = {"nvenc_h265", "x265"}
    assert core.select_backend("auto", available, "vce_h265", "linux")[0].name == "nvidia"
    assert core.select_backend("auto", {"x265"}, "vce_h265", "darwin")[0].name == "cpu"
    assert core.select_backend("auto", {"vt_h265", "x265"}, "vce_h265", "darwin")[0].name == "apple"


def test_explicit_backend_is_not_auto_and_must_exist():
    assert core.select_backend("nvidia", {"nvenc_h265"}, "vce_h265") == (core.BACKENDS["nvidia"], False)
    assert core.select_backend("nvidia", {"x265"}, "vce_h265") == (None, False)


def test_encoder_args_are_empty_on_the_presets_own_hardware():
    amd = core.BACKENDS["amd"]
    assert core.encoder_args(amd, "vce_h265", 25, available=ALL_ENCODERS) == []


def test_encoder_args_switch_encoder_and_remap_videotoolbox_quality():
    assert core.encoder_args(core.BACKENDS["cpu"], "vce_h265", 25, available=ALL_ENCODERS) == [
        "-e", "x265"]
    # VideoToolbox's scale is inverted: passing CQ 25 through would be near-worst quality.
    assert core.encoder_args(core.BACKENDS["apple"], "vce_h265", 25, available=ALL_ENCODERS) == [
        "-e", "vt_h265", "-q", "58"]


def test_encoder_args_10bit_and_its_fallback():
    nvidia = core.BACKENDS["nvidia"]
    assert core.encoder_args(nvidia, "vce_h265", 25, ten_bit=True, available=ALL_ENCODERS) == [
        "-e", "nvenc_h265_10bit"]
    # No 10-bit variant in this build: stay on the same hardware at 8-bit.
    intel = core.BACKENDS["intel"]
    assert core.encoder_args(intel, "vce_h265", 25, ten_bit=True, available=ALL_ENCODERS) == [
        "-e", "qsv_h265"]


def test_quality_override_always_applies():
    amd = core.BACKENDS["amd"]
    assert core.encoder_args(amd, "vce_h265", 25, quality_override=20, available=ALL_ENCODERS) == [
        "-q", "20"]


@pytest.mark.parametrize("new, old, verdict", [
    ((1080, 100.0), (720, 100.0), core.UPGRADE),
    ((1080, 90.0), (720, 100.0), core.AMBIGUOUS),     # higher res but noticeably shorter
    ((1080, 99.0), (720, 100.0), core.UPGRADE),       # within the 2% tolerance
    ((720, 110.0), (720, 100.0), core.MORE_COMPLETE),
    ((720, 101.0), (720, 100.0), core.AMBIGUOUS),
    ((480, 100.0), (720, 100.0), core.DOWNGRADE),
    ((480, 120.0), (720, 100.0), core.AMBIGUOUS),     # longer but worse is a judgement call
    ((1080, 0.0), (720, 100.0), core.UPGRADE),        # no duration: resolution decides
    ((720, 0.0), (720, 100.0), core.AMBIGUOUS),
    ((1088, 100.0), (1080, 100.0), core.AMBIGUOUS),   # same rung once bucketed
])
def test_duplicate_verdict(new, old, verdict):
    assert core.duplicate_verdict(new[0], new[1], old[0], old[1]) == verdict


def test_parse_hours():
    assert core.parse_hours("01:00-07:30") == (time(1, 0), time(7, 30))
    assert core.parse_hours(" 22:00 - 06:00 ") == (time(22, 0), time(6, 0))
    for bad in ("1-7", "25:00-07:00", "01:60-02:00", ""):
        with pytest.raises(ValueError):
            core.parse_hours(bad)


def test_within_hours_handles_wraparound():
    night = core.parse_hours("22:00-06:00")
    assert core.within_hours(night, time(23, 0))
    assert core.within_hours(night, time(5, 59))
    assert not core.within_hours(night, time(6, 0))
    assert not core.within_hours(night, time(12, 0))
    assert core.within_hours(None, time(12, 0))


def test_seconds_until_window_opens():
    window = core.parse_hours("01:00-07:00")
    assert core.seconds_until(window, datetime(2026, 1, 1, 0, 30)) == 1800
    assert core.seconds_until(window, datetime(2026, 1, 1, 2, 0)) == 23 * 3600
