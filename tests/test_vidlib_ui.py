"""A smoke test of vidlib's web page in a real browser. Skipped where Playwright isn't
installed (CI doesn't install it); run it locally after changing the page's script:

    uv run --with playwright pytest tests/test_vidlib_ui.py

CHROMIUM_PATH points it at a Chromium binary other than the one Playwright downloads.
"""

from __future__ import annotations

import os
import sys

import pytest

from toolboxcli.vidlib.db import LibraryDB, Video
from toolboxcli.vidlib.downloads import DownloadManager
from toolboxcli.vidlib.server import VidlibServer

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def page_url(tmp_path):
    with LibraryDB.open(tmp_path) as db:
        db.upsert(Video(path="Lemonade Stand/DougDoug, Atrioc - Old - Lemonade Stand.mp4"))
    db = LibraryDB.open(tmp_path, shared=True)
    manager = DownloadManager(tmp_path, db)
    server = VidlibServer(manager, port=0)
    manager.start()
    server.start()
    yield server.url
    server.stop()
    manager.shutdown()
    db.close()


def test_naming_a_title_on_the_page(page_url):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"))
        except Exception as exc:  # noqa: BLE001 — no browser binary: nothing to drive
            pytest.skip(f"no Chromium for Playwright: {exc}")
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(page_url)
        page.fill("#url", "https://example.com/v")
        page.fill("#page-title", "Lemonade Stand - The Conspiracy Episode - dougdoug - Atri-oc")
        stem = page.locator("#stem")
        sync_api.expect(stem).to_have_text(
            "DougDoug, Atri-oc - The Conspiracy Episode - Lemonade Stand.ext")
        sync_api.expect(page.locator(".segment")).to_have_count(4)
        sync_api.expect(page.locator("#add-btn")).to_be_enabled()

        # The loose match offers the library's spelling; accepting it rewrites the name.
        page.get_by_role("button", name="did you mean Atrioc?").click()
        sync_api.expect(stem).to_have_text(
            "DougDoug, Atrioc - The Conspiracy Episode - Lemonade Stand.ext")

        # Making the title segment the collection demotes the old collection to title.
        page.get_by_role("button", name="The Conspiracy Episode: collection").click()
        sync_api.expect(stem).to_have_text(
            "DougDoug, Atri-oc - Lemonade Stand - The Conspiracy Episode.ext")

        # Skipping the people leaves the name incomplete: Add gives way to "Download anyway".
        page.get_by_role("button", name="dougdoug: skip").click()
        page.get_by_role("button", name="Atri-oc: skip").click()
        sync_api.expect(page.locator("#problems")).to_contain_text("no people")
        sync_api.expect(page.locator("#add-btn")).to_be_disabled()
        sync_api.expect(page.locator("#add-anyway")).to_be_visible()
        assert errors == []
        browser.close()


@pytest.fixture
def library_url(tmp_path):
    root = tmp_path / "lib"
    (root / "Lakes").mkdir(parents=True)
    with LibraryDB.open(root) as db:
        for path in ("Lakes/Alice - Day 1 - Lakes [720p].mp4", "Lakes/Bob - Swim - Lakes.mp4"):
            (root / path).write_bytes(b"x")
            db.upsert(Video(path=path, height=720))
    db = LibraryDB.open(root, shared=True)
    manager = DownloadManager(root, db)
    server = VidlibServer(manager, port=0)
    manager.start()
    server.start()
    yield server.url, root
    server.stop()
    manager.shutdown()
    db.close()


def test_renaming_from_the_library_page(library_url):
    url, root = library_url
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"))
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"no Chromium for Playwright: {exc}")
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "#library")
        sync_api.expect(page.locator(".card")).to_have_count(2)
        # Typing only narrows the person list; Enter picks the highlighted one.
        page.click("#lib-person")
        page.keyboard.type("bo")
        sync_api.expect(page.locator("#lib-person-list li")).to_have_text(["Bob 1"])
        sync_api.expect(page.locator(".card")).to_have_count(2)
        page.keyboard.press("Enter")
        sync_api.expect(page.locator(".card")).to_have_count(1)
        sync_api.expect(page.locator("#lib-person")).to_have_value("Bob")
        page.locator(".card").first.click()
        sync_api.expect(page.locator("#detail")).to_be_visible()
        sync_api.expect(page.locator("#rn-save")).to_be_disabled()        # nothing changed yet
        page.fill("#rn-title", "Swim at dawn")
        sync_api.expect(page.locator("#rn-stem")).to_have_text("Bob - Swim at dawn - Lakes")
        page.click("#rn-save")
        sync_api.expect(page.locator("#detail-msg")).to_contain_text("Renamed")
        assert (root / "Lakes" / "Bob - Swim at dawn - Lakes.mp4").exists()
        assert errors == []
        browser.close()


