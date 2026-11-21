# vidlib — plan

`optimiselib` and `video-download` become one tool, **vidlib**: a single long-running process
on the MacBook that owns the library database, runs the encode pipeline, downloads videos,
and serves a web UI on `127.0.0.1`. This file records the decisions behind it and each
stage's scope. It's deleted once the work is done, with the lasting conventions moved into
CLAUDE.md.

## Decisions

### Process and access
- `vidlib [ROOT]` = web server + encode pipeline + downloader in **one process**. The UI
  reads pipeline state in memory; no IPC.
- `-W/--no-web` runs the pipeline headless with today's rich display. Query flags (`-l`,
  `-a`, `-c`, `-R`, `-T`, `-U`, `-D`, `-r`), `-O`, `-n` and `-s` stay terminal one-shots.
- Listens on **`127.0.0.1` only**, fixed default port (changeable by flag), no login.
  Requests whose `Host`/`Origin` isn't the server's own are rejected (CSRF, DNS rebinding).
  Opens the browser on start unless told not to. A busy port is an error naming the flag,
  never a silent random port.
- While anything downloads or encodes, vidlib holds `caffeinate -i -w <pid>` (macOS only).
- Before each poll the library root is checked; when the disk isn't mounted nothing is
  synced or encoded and the UI shows a banner. (Today the root is only checked at startup,
  so an ejected disk marks every video `missing`.)
- No launchd agent for now.

### Stack
- stdlib `http.server` (`ThreadingHTTPServer`) with a small router; live updates over
  Server-Sent Events. No new dependencies.
- Frontend: plain HTML/CSS/JS shipped as package data (`importlib.resources`), no build step.
  Light/dark follows the system.
- Tests: pytest drives the HTTP API against a real server on a random port, with fake
  `yt-dlp` and real database files. A Playwright smoke test runs where Playwright is
  installed and skips elsewhere (CI doesn't install it).

### Naming a download
- The pasted title is split into **segments** on ` - `, spaced `–`/`—`, and `|`. A segment
  after the last `|` defaults to Skip (it's the site name).
- Each segment gets a role: **People**, **Title**, **Collection** or **Skip**. Any number of
  People and Title segments; exactly one Collection (choosing another demotes the old one to
  Title). People segments are split into names on `,` `&` `and` `x` `+`.
- Output: `People - Title - Collection`, people joined with `, `, title segments joined with
  ` - ` in their original order. optimiselib's `parse_name` already reads the middle fields
  back as one title. Order in the pasted title doesn't matter — roles decide placement.
- Role guess: a segment whose names are all known people → People; a segment matching a
  known collection → Collection; the rest by position (first People, last Collection, middle
  Title; two segments People/Title; one segment Title).
- **Exact** match (optimiselib's `normalize`: case and spacing) drives the guess and rewrites
  the name to the library's spelling. **Loose** match (also ignoring punctuation, inner
  spaces and accents) drives the guess but only *suggests* the spelling ("did you mean…").
  Both for people and collections.
- Every name is checked by `parse_name`; Add is disabled until People, Title and Collection
  are all present, with a "download anyway" escape.
- Bulk paste: each URL's title is fetched with `yt-dlp --print title` (metadata only) and
  lands in a **Needs naming** list, pre-segmented, to be named one by one.

### Downloads
- Stored in a `downloads` table in the library database (schema migration), replacing
  `.video-download-queue.json`, which is imported once and renamed `.migrated`.
- Statuses: `queued` → `downloading` → `downloaded`, or `failed` / `cancelled`; then
  `imported`, linked to the video row, once it enters the library.
- Normal mode downloads into `ROOT/_incoming/`, then moves the finished file into the root
  as pipeline input. `_incoming/` is **excluded from the library scan** — without that,
  `sync_library` records an unknown file in any subfolder as already sorted, and it would
  never be encoded.
- `--download-only [DIR]` serves only downloads, into `DIR` (default: current folder),
  leaves them `downloaded`, and never encodes. `--library PATH` (flag → `VIDLIB_LIBRARY` →
  default) says where the database lives, so history and name suggestions stay central.
  Later the UI offers to move waiting downloads into the library (size checked after a
  cross-disk copy, source removed only then), marking them `imported`. Files moved by hand
  into the root still get encoded, just unlinked from their download row.
- 2 parallel downloads by default (flag); they keep running during encodes and ignore the
  off-hours window.

### UI pages
- **Downloads**: add form (URL + title → segment chips → name preview, suggestions), bulk
  paste / Needs naming, the queue with per-item progress, speed, ETA, retry / cancel /
  remove, an overall bar.
- **Pipeline**: current encode (file, preset, encoder, progress, ETA), what's waiting,
  pause/resume (finish current file) and cancel-current. `-H`/`-L` state shown, not edited.
- **Library**: filters (people, collection, resolution, tag, search), duplicates and
  unsorted-name lists, thumbnails (lazy ffmpeg frame, cached in `ROOT/.vidlib-cache/`),
  per-video play (`open`), reveal in Finder, rename (segment UI; moves to the new collection
  folder via the database so history follows), move to Trash (confirmed).
- **Review**: each `_review/` item next to its library copy (resolution, depth, duration,
  size, tags, audio tracks); keep new / keep old / keep both (rename one first).

### Rename and removal
- `optimiselib` → `vidlib`, clean break, no aliases. `video-download` goes once vidlib's
  downloads land (stage 2), along with batch mode (`urls.txt`), the Textual TUI and the
  `textual` dependency.
- `.optimiselib.db` (+ `-wal`/`-shm`) is renamed `.vidlib.db` on first open. If both exist
  the old one is left alone with a warning.

## Stages

Each stage is one PR, reviewed and merged before the next starts. Lint, pyright and pytest
pass at every stage.

1. **Rename.** `optimiselib` → `vidlib` (package, command, tests, docs, `toolbox` list),
   database file rename, `_incoming/` excluded from the scan. Behaviour otherwise
   unchanged; `video-download` keeps working against the renamed module so there's no gap
   without a downloader.
   *Done when*: every former `optimiselib` test passes as `vidlib`; an old `.optimiselib.db`
   is picked up and renamed; a video in `_incoming/` is never recorded.
2. **Server and downloads.** HTTP server + SSE, the segment naming core with exact/loose
   matching, the `downloads` table and queue import, normal and `--download-only` modes,
   move-into-library, bulk paste. Removes `video-download`, batch mode, the TUI and
   `textual`.
   *Done when*: a URL named in the UI downloads (fake yt-dlp) into `_incoming/` and lands in
   the root; download-only leaves it `downloaded` in DIR and the move button imports it;
   requests with a foreign Host/Origin are refused.
3. **Pipeline in the server.** The poll loop runs in a thread of the server process,
   reporting through callbacks instead of the rich display; Pipeline page with live
   progress, pause/resume/cancel; disk-missing guard; caffeinate. `-W` keeps the headless
   display.
   *Done when*: an encode's progress reaches the page; pause holds the next file; an
   unmounted root marks nothing missing.
4. **Library page.** Queries over HTTP, thumbnails, play / reveal / rename / trash.
5. **Review page.** Side-by-side comparison and the three decisions.
