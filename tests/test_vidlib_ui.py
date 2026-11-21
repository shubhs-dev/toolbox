"""A smoke test of vidlib's web page in a real browser. Skipped where Playwright isn't
installed (CI doesn't install it); run it locally after changing the page's script:

    uv run --with playwright pytest tests/test_vidlib_ui.py

CHROMIUM_PATH points it at a Chromium binary other than the one Playwright downloads.
"""

from __future__ import annotations

import os

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