def test_pills_fill_the_fields_and_the_dialog_closes_easily(library_url):
    url, root = library_url
    (root / "Alice and Bob swimming in the Lakes.mp4").write_bytes(b"x")
    with LibraryDB.open(root) as db:
        db.upsert(Video(path="Alice and Bob swimming in the Lakes.mp4", height=480))
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"))
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"no Chromium for Playwright: {exc}")
        page = browser.new_page(viewport={"width": 900, "height": 500})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "#library")
        page.click(".seg button[data-view='unnamed']")
        sync_api.expect(page.locator(".card")).to_have_count(1)
        page.locator(".card").first.click()
        pills = page.locator("#rn-people-pills")
        # Everything starts in the title; the known names are offered as pills.
        page.locator("#rn-people-pills").get_by_role("button", name="Alice").click()
        sync_api.expect(page.locator("#rn-people")).to_have_value("Alice")
        sync_api.expect(page.locator("#rn-title")).to_have_value("Bob swimming in the Lakes")
        pills.get_by_role("button", name="Bob").click()
        # The empty collection previews as "Unknown", which is what Rename would use.
        sync_api.expect(page.locator("#rn-stem")).to_have_text(
            "Alice, Bob - swimming in the Lakes - Unknown")
        page.locator("#rn-collection-pills").get_by_role("button", name="Lakes").click()
        sync_api.expect(page.locator("#rn-stem")).to_have_text(
            "Alice, Bob - swimming in the - Lakes")
        # The fill-ins replace a field outright.
        page.locator("#rn-title-pills").get_by_role("button", name="Unknown").click()
        page.locator("#rn-collection-pills").get_by_role("button", name="Clips").click()
        sync_api.expect(page.locator("#rn-stem")).to_have_text("Alice, Bob - Unknown - Clips")

        # The Trash confirmation scrolls into view; Close sits beside Rename.
        page.click("#detail-trash")
        sync_api.expect(page.locator("#detail-confirm-yes")).to_be_in_viewport()
        page.click("#detail-confirm-no")
        page.click("#rn-close")
        sync_api.expect(page.locator("#detail")).to_be_hidden()

        # A click on the backdrop closes it too.
        page.locator(".card").first.click()
        sync_api.expect(page.locator("#detail")).to_be_visible()
        page.mouse.click(5, 5)
        sync_api.expect(page.locator("#detail")).to_be_hidden()

        # The add-a-download form offers the same pills.
        page.goto(url + "#downloads")
        page.fill("#url", "https://example.com/v")
        page.fill("#page-title", "Carol - Bob hiking - Peaks")
        page.locator("#people-pills").get_by_role("button", name="Bob").click()
        sync_api.expect(page.locator("#stem")).to_have_text("Carol, Bob - hiking - Peaks.ext")
        page.locator("#collection-pills").get_by_role("button", name="Clips").click()
        sync_api.expect(page.locator("#stem")).to_have_text("Carol, Bob - hiking - Clips.ext")
        assert errors == []
        browser.close()


@pytest.fixture
def parts_url(tmp_path):
    from toolboxcli.vidlib.status import PipelineStatus
    root = tmp_path / "lib"
    (root / "Trips").mkdir(parents=True)
    with LibraryDB.open(root) as db:
        for path in ("Trips/Alice - Lake P1 - Trips.mp4", "Trips/Alice - Lake P2 - Trips.mp4",
                     "Odd clip.mp4", "Another clip.mp4"):
            (root / path).write_bytes(b"x")
            db.upsert(Video(path=path, height=720, duration=60))
    db = LibraryDB.open(root, shared=True)
    manager = DownloadManager(root, db)
    status = PipelineStatus(on_change=manager.notify)   # as cli wires it
    server = VidlibServer(manager, port=0, pipeline=status)
    manager.start()
    server.start()
    yield server.url, status
    server.stop()
    manager.shutdown()
    db.close()


