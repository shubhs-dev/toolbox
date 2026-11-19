"""The I/O-free logic of wallready, prunelib, concatvid and downscalevid."""

from __future__ import annotations

from pathlib import Path

import pytest

from toolboxcli.concatvid import core as concat
from toolboxcli.downscalevid.core import parse_resolution
from toolboxcli.prunelib import core as prune
from toolboxcli.wallready import core as wall

# ── wallready ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec, size", [
    ("4k", (3840, 2160)), ("3840x2160", (3840, 2160)), (" 1920 X 1080 ", (1920, 1080)),
    ("2560×1440", (2560, 1440)), ("1080*1920", (1080, 1920)),
])
def test_parse_size(spec, size):
    assert wall.parse_size(spec) == size


@pytest.mark.parametrize("spec", ["", "huge", "0x1080", "1920x"])
def test_parse_size_rejects(spec):
    with pytest.raises(SystemExit):
        wall.parse_size(spec)


@pytest.mark.parametrize("stem, expected", [
    ("1402962", "1402962"),
    ("wallhaven-e204cm", "e204cm"),
    ("wallhaven.e204cm (1)", "e204cm"),     # browser dedupe suffix dropped
    ("pexels-photo-1402962", "1402962"),
    ("arc-raiders-e204cm", "e204cm"),       # an already-renamed file keeps its id
    ("sunset 1920x1080", None),             # dimensions aren't ids
    ("mountain 4k", None),
    ("wallpaper2", None),                   # word + small number is a typed name
    ("abc", None),
    ("123", None),
])
def test_extract_id(stem, expected):
    assert wall.extract_id(stem) == expected


def test_rename_is_idempotent_and_generates_missing_ids():
    assert wall.rename_stem("wallhaven-e204cm", "arc-raiders") == "arc-raiders-e204cm"
    assert wall.rename_stem("arc-raiders-e204cm", "arc-raiders") == "arc-raiders-e204cm"
    generated = wall.rename_stem("sunset", "arc-raiders")
    assert generated.startswith("arc-raiders-") and len(generated) == len("arc-raiders-") + 6


def test_slugify():
    assert wall.slugify("  Arc Raiders: Night!  ") == "arc-raiders-night"


@pytest.mark.parametrize("size, jpeg, rotated, action", [
    ((3840, 2160), True, False, wall.Action.READY),
    ((3840, 2160), False, False, wall.Action.CONVERT),
    ((3840, 2160), True, True, wall.Action.CONVERT),   # bake the rotation in
    ((5000, 3000), True, False, wall.Action.RESIZE),
    ((1920, 1080), True, False, wall.Action.TOO_SMALL),
    ((2160, 3840), True, False, wall.Action.MISMATCH),  # shape is checked before size
    ((4000, 4000), True, False, wall.Action.RESIZE),    # square crops to either shape
])
def test_classify(size, jpeg, rotated, action):
    assert wall.classify(*size, (3840, 2160), jpeg, rotated).action is action


def test_cover_size_never_lands_under_the_target():
    assert wall.cover_size(5000, 3000, (3840, 2160)) == (3840, 2304)
    assert wall.cover_size(4000, 4000, (3840, 2160)) == (3840, 3840)


def test_oriented_size_and_mismatch_dir():
    assert wall.oriented_size(3000, 4000, "RightTop") == (4000, 3000)
    assert wall.oriented_size(3000, 4000, "TopLeft") == (3000, 4000)
    assert wall.mismatch_dir((3840, 2160)) == "portrait"
    assert wall.mismatch_dir((1080, 1920)) == "landscape"


# ── prunelib ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("tokens, expected", [
    ([], (10, "size")), (["25"], (25, "size")), (["oldest", "5"], (5, "oldest")),
    (["STALE"], (10, "stale")),
])
def test_parse_positionals(tokens, expected):
    assert prune.parse_positionals(tokens) == expected


@pytest.mark.parametrize("tokens", [["0"], ["5", "6"], ["size", "oldest"], ["biggest"]])
def test_parse_positionals_rejects(tokens):
    with pytest.raises(ValueError):
        prune.parse_positionals(tokens)


def _cand(rel, size=1, mtime=0.0, atime=0.0):
    return prune.Candidate(rel, size, mtime, atime)


