"""vidlib/index.py: thumbnails, hover previews and content fingerprints in one ffmpeg run,
the cache they live in, and the background indexer — on small clips made with ffmpeg."""

from __future__ import annotations

import os
import threading
import time

from conftest import needs

from toolboxcli.vidlib import core, index
from toolboxcli.vidlib.db import LibraryDB, Video
from toolboxcli.vidlib.library import Library


@needs("ffmpeg")
def test_one_run_makes_the_thumbnail_the_filmstrip_and_the_fingerprint(tmp_path, clip):
    small = clip(tmp_path / "small.mp4", "testsrc2")
    big = clip(tmp_path / "big.mkv", "testsrc2", "640x360")
    other = clip(tmp_path / "other.mp4", "mandelbrot")
    thumb, strip = tmp_path / "t.jpg", tmp_path / "s.jpg"
    fp = index.render(small, 12.0, thumb, strip)
    assert thumb.read_bytes()[:2] == strip.read_bytes()[:2] == b"\xff\xd8"
    assert len(core.parse_fingerprint(fp)) == core.FP_FRAMES
    # The same picture at another size and in another container is the same video;
    # another picture isn't. Fingerprint alone: no images asked for, none made.
    assert core.same_content(fp, 12.0, index.render(big, 12.0), 12.0) is True
    assert core.same_content(fp, 12.0, index.render(other, 12.0), 12.0) is False
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".jpg") == ["s.jpg", "t.jpg"]


@needs("ffmpeg")
def test_a_length_that_overshoots_the_file_still_gets_everything(tmp_path, clip):
    # The recorded length says 20s but the file is 8s: the late seeks land past the end,
    # which stops the one-run graph, so each frame is read on its own instead.
    video = clip(tmp_path / "short.mp4", "life", seconds=8)
    thumb, strip = tmp_path / "t.jpg", tmp_path / "s.jpg"
    fp = index.render(video, 20.0, thumb, strip)
    hashes = core.parse_fingerprint(fp)
    assert len(hashes) == core.FP_FRAMES and hashes[0] is not None
    assert thumb.is_file() and strip.is_file()


def test_an_unreadable_file_gets_nothing(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    thumb = tmp_path / "t.jpg"
    assert index.render(bad, 30.0, thumb, tmp_path / "s.jpg") == ""
    assert not thumb.exists()


def test_the_cache_is_named_by_size_and_mtime_not_path(tmp_path):
    thumb, strip = index.cache_paths(tmp_path, 1000, 12.5)
    assert thumb.parent == tmp_path / index.CACHE_DIR / "thumbs"
    assert strip.name == thumb.stem + f"-strip{index.STRIP_FRAMES}.jpg"
    assert index.cache_paths(tmp_path, 1000, 12.5) == (thumb, strip)
    assert index.cache_paths(tmp_path, 1001, 12.5)[0] != thumb


def test_needs_and_prune(tmp_path):
    folder = index.thumbs_dir(tmp_path)
    folder.mkdir(parents=True)
    keep = Video(path="a.mp4", size=10, mtime=1.0, duration=60.0, fingerprint="x")
    thumb, strip = index.cache_paths(tmp_path, 10, 1.0)
    thumb.write_bytes(b"t")
    assert index.needs(keep, index.cached_names(tmp_path))          # no filmstrip yet
    strip.write_bytes(b"s")
    assert not index.needs(keep, index.cached_names(tmp_path))
    assert index.needs(Video(path="a.mp4", size=10, mtime=1.0, duration=60.0),
                       index.cached_names(tmp_path))                 # no fingerprint
    assert not index.needs(Video(path="b.mp4", size=10, mtime=1.0),
                           index.cached_names(tmp_path))             # no length: thumb only
    (folder / "0123-old-path-keyed.jpg").write_bytes(b"x" * 50)     # a video that's gone
    (folder / ".being-written.tmp.jpg").write_bytes(b"x")
    assert index.prune_cache(tmp_path, [keep]) == (1, 50)
    assert sorted(os.listdir(folder)) == sorted(
        [".being-written.tmp.jpg", thumb.name, strip.name])
    assert index.cache_size(tmp_path) == 3


@needs("ffmpeg")
def test_prepare_stores_the_fingerprint_and_a_rename_keeps_the_images(tmp_path, clip):
    root = tmp_path / "lib"
    video = clip(root / "Lakes" / "Alice - Day 1 - Lakes.mp4", "cellauto")
    st = video.stat()
    with LibraryDB.open(root) as db:
        db.upsert(Video(path="Lakes/Alice - Day 1 - Lakes.mp4", duration=12.0,
                        size=st.st_size, mtime=st.st_mtime))
        lib = Library(root, db, threading.RLock())
        row = db.get("Lakes/Alice - Day 1 - Lakes.mp4")
        assert row is not None and lib.prepare(row)
        row = db.get("Lakes/Alice - Day 1 - Lakes.mp4")
        assert row is not None and row.fingerprint
        assert not index.needs(row, index.cached_names(root))
        # Filed elsewhere under a new name: same size and mtime, so nothing to redo.
        moved = root / "Peaks" / "Alice - Day 1 - Peaks.mp4"
        moved.parent.mkdir()
        video.rename(moved)
        db.move(row.path, "Peaks/Alice - Day 1 - Peaks.mp4")
        moved_row = db.get("Peaks/Alice - Day 1 - Peaks.mp4")
        assert moved_row is not None and not index.needs(moved_row, index.cached_names(root))
        assert lib.thumbnail("Peaks/Alice - Day 1 - Peaks.mp4")[:2] == b"\xff\xd8"


@needs("ffmpeg")
def test_the_indexer_prepares_everything_then_rests(tmp_path, clip):
    root = tmp_path / "lib"
    paths = []
    for n, source in enumerate(("testsrc2", "life")):
        video = clip(root / "C" / f"P - T{n} - C.mp4", source)
        st = video.stat()
        paths.append((f"C/P - T{n} - C.mp4", st))
    (root / "C" / "broken.mp4").write_bytes(b"nope")
    with LibraryDB.open(root, shared=True) as db:
        for path, st in paths:
            db.upsert(Video(path=path, duration=12.0, size=st.st_size, mtime=st.st_mtime))
        db.upsert(Video(path="C/broken.mp4", duration=12.0, size=4,
                        mtime=(root / "C" / "broken.mp4").stat().st_mtime))
        lock = threading.RLock()
        lib = Library(root, db, lock)
        changes = threading.Event()

        def videos() -> list[Video]:
            with lock:
                return db.all()

        indexer = index.Indexer(root, videos, lib.prepare, on_change=changes.set)
        indexer.start()
        try:
            end = time.monotonic() + 60
            while time.monotonic() < end:
                with lock:
                    done = all(r.fingerprint for r in db.all() if r.path != "C/broken.mp4")
                if done and indexer.snapshot()["total"] == 0:
                    break
                time.sleep(0.1)
            assert done and changes.is_set()
            # The unreadable file isn't tried again and again.
            assert [v.path for v in indexer.pending()] == []
        finally:
            indexer.stop()
        assert indexer.stopping