def test_merging_from_the_parts_view_and_by_hand(parts_url):
    url, status = parts_url
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"))
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"no Chromium for Playwright: {exc}")
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "#library")
        page.click(".seg button[data-view='parts']")
        page.get_by_role("button", name="Merge…").click()
        sync_api.expect(page.locator("#mg-title")).to_have_value("Lake")
        sync_api.expect(page.locator("#mg-stem")).to_have_text("Alice - Lake - Trips")
        sync_api.expect(page.locator(".merge-part")).to_have_count(2)
        # Reorder, then put it back.
        page.get_by_role("button", name="Move Alice - Lake P1 - Trips.mp4 down").click()
        sync_api.expect(page.locator(".merge-part").first).to_contain_text("P2")
        page.get_by_role("button", name="Move Alice - Lake P1 - Trips.mp4 up").click()
        sync_api.expect(page.locator("#mg-go")).to_be_enabled()
        page.click("#mg-go")
        sync_api.expect(page.locator("#merge")).to_be_hidden()
        [job] = status.snapshot()["merges"]
        assert job["state"] == "queued" and job["name"] == "Alice - Lake - Trips.mp4"
        assert job["paths"] == ["Trips/Alice - Lake P1 - Trips.mp4", "Trips/Alice - Lake P2 - Trips.mp4"]

        # By hand: pick two cards in the grid.
        page.click(".seg button[data-view='all']")
        sync_api.expect(page.locator("#lib-grid .card")).to_have_count(4)
        page.get_by_role("checkbox", name="Select Another clip.mp4 to merge").check()
        page.get_by_role("checkbox", name="Select Odd clip.mp4 to merge").check()
        sync_api.expect(page.locator("#select-count")).to_have_text("2 selected")
        page.click("#select-merge")
        sync_api.expect(page.locator(".merge-part")).to_have_count(2)
        sync_api.expect(page.locator(".merge-part").first).to_contain_text("Another clip.mp4")
        assert errors == []
        browser.close()


# Progress every 50 ms, like a fast download: the page gets a new state every quarter second.
BUSY_YTDLP = """\
#!{python}
import time
for i in range(600):
    print(f"[download]  {{i / 6:.1f}}% of 1.00MiB at 3.00MiB/s ETA 00:10", flush=True)
    time.sleep(0.05)
"""


@pytest.mark.skipif(os.name == "nt", reason="fake yt-dlp is a POSIX script")
def test_cancel_works_while_progress_streams_in(page_url, tmp_path, monkeypatch):
    # Every state update used to rebuild the rows, so the Cancel button was replaced
    # between mousedown and mouseup and the click never landed.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "yt-dlp"
    script.write_text(BUSY_YTDLP.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"))
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"no Chromium for Playwright: {exc}")
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(page_url)
        page.evaluate("""() => fetch("/api/downloads", {method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({url: "https://example.com/busy", name: "A - B - C"})})""")
        row = page.locator("#rows-active .row")
        sync_api.expect(row.locator(".pill")).to_have_text("downloading")
        page.wait_for_timeout(500)
        row.get_by_role("button", name="Cancel").hover()
        page.mouse.down()
        page.wait_for_timeout(400)      # at least one state update between down and up
        page.mouse.up()
        sync_api.expect(page.locator("#rows-done .row .pill")).to_have_text("cancelled")
        assert errors == []
        browser.close()


def test_the_header_shows_downloads_and_encoding_apart(parts_url):
    url, status = parts_url
    status.begin_batch(["a.mp4", "b.mp4", "c.mp4", "d.mp4"])
    for name in ("a.mp4", "b.mp4"):
        status.begin_file(name)
        status.end_file({"file": name, "status": "sorted"})
    status.begin_file("c.mp4")
    status.encoding("preset", "x265")
    status.progress(50)
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"))
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"no Chromium for Playwright: {exc}")
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        sync_api.expect(page.locator("#overall-counts")).to_have_text("Nothing queued")
        # The whole batch: 2 of 4 done and half of the third is 62.5%.
        sync_api.expect(page.locator("#encode-counts")).to_have_text("file 3 of 4 · 63%")
        assert page.locator("#encode-bar").evaluate("e => e.style.width") == "62.5%"
        status.end_file({"file": "c.mp4", "status": "sorted"})
        status.end_batch()
        sync_api.expect(page.locator("#encode-counts")).not_to_contain_text("file")
        assert errors == []
        browser.close()


@pytest.mark.skipif(os.name == "nt", reason="fake yt-dlp is a POSIX script")
def test_a_skipped_segment_is_skipped_next_time(page_url, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "yt-dlp"
    script.write_text(BUSY_YTDLP.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"))
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"no Chromium for Playwright: {exc}")
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(page_url)
        stem = page.locator("#stem")
        page.fill("#url", "https://example.com/one")
        page.fill("#page-title", "Carol - Hike - Peaks - Promo Clip")
        sync_api.expect(stem).to_have_text("Carol - Hike - Peaks - Promo Clip.ext")
        page.get_by_role("button", name="Promo Clip: skip").click()
        page.get_by_role("button", name="Peaks: collection").click()
        sync_api.expect(stem).to_have_text("Carol - Hike - Peaks.ext")
        page.click("#add-btn")
        sync_api.expect(page.locator("#page-title")).to_have_value("")
        page.fill("#url", "https://example.com/two")
        page.fill("#page-title", "Dan - Ski - Alps - Promo Clip")
        sync_api.expect(stem).to_have_text("Dan - Ski - Alps.ext")
        sync_api.expect(page.get_by_role("button", name="Promo Clip: skip")).to_have_attribute(
            "aria-pressed", "true")
        assert errors == []
        browser.close()