def test_rank_orders_and_breaks_ties_on_path():
    cands = [_cand("b", 5, 3, 9), _cand("a", 5, 2, 1), _cand("c", 9, 1, 5)]
    assert [c.rel for c in prune.rank(cands, "size")] == ["c", "a", "b"]
    assert [c.rel for c in prune.rank(cands, "oldest")] == ["c", "a", "b"]
    assert [c.rel for c in prune.rank(cands, "stale")] == ["a", "c", "b"]


def test_atime_looks_unrecorded():
    untouched = [_cand(str(i), mtime=1000, atime=1000) for i in range(5)]
    assert prune.atime_looks_unrecorded(untouched)
    watched = [*untouched[:4], _cand("w", mtime=1000, atime=1000 + 2 * 86400)]
    assert not prune.atime_looks_unrecorded(watched)
    assert not prune.atime_looks_unrecorded(untouched[:4])  # too few to judge


@pytest.mark.parametrize("text, expected", [
    ("1 3 5-7", [0, 2, 4, 5, 6]), ("2,1", [1, 0]), ("7-5", [4, 5, 6]), ("", []),
])
def test_parse_selection(text, expected):
    assert prune.parse_selection(text, 7) == expected


@pytest.mark.parametrize("text", ["0", "8", "x", "1-x", "3-9"])
def test_parse_selection_rejects(text):
    with pytest.raises(ValueError):
        prune.parse_selection(text, 7)


def test_history_round_trip_and_change_detection():
    c = _cand("Movies/A.mkv", size=100, mtime=1000.0)
    reviewed = prune.mark_reviewed({}, [c], "2026-01-01")
    reviewed = prune.parse_history(prune.dump_history(reviewed))
    assert prune.is_reviewed(reviewed, c)
    assert prune.is_reviewed(reviewed, _cand("Movies/A.mkv", 100, 1001.5))  # FAT/SMB drift
    assert not prune.is_reviewed(reviewed, _cand("Movies/A.mkv", 90, 1000.0))  # re-encoded
    assert not prune.is_reviewed(reviewed, _cand("Movies/A.mkv", 100, 2000.0))


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"reviewed": []}'])
def test_unreadable_history_is_empty(text):
    assert prune.parse_history(text) == {}


def test_sidecars_belong_to_the_longest_matching_video():
    siblings = ["Show S01E01.mkv", "Show S01E01-E02.mkv", "Show S01E01.nfo",
                "Show S01E01-E02.nfo", "Show S01E01.en.srt", "Show S01E01-thumb.jpg",
                "Show S01E01.zip", "Show S01E01.trickplay", "Other.nfo"]
    is_dir = {"Show S01E01.trickplay"}.__contains__
    assert sorted(prune.sidecar_names("Show S01E01.mkv", siblings, is_dir)) == [
        "Show S01E01-thumb.jpg", "Show S01E01.en.srt", "Show S01E01.nfo",
        "Show S01E01.trickplay",
    ]
    assert prune.sidecar_names("Show S01E01-E02.mkv", siblings, is_dir) == ["Show S01E01-E02.nfo"]


@pytest.mark.parametrize("name, is_dir, expected", [
    ("poster.jpg", False, True), ("movie.nfo", False, True), ("x.zip", False, False),
    ("metadata", True, True), ("A.trickplay", True, True), ("Season 1", True, False),
])
def test_is_leftover(name, is_dir, expected):
    assert prune.is_leftover(name, is_dir) is expected


@pytest.mark.parametrize("name, skipped", [
    (".git", True), ("#recycle", True), ("@eaDir", True), ("$RECYCLE.BIN", True),
    ("Movies", False),
])
def test_skip_dir(name, skipped):
    assert prune.skip_dir(name) is skipped


# ── concatvid ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("stem, expected", [
    ("Trip part1", ("Trip", 1)), ("Trip - Part 2", ("Trip", 2)), ("Trip_pt.03", ("Trip", 3)),
    ("Trip (4)", ("Trip", 4)), ("Movie CD2", ("Movie", 2)), ("Clip 01", ("Clip", 1)),
    ("Trip", None),
    ("Trip.concat", None), ("Trip.concat2", None),  # concatvid's own outputs
])
def test_split_sequence(stem, expected):
    assert concat.split_sequence(stem) == expected


