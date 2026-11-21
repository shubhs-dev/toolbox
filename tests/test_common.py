"""Shared _common helpers."""

from __future__ import annotations

import os

import pytest

from toolboxcli._common import output, streams
from toolboxcli._common.ffprobe import bit_depth
from toolboxcli._common.pathsafe import sanitize_for_path

# ── ffprobe.bit_depth ──────────────────────────────────────────────────


@pytest.mark.parametrize("pix_fmt, raw, depth", [
    ("yuv420p", None, 8), ("yuv420p10le", None, 10), ("yuv422p12be", None, 12),
    ("yuv410p", None, 8), ("yuv411p", None, 8),  # digits in an 8-bit layout's name
    ("p010le", None, 10), ("p016le", None, 16),
    ("yuv420p", "10", 10), ("", None, 8), ("yuv420p10le", "N/A", 10),
])
def test_bit_depth(pix_fmt, raw, depth):
    assert bit_depth(pix_fmt, raw) == depth


# ── pathsafe ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw, clean", [
    ('Re: Zero? "Part" 1/2', "Re Zero Part 12"), ("  name.  ", "name"), ("a  b", "a b"),
])
def test_sanitize_for_path(raw, clean):
    assert sanitize_for_path(raw) == clean


# ── streams ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("codec, ext, expected", [
    ("subrip", ".mkv", "copy"), ("mov_text", ".mkv", "srt"),
    ("subrip", ".mp4", "mov_text"), ("mov_text", ".mp4", "copy"),
    ("hdmv_pgs_subtitle", ".mp4", None),
    ("subrip", ".webm", "webvtt"), ("hdmv_pgs_subtitle", ".webm", None),
    ("subrip", ".avi", None),
])
def test_subtitle_codec_for(codec, ext, expected):
    assert streams.subtitle_codec_for(codec, ext) == expected


PROBED = [
    {"index": 0, "codec_type": "video", "codec_name": "h264"},
    {"index": 1, "codec_type": "audio", "codec_name": "aac"},
    {"index": 2, "codec_type": "audio", "codec_name": "ac3"},
    {"index": 3, "codec_type": "subtitle", "codec_name": "subrip"},
    {"index": 4, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"},
    {"index": 5, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
    {"index": 6, "codec_type": "attachment", "codec_name": "ttf"},
    {"index": 7, "codec_type": "data", "codec_name": "tmcd"},
]


def test_keep_all_streams_into_mkv_keeps_everything_but_cover_art_and_data():
    assert streams.keep_all_streams_args(PROBED, ".mkv") == [
        "-map", "0:0", "-map", "0:1", "-map", "0:2", "-map", "0:3", "-map", "0:4",
        "-map", "0:6",
    ]


def test_keep_all_streams_into_mp4_converts_text_subs_and_drops_the_rest():
    assert streams.keep_all_streams_args(PROBED, ".mp4") == [
        "-map", "0:0", "-map", "0:1", "-map", "0:2", "-map", "0:3", "-c:s:0", "mov_text",
    ]


def test_main_video_streams_skips_cover_art():
    assert [s["index"] for s in streams.main_video_streams(PROBED)] == [0]


# ── output ─────────────────────────────────────────────────────────────


def test_is_same_file_however_spelled(tmp_path, monkeypatch):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    assert output.is_same_file(f, f.relative_to(tmp_path))
    assert output.is_same_file(f, tmp_path / "sub" / ".." / "clip.mp4")
    if hasattr(os, "link"):
        os.link(f, tmp_path / "hard.mp4")
        assert output.is_same_file(f, tmp_path / "hard.mp4")
    assert not output.is_same_file(f, tmp_path / "other.mp4")


def test_check_output_path(tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    with pytest.raises(SystemExit):
        output.check_output_path(src, src, force=True)       # never the input
    with pytest.raises(SystemExit):
        output.check_output_path(src, tmp_path / "missing" / "o.mp4", force=False)
    existing = tmp_path / "out.mp4"
    existing.write_bytes(b"old")
    with pytest.raises(SystemExit):
        output.check_output_path(src, existing, force=False)
    output.check_output_path(src, existing, force=True)
    output.check_output_path(src, tmp_path / "new.mp4", force=False)


def test_atomic_output_replaces_only_on_success(tmp_path, monkeypatch):
    trashed = []
    monkeypatch.setattr(output, "move_to_trash", trashed.append)
    dest = tmp_path / "out.mp4"
    dest.write_bytes(b"old")

    with pytest.raises(RuntimeError), output.atomic_output(dest, force=True) as tmp:
        tmp.write_bytes(b"half")
        raise RuntimeError("tool failed")
    assert dest.read_bytes() == b"old" and not trashed
    assert [p.name for p in tmp_path.iterdir()] == ["out.mp4"]  # temp file cleaned up

    with output.atomic_output(dest, force=True) as tmp:
        assert tmp.suffix == ".mp4"  # ffmpeg picks the muxer from the extension
        tmp.write_bytes(b"new")
    assert dest.read_bytes() == b"new"
    assert trashed == [dest]


# ── handbrake presets ──────────────────────────────────────────────────


def test_list_presets_reads_the_bundled_presets(capsys):
    # The bundled directory is an importlib.resources Traversable, which only promises
    # iterdir(); listing it used to rely on glob().
    from toolboxcli._common import handbrake
    handbrake.list_presets()
    out = capsys.readouterr().out
    assert "hw-1080" in out and "hw-720" in out