def _probe(vcodec="h264", w=1920, h=1080, acodec="aac", rate="48000", ch=2, extra=(),
           cover=False, duration="10.0"):
    streams = [{"codec_type": "video", "codec_name": vcodec, "width": w, "height": h,
                "pix_fmt": "yuv420p"}]
    if cover:
        streams.append({"codec_type": "video", "codec_name": "mjpeg", "width": 300,
                        "height": 300, "disposition": {"attached_pic": 1}})
    if acodec:
        streams.append({"codec_type": "audio", "codec_name": acodec, "sample_rate": rate,
                        "channels": ch})
    streams += list(extra)
    return {"streams": streams, "format": {"duration": duration}}


def test_parse_streams_ignores_cover_art():
    with_cover = concat.parse_streams(_probe(cover=True))
    without = concat.parse_streams(_probe())
    assert with_cover == without
    assert without.duration == 10.0 and not without.has_extra_streams


def test_find_incompatibilities_reports_each_property():
    a = concat.parse_streams(_probe())
    b = concat.parse_streams(_probe(w=1280, h=720, ch=6))
    assert concat.find_incompatibilities([a, b]) == (True, ["audio channels"])
    c = concat.parse_streams(_probe(extra=[{"codec_type": "audio", "codec_name": "ac3"}]))
    assert concat.find_incompatibilities([a, c])[1] == ["track layout"]


def test_audio_presence_mismatch():
    with_audio = concat.parse_streams(_probe())
    silent = concat.parse_streams(_probe(acodec=None))
    assert concat.audio_presence_mismatch([with_audio, silent])
    assert not concat.audio_presence_mismatch([with_audio, with_audio])


def test_choose_target_takes_smallest_resolution_and_majority_formats():
    parts = [concat.parse_streams(p) for p in (
        _probe(w=1920, h=1080), _probe(w=1280, h=720, acodec="ac3"), _probe(vcodec="vp9"),
    )]
    target = concat.choose_target(parts)
    assert (target.width, target.height) == (1280, 720)
    assert (target.codec, target.vcodec, target.acodec) == ("h264", "h264", "aac")


def test_choose_target_falls_back_for_codecs_without_an_encoder():
    target = concat.choose_target([concat.parse_streams(_probe(vcodec="vp9"))])
    assert target.codec == "h264"


def test_audio_only_mismatch_copies_the_video():
    target = concat.choose_target([concat.parse_streams(_probe())])
    part = concat.parse_streams(_probe(ch=6))
    cmd = concat.normalize_command(Path("in.mp4"), Path("out.mp4"), part, target, encoder=None)
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-ac") + 1] == "2"
    assert cmd[cmd.index("-b:a") + 1] == "192k"


def test_audio_reencode_tolerates_missing_channels_and_rate():
    # ffprobe can omit sample_rate/channels; that used to crash (96 * None) or hand
    # ffmpeg "-ac None".
    target = concat.choose_target([concat.parse_streams(_probe(acodec="ac3", rate=None, ch=None))])
    part = concat.parse_streams(_probe(acodec="mp3", rate=None, ch=None))
    cmd = concat.normalize_command(Path("in.mp4"), Path("out.mp4"), part, target, encoder=None)
    assert "None" not in cmd
    assert "-ac" not in cmd and "-ar" not in cmd
    assert cmd[cmd.index("-b:a") + 1] == "192k"


def test_video_reencode_without_an_encoder_is_refused():
    target = concat.choose_target([concat.parse_streams(_probe(w=1280, h=720))])
    part = concat.parse_streams(_probe())
    with pytest.raises(ValueError):
        concat.normalize_command(Path("a.mp4"), Path("b.mp4"), part, target, encoder=None)


# ── downscalevid ───────────────────────────────────────────────────────


@pytest.mark.parametrize("spec, expected", [
    ("720p", (None, 720)), ("1080", (None, 1080)), ("900", (None, 900)),
    ("1920x1080", (1920, 1080)), ("4k", (None, 2160)),
])
def test_parse_resolution(spec, expected):
    assert parse_resolution(spec) == expected


def test_parse_resolution_rejects_garbage():
    with pytest.raises(SystemExit):
        parse_resolution("big")
