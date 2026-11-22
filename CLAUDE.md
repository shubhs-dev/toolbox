# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A collection of CLI tools for media processing, file organization, and game modding, all
written in Python and packaged as a single distribution installed with uv
(`shubhs-toolbox`, importable as `toolboxcli`). There is no build step beyond standard Python
packaging. Tests live in `tests/` (pytest).

Primarily used on Windows via Git Bash/MSYS2 and on macOS. Once installed with
`uv tool install --managed-python -e .`, every script is a real executable on `PATH` — `addsub`, `autosub`,
`check-deps`, `compressvid`, `concatvid`, `convertimg`, `cutvid`, `cybermod`,
`downscalevid`, `finddupes`, `flatten`, `flipvid`, `jellyname`, `kavitaname`,
`mergemanga`, `prunelib`, `sortmedia`, `toolbox`, `vidlib`, `wallready` —
identically on both platforms. `inventory.html` is a standalone static HTML dashboard,
unrelated to the Python package.

## Commands

```bash
# Install (editable, on a uv-managed Python), or reinstall after pyproject.toml changes
# (dependencies, entry points). Source edits need no reinstall.
uv tool install --managed-python -e . --reinstall

# Run a script directly during development, without going through the installed entry point
# (uses the project's own .venv, created from uv.lock on first use)
uv run python -m toolboxcli.<script>.cli --help

# Smoke-test the *installed* commands after a change to pyproject.toml's entry points
# (tests/test_project.py already runs every command's --help from source)
for cmd in addsub autosub check-deps compressvid concatvid convertimg cutvid cybermod \
           downscalevid finddupes flatten flipvid jellyname kavitaname mergemanga \
           prunelib sortmedia toolbox vidlib wallready; do
  $cmd --help > /dev/null || echo "FAILED: $cmd"
done

# Lint, type-check and test (config in pyproject.toml); all must pass before a change is committed
uv run ruff check src tests
uv run pyright
uv run pytest
```

CI (`.github/workflows/ci.yml`) runs the same three on every push to `main` and every PR:
ruff + pyright once, and pytest on Linux, macOS and Windows × Python 3.11 (the
`requires-python` floor) and 3.14 (what `uv tool install --managed-python` picks). Only
Linux installs HandBrakeCLI, so the encode/sort integration tests run there and skip
elsewhere. Don't merge a PR with a red check; don't skip or disable a test to get green.

`requires-python` tracks the oldest Python still in support (3.11 until its end of life in
October 2027); uv installs its own newer Python, so raising the floor costs users nothing.
Dependabot (`.github/dependabot.yml`) opens monthly PRs for the GitHub Actions and the uv
lock file; they go through CI like any other PR.

ruff is a linter only: `ruff format` is deliberately not used, because it would explode
the paired argument layouts (`"-o", template,` on one line) that make the subprocess
command lists readable. The rule set is correctness-focused (pyflakes, pycodestyle,
bugbear, import order, pyupgrade, pylint errors); don't add complexity/style rule families
without a reason.

### Types

pyright runs in **basic** mode over `src/`, and **strict** for the `*/core.py` modules, `vidlib/naming.py` and `vidlib/parts.py`
(`[tool.pyright]` in pyproject.toml) — those hold the parsing and decision logic and take
plain values, so full types are cheap there. `pythonVersion = "3.11"` matches
`requires-python`, so a stdlib call newer than 3.11 is flagged. ffprobe/JSON payloads are
dynamic: annotate them `dict[str, Any]` / `list[dict[str, Any]]` rather than bare `dict`,
which strict mode reports as unknown. When pyright can't see an invariant the code relies on
(e.g. `plan` is only None in a dry run), narrow with an `assert` that states it, rather than
a `# type: ignore`.

### Tests

- `tests/fixtures/*.json` hold real-world filenames → expected results for the filename
  parsers (`jellyname.json`, `kavitaname.json`). When a new naming pattern turns up, add a
  row there rather than reasoning about the regexes in isolation; the parametrized tests
  pick it up. Both parsers are also checked to be no-ops on their own output, and
  kavitaname's output to parse back to the same numbers.
- The fixtures pin *current* behaviour. A row whose expected value looks wrong is a bug to
  fix in the code (then update the row), not a test to delete.
- `tests/test_integration.py` runs the real commands as subprocesses on throwaway folders
  (`run_cli`, `make_video` and `sandbox_env` in `conftest.py`): videos are generated with
  ffmpeg, HOME points into the test's tmp dir so trashed files never reach the real Trash,
  and tests needing a missing binary (`ffmpeg`, `HandBrakeCLI`) are skipped via `needs()`.
  vidlib's downloads are driven by a fake `yt-dlp` script (POSIX only), never the network.

## Architecture

`vidlib` (formerly `optimiselib`, and also what `video-download` was) is one long-running
process on the user's MacBook, next to a library on an external disk: the encode pipeline on
the main thread, and a web page on `127.0.0.1` (stdlib server threads) with Downloads,
Library and Review tabs. Decisions behind that shape, so they aren't re-litigated:

- One process, so the page reads pipeline state in memory; no IPC, no second daemon.
- Localhost only, no login: it runs on the same machine as the browser. The Host/Origin/JSON
  checks in `server.py` are what keep other websites out — don't loosen them.
- No new dependencies: stdlib HTTP + Server-Sent Events, and a plain HTML/CSS/JS page in
  `vidlib/data/web/` with no build step. The page holds no naming logic; it asks the server.
- Downloads are staged in `_incoming/`, which every library walk prunes; download-only mode
  (`-X DIR`) exists for when the library's disk is full, and its files are imported later
  from the page.
- The library disk can be unplugged at any time: nothing may create a missing target's
  parents (macOS would then mount the real disk under another name), and the loop doesn't
  sync a missing root.
- Batch `urls.txt` downloading and the Textual TUI were dropped on purpose; bulk paste on the
  page replaced them.

### Package layout

```
src/toolboxcli/
├── _common/          # shared modules, imported by every script (see below)
├── addsub/cli.py
├── autosub/cli.py
├── checkdeps/cli.py
├── compressvid/{cli.py, data/handbrake-presets/*.json}
├── concatvid/{cli.py, core.py}
├── convertimg/cli.py
├── cutvid/cli.py
├── cybermod/{cli.py, core.py}
├── downscalevid/{cli.py, core.py}
├── finddupes/cli.py
├── flatten/cli.py
├── flipvid/cli.py
├── jellyname/{cli.py, core.py}
├── kavitaname/cli.py
├── mergemanga/{cli.py, data/volume_map.json}
├── prunelib/{cli.py, core.py}
├── sortmedia/cli.py
├── toolbox/cli.py
├── vidlib/{cli.py, core.py, db.py, naming.py, fetcher.py, downloads.py, server.py, data/web/*}
└── wallready/{cli.py, core.py}
```

Each script is a `<name>/cli.py` with a `main()` function wired up in `pyproject.toml`'s
`[project.scripts]` table as the console-script entry point. `concatvid`, `cybermod`,
`downscalevid`, `jellyname`, `prunelib`, `vidlib` and `wallready` additionally split their logic into
`core.py`, keeping `cli.py`
to argparse wiring, filesystem I/O and subprocess calls only — follow this `cli.py`/`core.py`
split for any future script whose logic grows beyond simple argument-parsing-and-dispatch.

**Bundled data**: `compressvid`'s HandBrake presets and `mergemanga`'s volume map are package
data (declared in `pyproject.toml`'s `[tool.setuptools.package-data]`), resolved at runtime via
`importlib.resources.files(...)` — **never** `Path(__file__).resolve().parent`-relative
lookups, since `__file__` points into an installed environment's site-packages, not the repo.
`kavitaname`'s optional volume-map file is a *user-supplied* path (prompted interactively), not
bundled data — no `importlib.resources` involved there.

`toolbox` lists every script via a hardcoded `SCRIPTS` list of `(name, description)` tuples in
`toolbox/cli.py` — keep it in sync with `pyproject.toml`'s `[project.scripts]` and the README's
scripts table whenever a script is added or removed.

### Shared modules (`src/toolboxcli/_common/`)

Every script should reach for these instead of reimplementing the same logic:

- **`console.py`** — `console` (shared rich `Console`), `info/ok/warn/error(msg)`, `die(msg)`.
  Use these instead of raw `print()`/ANSI codes for any user-facing output.
- **`trash.py`** — `move_to_trash(path)`. The one cross-platform "send to Recycle Bin/Trash"
  implementation (send2trash first, then OS-specific fallback). Never delete a user's file
  outright when a trash mechanism is available.
- **`output.py`** — `is_same_file(a, b)`, `check_output_path(input, output, force)`,
  `atomic_output(output, force)`. For scripts that turn one input into one new output file:
  refuse to write over the input however its path is spelled (comparing `Path` objects misses
  absolute vs relative, symlinks and case-insensitive filesystems — `ffmpeg -y` then truncates
  the input), refuse an existing output without `-f/--force`, and write through a temp file
  that only replaces the destination (old one to the Trash) once the tool has succeeded.
- **`streams.py`** — `keep_all_streams_args(streams, out_ext)`, `main_video_streams(streams)`.
  Explicit `-map` args that keep every video/audio/subtitle track (plus attachments for MKV),
  converting or dropping subtitles the output container can't hold. Without explicit maps
  ffmpeg keeps one track of each type and silently drops the rest.
- **`confirm.py`** — `confirm(prompt, choices="yn", default=None)`. Single-keypress prompt, no
  Enter required; falls back to `input()` when stdin isn't a tty.
- **`humanize.py`** — `human_size(n)`. Byte-size formatting.
- **`progress.py`** — `spinner()` / `bar()`. rich `Progress` factories for indeterminate and
  determinate work respectively.
- **`tooling.py`** — `require_tool(name, install_hint=None)`, `detached_kwargs()`. Check an
  external CLI binary is on `PATH` before shelling out to it; dies with a clear message if
  missing. `detached_kwargs()` are Popen/run kwargs that cut a child off from the terminal
  (no stdin; POSIX: new session; Windows: `CREATE_NO_WINDOW`) — piping stdout/stderr is *not*
  enough to silence a child, because HandBrake's bundled libdvdread writes
  `libdvdread: Can't open file VIDEO_TS.IFO.` straight to the terminal for every input.
  `handbrake.py` and `ffprobe.py` use it. A detached child no longer receives the terminal's
  Ctrl+C, so anything that `Popen().wait()`s on one must terminate it on
  `KeyboardInterrupt` itself (`transcode()` does; `subprocess.run` already kills its child).
- **`handbrake.py`** — `wait_stable(path)`, `load_preset(name)`, `read_preset_settings(name)`,
  `list_presets()`, `transcode(...)`, `available_encoders()`, `presets_dir()`/`set_presets_dir()`.
  Everything HandBrake-related that `compressvid` and `vidlib` share, including the
  stderr-scraping progress parser. `transcode()` takes `extra_args` (CLI flags that override
  preset fields) and `low_priority` (de-prioritises the child process). Preset stems still
  resolve against `compressvid`'s bundled `data/handbrake-presets/` via `importlib.resources`.
- **`encoders.py`** — `select_encoder(codec, preference)`, `cpu_encoder(codec)`,
  `gpu_preference(env_var)`, `Encoder`, `BACKENDS`, `GPU_CHOICES`. GPU/CPU encoder selection
  for every script that re-encodes video (`downscalevid`, `concatvid`). See the encoder-probing
  note under per-script conventions below.

`compressvid` has substantial bespoke rich UI (Panels, Tables, live per-file Progress tasks
driven by parsed HandBrake output) that stays inline in its own `cli.py` rather than being
generalized into `_common` — only genuinely cross-script pieces (trash, byte formatting, the
Progress *factories* themselves) were extracted from it.

### Per-script conventions

- Every script's module docstring is passed to argparse as `description=__doc__` with
  `formatter_class=argparse.RawDescriptionHelpFormatter`, so the docstring **is** the
  `--help` output. Keep it accurate — don't let it drift from the actual flags.
- Every flag has both a short and a long form (e.g. `-y, --yes`); `-h/--help` is always
  argparse's default. Positional arguments don't need a short/long pair.
- External CLI tools the script shells out to (`ffmpeg`, `ffprobe`, `HandBrakeCLI`, `magick`,
  `7z`, `addsub`, `yt-dlp`, `aria2c`) are checked with `_common.tooling.require_tool()` before use, not assumed
  present.
- `mergemanga` deliberately does **not** use `7z` — CBZ files are zip archives, so it uses the
  stdlib `zipfile` module directly (`ZIP_STORED` to match the original's "no compression"
  behavior). Don't reintroduce a 7z dependency there.
- `cybermod`'s Cyberpunk install directory is resolved in this order: `-g/--game-dir` flag →
  `CYBERMOD_GAME_DIR` env var → hardcoded default. There's no "edit the script" story anymore
  since it's installed as a package into site-packages — new configurable paths in any script
  should follow this flag → env var → default pattern rather than a hardcoded constant.
- `cybermod`'s `InstallStatus` (IntEnum: `SUCCESS`/`FAILED`/`SKIPPED`) and `ModRoot` (dataclass:
  `kind` + `path`) replace the original bash script's magic-number return codes and
  stringly-typed `"LOOSE_ARCHIVE:$dir"` sentinel — prefer typed returns like this over string
  sentinels when porting or extending mod-detection logic.
- `_common/encoders.py` picks an encoder by *probing*: it encodes one throwaway lavfi frame
  with the exact encoder + rate-control args it would use, because `ffmpeg -encoders` lists
  everything the build was compiled with regardless of whether the hardware exists. Backends
  (`nvidia`/`amd`/`apple`, tried in a per-platform order; no Intel QSV or VAAPI) each carry
  their own quality args since there's no portable `-crf` for hardware encoders. Selection
  follows the flag → env var → default pattern, with each script owning its env var name and
  passing it to `gpu_preference()` (`-g/--gpu` → `DOWNSCALEVID_GPU`/`CONCATVID_GPU` → `auto`);
  an *auto*-selected GPU that fails mid-encode is retried once on the CPU, an explicitly
  requested one is not. Verify changes here on real hardware — a wrong rate-control flag
  fails the probe and silently demotes the script to CPU encoding.
- `concatvid/core.py` plans a *normalization* pass when a group's parts can't be stream-copy
  concatenated. The target format is a per-property majority vote across the parts, except
  resolution, which is the group's smallest (parts are only ever downscaled), and codecs with
  no encoder in `_common/encoders.py`, which fall back to H.264/AAC. Video and audio are
  decided independently so a part that only disagrees on audio gets `-c:v copy` — don't
  collapse those back into a single "re-encode the whole part" branch, it's the common case
  and the expensive one to get wrong. Normalized parts are mapped to `-map 0:v:0 -map 0:a:0`,
  so when any part carries extra streams *and* a re-encode is needed, every part is normalized
  to keep stream layouts identical. A group where some parts have audio and others don't is
  skipped outright — reconciling it would mean synthesizing silent tracks.
- `concatvid`'s final concat maps tracks explicitly (`-map 0:V -map 0:a? -map 0:s?`; capital
  `V` skips cover art) — the concat demuxer otherwise keeps one track per type. Because the
  demuxer takes its stream list from the first part, a difference in the count *or format*
  of any non-primary track is a mismatch like any other (`StreamInfo.track_layout`) and goes
  through the drop-extras normalization — once every track is mapped, a second audio track
  that's AC3 in one part and AAC in the next would otherwise be copied into one corrupt
  track. Cover art (`attached_pic`) is filtered out in `parse_streams` and never counts.
  Fallback outputs are named `<base>.concat<N>`, and `split_sequence` refuses those stems so
  a later run can't re-concatenate (and trash) concatvid's own outputs. Parts are only trashed after the output's duration is checked against the
  sum of the parts': ffmpeg's concat demuxer **exits 0** when it can't open a part, or when
  a part is truncated but its header still claims the full length — the output just stops
  early. Don't drop that check in favour of the exit code.
- `concatvid` has two modes sharing one `concat_group()`: an explicit file list (joined in the
  order *given*, never re-sorted by sequence number — the user chose that order) with an
  optional `-o/--output`, and `-a/--auto [DIR]`, the folder scan. The scan is opt-in: bare
  `concatvid`, or a lone directory argument, is an error that points at `-a`, rather than
  silently scanning. `-o` is rejected with `-a` because it names one output and a scan can
  produce several. A bare `-o` name lands next to the first part; the extension check is
  against `VIDEO_EXTS`, not `Path.suffix`, so `-o "Trip v1.2"` becomes `Trip v1.2.mp4`.
- `downscalevid` writes through `_common/output.py` like `cutvid`/`flipvid`, but its
  overwrite flag is `-o/--overwrite` because `-f/--force` already meant "allow upscaling"
  — `check_output_path(..., flag=...)` exists so the error message names the right flag.
- `vidlib` deliberately does **not** compose `compressvid` + `sortmedia`, even though it
  does what they do: `compressvid` takes one preset per run and writes to a `compressed/`
  subfolder, while `vidlib` needs a per-file preset and an in-place result; and
  `sortmedia`'s last-whitespace-token rule breaks outright once a ` [1080p]` suffix exists
  (every file would sort into a folder named `[1080p]`). `vidlib` splits on `" - "` and
  takes the last **field** instead, so multi-word trip names work. Both original scripts remain
  the right tools when run by hand — don't "unify" them.
- `vidlib` owns the resolution *and* bit-depth tokens in a filename's trailing `[...]`
  group (`core.apply_tags`): it *replaces* a matching `^\d{3,4}[pi]$` or `^(8|10|12)bit$` token
  rather than appending a second one, while every other token survives verbatim in its original
  order — `[Restored HDR 720p]` becomes `[Restored HDR 1080p 10bit]`. That's what lets an
  externally upscaled file retag itself on the way back through. 8-bit deliberately carries no
  token: it's the norm, and tagging every file would be noise. The tag always describes the
  *finished* file, re-derived from a probe after the encode — so when a transcode is discarded
  for being larger, the tag reverts to the original's real depth rather than the intent. It also
  treats a final `" - Sub"` field as `addsub -u`'s marker rather than a trip name — without
  that guard those files sort into a folder called `Sub`.
- `vidlib/db.py` is the library database (`.vidlib.db`, stdlib `sqlite3`, WAL; the
  library is always on a local disk, never a network share, where SQLite's locking is
  unreliable). It identifies a video by `core.parse_name(...).key` — the *set* of people
  (split on commas, `&` and the whole word `and`, any case), the title and the collection,
  all case- and spacing-insensitive, tags ignored — so reordered people or a different tag
  group don't make a known video look new. Only a *complete* name (all three parts, none
  empty) is split into fields: anything else gets no people, no collection, its whole base
  name as the title and `key is None`, so it never creates a person, matches a duplicate or
  shows up in a person/collection search (`-U` lists them). Schema v1 split every `" - "`
  name, which filled `people` with junk like "Movie"; the v2 step re-derives every row and
  prunes people no video mentions (`prune_people`, also run at the end of each sync). Name-derived columns are recomputed from the path
  on every write and never set directly. The schema is versioned with `PRAGMA user_version`:
  a change to it needs a migration step in `_migrate_schema`, not an edit to `_SCHEMA`.
  `import_json_log` migrates the old `.optimiselib.json` once (renaming it `.migrated`), and
  `LibraryDB.open` renames a library's `.optimiselib.db` (+ `-wal`/`-shm`) to `.vidlib.db`
  when there's no `.vidlib.db` yet — with both present the old one is left alone and warned
  about. `_incoming/` at the root is where downloads are written; every walk of the library
  (`library_files`, `all_videos`, `find_folder`) prunes it, because `sync_library` records any
  unknown file in a subfolder as already sorted — a half-downloaded file would never be
  encoded. The same walks prune any folder holding a `.vidlib-ignore` file (`IGNORE_FILE`):
  the user's way to keep files out of the library. `sync_library` drops rows inside such a
  folder, and rows whose file turns up in one (by size + mtime), instead of marking them
  missing; `_finish` won't file into a collection folder that's marked (the file waits in
  the root, `skipped` with reason `IGNORED_DESTINATION`, not retried), nor will a rename.
- `vidlib`'s "already done" test is `already_done()`: the database knows *this exact
  file* (by path) with a done status — not a guess from the filename's tags, which is what
  let a hand-renamed file be re-encoded before. `sync_library()` keeps paths true before each
  poll: a file at a new path with the exact size + mtime of a vanished row was renamed or
  moved by hand and its row follows it; unknown files in folders (or `_review/`) are recorded
  as already compressed, since the pipeline never touches subfolders; unknown *root* files are
  left as pipeline input; vanished rows become `missing`. Every pipeline write goes through
  `record()`, which re-reads size + mtime from disk — skip that and the next hand rename can't
  be matched. Duplicate lookup (`resolve_duplicate`) is library-wide by match key, ignoring
  `_review/` copies (a decision still waiting on the user). A dry run opens an in-memory copy
  (`LibraryDB.open(root, in_memory=True)`), so a JSON import or sync it does vanishes.
- `vidlib`'s queries (`-l` with `-a/-c/-R/-T`, `-D`) read the database after a
  `sync_library()`, never the filesystem, so they see people/collection/tags as parsed and
  matched by the same rules duplicate detection uses. A filter alone implies `-l`. `-D` is a
  report only — unlike the pipeline's own duplicate handling it never moves or trashes.
- `vidlib` picks its HandBrake encoder at *runtime* from `core.BACKENDS`, overriding only
  `-e`/`-q` on the command line so the bundled presets are never edited: if the preset's own
  encoder is available it's used with **no** override at all. Unlike `ffmpeg -encoders`, which
  is why `_common/encoders.py` probes with a throwaway encode, `HandBrakeCLI --help` omits
  hardware encoders it can't actually use — so parsing it is a real availability signal and no
  probe encode is needed. VideoToolbox is the one backend whose quality scale is inverted
  (0–100, higher better) and must be remapped; passing the presets' CQ 25 straight through
  would silently produce near-worst quality. As with `encoders.py`, an *auto*-selected backend
  that fails mid-encode retries once on CPU; an explicitly requested one does not.
- `vidlib`'s `BatchDisplay` is two `Progress` instances in one `Live` (per-file bars on
  top, the overall bar beneath) rather than one `Progress`, because a `Progress` lists tasks in
  creation order and per-file tasks come and go — in a single `Progress` the overall bar could
  only sit at the top. `transcode()` only ever calls `progress.update(task_id, completed=pct)`,
  so `_EncodeProgress` stands in for the Progress it's given and also feeds each percentage
  into the overall bar; that's what makes the ETA move *during* a long encode. Only one `Live`
  can be active at a time, so `process()` must never open its own progress display.
- Each backend carries a 10-bit encoder alongside its 8-bit one (`vce_h265_10bit`,
  `nvenc_h265_10bit`, `qsv_h265_10bit`, `vt_h265_10bit`, `x265_10bit`), selected on
  `-B/--bit-depth auto` when the *source* is above 8-bit. Deliberately not upconverting 8-bit
  sources: feeding 8-bit through a 10-bit encoder yields a Main 10 file with no more real
  information in it. `already_optimised()` compares against the depth *this invocation* would
  produce, not the file's own content, so `-B 8` re-encodes a file already tagged `10bit`
  instead of treating it as done. 12-bit sources encode as 10-bit — only `x265_12bit` goes higher and no
  hardware encoder does. When the chosen backend has no 10-bit variant in this build it stays
  8-bit and warns, rather than switching to different hardware.
- **No `--encoder-profile` is emitted for 10-bit.** The presets pin `VideoProfile: "main"`,
  which is 8-bit-only and not even a valid value for the 10-bit encoders, but HandBrake
  resolves the mismatch itself — verified: `-e x265_10bit` against a `main` preset still
  produces `Main 10 / yuv420p10le`. Adding an explicit profile override would only be a second
  thing to keep in sync.
- Bit depth is read from ffprobe's `pix_fmt` (`bits_per_raw_sample` is often `N/A` for HEVC).
  The parser requires the `le`/`be` endian suffix that only appears above 8 bits — without
  that, 8-bit layouts whose *names* contain digits (`yuv410p`, `yuv411p`) get misread as high
  bit depth. Semi-planar `p010`/`p012`/`p016` don't fit the rule and are special-cased.
- The bundled presets keep **every** audio track (`AudioTrackSelectionBehavior: "all"`) and
  encode each one to stereo AAC at 160 kbps (`AudioEncoder: "av_aac"`,
  `AudioMixdown: "stereo"`, `AudioBitrate: 160`). Two separate decisions, easily conflated:
  *track selection* was previously `"first"`, silently discarding second-language and
  commentary tracks — don't reintroduce that. *Encoding* is deliberate: passthrough was
  measured at ~28% larger on a PCM+DTS+AAC source, since a DTS track copies through at
  1411 kbps. Surround is downmixed rather than re-encoded as 5.1 because ffmpeg's `av_aac` is
  weak at multichannel — asked for 384 kbps on a 5.1 track it capped at ~250, and HandBrake's
  own default gave it just 125.
- **`AudioBitrate: 0` does not mean "auto"** in a HandBrake preset — it yields roughly 30 kbps
  AAC on real (incompressible) content, which is catastrophic for PCM camcorder audio hitting
  the fallback path. Always set an explicit bitrate. This is easy to get wrong because sine-wave
  test signals compress so well that the bug looks like a plausible bitrate; verify audio
  changes with `anoisesrc` and `ffprobe`, never with `sine`.
- `AudioCopyMask` and `AudioEncoderFallback` only take effect when `AudioEncoder` is a `copy:*`
  passthrough. With `av_aac` they are inert — keep `AudioCopyMask` minimal so it doesn't read
  as though passthrough is happening.
- `autosub` shells out to the installed `addsub` command (`subprocess.run(["addsub", "-u", ...])`)
  rather than importing `toolboxcli.addsub`'s internals — keeps each script independently
  invocable, matching the original design where every script is a standalone executable.
- `kavitaname/core.py` uses the same tokenize-then-classify structure as `jellyname/core.py`,
  but its vocabulary is ported from Kavita's own scanner
  (`Kavita.Services/Scanner/Parser.cs`) rather than from release-group conventions. The
  invariant that matters is a *round trip*: it's not enough to parse messy input well, the
  filename it **emits** must be what Kavita reads back. Verify both directions against real
  fixtures — parse correctness and re-parse through Kavita's `MangaVolumeRegex`/
  `MangaChapterRegex` — rather than reasoning about the regexes in isolation.
  `tests/test_kavitaname.py` covers the first direction and re-parses output through
  kavitaname's *own* parser; it does not run Kavita's regexes, so a change to the output
  vocabulary still needs checking against Kavita itself.
- The output vocabulary is deliberately much narrower than the input vocabulary: only
  `c{ch:03d}`, `(v{vol:02d})`, `v{vol:02d}` and `SP{NN}` are ever produced, because those are
  the forms verified to survive Kavita's re-parse. Don't widen it to "preserve" an input
  spelling.
- Specials follow Kavita exactly: `SP\d+` in the filename is the **only** real trigger
  (`Parser.IsSpecial` → `HasSpecialMarker`), so emitting an explicit SP marker is the entire
  point of that path. A keyword like "Omake" only promotes a file to special when *neither* a
  volume nor a chapter was parsed — the guard is `BasicParser.cs`'s
  `IsDefaultChapter && IsLooseLeafVolume && isSpecial`, and it's load-bearing:
  `v20 c171-180 Omake` is a chapter. A trailing keyword on a numbered file becomes
  `ChapterInfo.descriptor` and is kept in the output; dropping it would rename
  `One Piece v01 Omake` onto `One Piece v01` and collide with the real volume archive.
- The duplicate-marker guard (`drop_duplicate_markers`) is ported from Kavita's
  `RemoveDuplicateVolumeIfExists`/`RemoveDuplicateChapterIfExists`: a *second* volume/chapter
  marker describes the file's contents, not the file, so everything from it onward is
  truncated. Ranges written `v1-v2`/`c1-c4` are explicitly exempt — they're one value.
- `kavitaname` keeps standalone `-` tokens when tokenizing (unlike `jellyname`, which drops
  them): a dash is part of the series name often enough to matter — `86 - Eighty Six`,
  `Goblin Slayer Side Story - Year One`. No anchor pattern matches a bare dash, so it's free.
- The bare-trailing-number fallback (`Hinowa ga CRUSH! 018`) never fires on the first token,
  which is what stops a series that simply *starts* with a number from being eaten. It also
  tracks consumed token indices so a `Vol. 10` pair can't have its `10` re-read as a chapter.
- `ComicInfo.xml` is read with the stdlib `zipfile`, the same "CBZ is already a zip" reasoning
  that let `mergemanga` drop its `7z` dependency — so `.cbr`/`.7z` are deliberately *not*
  read (that would mean a new dependency) while their filenames still parse normally. It fills
  only fields the filename left blank, mirroring `jellyname`'s `apply_probed_tags` contract,
  and like `jellyname`'s ffprobe call it lives in `cli.py` and is injected into `core.py` as a
  callable so `core.py` stays I/O-free.
- `kavitaname`'s volume map accepts **both** JSON (`mergemanga`'s real schema) and the legacy
  pipe-delimited form, auto-detected from content rather than extension. The previous version
  documented pipe format as "same format as mergemanga's data file" while that file was JSON,
  so following the docs silently produced zero mappings and skipped every chapter. Don't drop
  either format.
- `jellyname/core.py` uses a tokenize-then-classify parser rather than the substring-deleting
  regex chain the previous version used: the stem is split into whitespace/bracket-delimited
  tokens, a season/episode marker (`SxxExx`, optionally `SxxExx-Eyy`/`SxxExxEyy`) or release
  year is located by *whole-token* match (never substring `.search`) to anchor the
  title/show-name split, and every other token is classified against ordered per-category
  dictionaries (`RESOLUTION_TAGS`, `HDR_TAGS`, `AUDIO_TAGS`, `VIDEO_CODEC_TAGS`,
  `BIT_DEPTH_TAGS`, `SOURCE_TAGS`, `EDITION_TAGS` — first match wins) into a `MediaTags`
  dataclass. Unlike the old version, matched tags are *preserved* in a bracketed filename
  suffix instead of being discarded — only unrecognized tokens (release-group names, hashes,
  fansub tags) are dropped. When extending the tag vocabularies or anchor-detection regexes,
  verify against real filename fixtures (`tests/fixtures/jellyname.json`) rather than
  reasoning about the regex in isolation — this is still the most fragile/fiddly logic here.
- jellyname keeps two kinds of qualifier in a title instead of dropping them as noise
  (`_title_qualifier`): a country code from `REGION_CODES` — `(US)` anywhere, or bare and
  upper-case just before the anchor as scene names write it (`The.Office.US.S02E05`) — and,
  for a TV show only, its year, rendered `Fargo (2014)` as Jellyfin names shows. Neither
  applies to the first token, so a show *called* `Us` or `1923` keeps its name. `MediaTags.hdr`
  is a list, like `audio`: `DV.HDR10` is both, and keeping only the last one lost Dolby Vision.
- `vidlib/naming.py` turns a pasted page title into a `People - Title - Collection` name for
  the web page. The title is split into *segments* on a spaced dash (`-`, `–`, `—`) and `|`,
  never on a dash inside a word; whatever follows the last `|` is marked `site` and guessed
  `skip`. Each segment has one role. Any number can be people (split into names on
  `, & and x + ×`) or title (joined with `" - "` in their original order — `parse_name` reads
  every middle field back as one title, so a multi-segment title round-trips); there is one
  collection (`set_role` demotes the previous one to title). `guess_roles` tries the library
  first — a segment whose names are *all* known people, the last segment that is a known
  collection — and only then position. Matching is two-tier: exact (`core.normalize`, as
  duplicate detection uses, or equal once every space is removed — `LemonadeStand` is
  `Lemonade Stand`) rewrites a name to the library's spelling without asking; loose (`loose_key`:
  also accents, punctuation and every space) counts for the guess but only offers a
  suggestion, and a loose key two library spellings share suggests nothing. `Draft.problems()`
  checks the result with `parse_name`, so naming and the library can't drift.
  `tests/fixtures/vidlib_titles.json` is the spec — change a rule by changing the code and the
  affected rows. Colons don't split: `Part 2: The Return` would come back as
  `Part 2 - The Return`.
  `find_in_title` powers the forms' pills: whole-word runs of the title that are a known
  person or collection (exact or loose), longest first, the *first* mention of a person and
  the *last* of a collection, each with the title it leaves (`_without` tidies the gap: a
  dangling `&`/`and`/`with`/`ft.` goes, a field separator on either side survives once). The
  page only ever applies `title_without` from an answer for the title as it is *now* — a
  pill click clears every pill until the fresh answer arrives, or a quick second click would
  put the first name back. "Unknown"/"Clips" are the user's own fill-ins, fixed in the page.
  Renaming (not downloading) fills a blank field with "Unknown" (`naming.fill_unknown`, in
  `Library.rename` and the rename form's preview via `fill_unknown: true`).
  Both naming answers carry `same_as`: library videos with the same match key, i.e. what
  duplicate detection would take for the same video and replace or park.
- `vidlib/parts.py` finds videos that are parts of one. The marker is read from the *title* only, at its start or
  end or as a `" - "` segment, in many spellings (`P1`, `Part1`, `Ep 2`, `Part II`, `Part
  Two`, `2/3`, `Part 1-5`); a sentence-opening marker followed by a small word is a title.
  Grouping deliberately ignores people and collection (crossovers) and the library's known
  names inside titles (a badly named file carries them): equal `loose_key` is *sure*, ≥80%
  shared words is *maybe*. A bare trailing number counts only in a set of two different
  numbers including 1 — two copies of "Day 1" are one video twice.
  `tests/fixtures/vidlib_parts.json` pins the markers. Dismissed sets live in
  `part_dismissals` (schema 4) and stay hidden until a new video joins them.
- Merging (`Library.merge_plan` / `queue_merge`, `cli.run_merge`) never joins anything
  itself: it runs the `concatvid` module (`python -m toolboxcli.concatvid.cli`, so no PATH
  lookup) for its compatibility check, its re-encode-only-what-differs normalization, its
  length check and its trashing of the parts — the merge screen's notes come from the same
  `concatvid.core` checks. Merges queue on `PipelineStatus` (in memory) and run on the
  pipeline's thread between polls, so Pause/Stop now/`-H` apply and there's one heavy job at
  a time. The join is written to `_incoming/merge-<id>/` and only moved into the root once
  its parts are gone, i.e. concatvid's length check passed; otherwise it stays there,
  `unverified`, for the page to discard. In the root it's `compressed` (sort only) when
  every part's status is in `DONE_STATUSES` — the pipeline's own "already compressed" test —
  and new input otherwise. Not `backend`: files recorded from folders or the old JSON log
  have none, and keying on it re-encoded merges of already-compressed libraries.
- `vidlib/fetcher.py` runs yt-dlp (+ aria2c) jobs in worker threads and reports only through
  callbacks (`on_start`, `on_line` with a parsed `Progress`, `on_done`), never printing. Every
  job writes into its own folder, so the finished file is whatever non-partial file is left
  there — no guessing yt-dlp's extension or the title an unnamed download gets. aria2c gets
  `--summary-interval=1 --show-console-readout=false` (after yt-dlp's own options, so they
  win): its live readout is buffered until exit when writing into a pipe, which showed 0%
  then 100%; the per-second summary line is flushed and parsed, its banner lines dropped
  from the tail (`_SUMMARY_NOISE`). A job cancelled
  before it starts still calls `on_done`. Leaving the `Engine` context on an exception cancels
  everything, so no yt-dlp outlives a Ctrl+C.
- `vidlib/downloads.py`'s `DownloadManager` owns the queue: rows in the `downloads` table
  (schema 3), statuses `naming` (bulk paste, waiting for a name while its title is fetched) →
  `queued` → `downloading` → `downloaded` → `imported`, or `failed`/`cancelled`. Jobs run in
  `<target>/_incoming/<id>/`; in library mode a finished file moves straight into the root
  (`imported`), in download-only mode (`-X DIR`) into DIR (`downloaded`) until
  `import_waiting()` moves it. `move_file` never overwrites — a clash leaves the file where it
  is, `downloaded` with the reason in `error` — and across disks copies to a hidden
  `.name.vidlib-partial`, checks the size, then renames and removes the source. It has its own
  `LibraryDB(shared=True)` connection, used only under its lock, alongside the pipeline's
  connection on the main thread (WAL). On shutdown the running rows keep `downloading`
  (`_closing` stops `_on_done` from marking them cancelled) and `start()` requeues them.
  `.video-download-queue.json` (the old TUI's queue) in the target folder is imported once.
  Clearing or removing a download that got somewhere (`downloaded`/`imported`) only sets
  `cleared` (schema 5) — it stays on record so `seen()` can say "already downloaded on …,
  now at …" when the same video's link is pasted again (a warning, never a block). Links
  are compared by `url_key`: YouTube by video id across hosts and parameters, anything else
  without its fragment and trailing slash; the in-queue duplicate check uses it too.
- `vidlib/status.py`'s `PipelineStatus` is how the pipeline loop (main thread) and the page
  (server threads) see each other: the loop reports states, the batch, the current file and
  each result; the page pauses, stops or resumes. The loop waits between polls with
  `status.sleep()`, which a resume cuts short. Stop now sets `status.cancel`, the Event passed
  to `transcode(cancel=...)`; a stopped file is renamed back from `.processing` and returns
  `status: "stopped"` without counting as a failure, and the CPU retry never follows a stop.
  `on_change` is called *outside* the status lock — it takes the download manager's lock, and
  the manager's `after_change` (KeepAwake) reads the status, so holding both would invite a
  lock-order deadlock. Before each poll the loop checks `root.is_dir()`: an unplugged disk
  would otherwise sync as "every video missing". `_finish` results carry the file's final
  root-relative `path`, which `on_settled` hands to `DownloadManager.video_settled` so an
  imported download follows its video. KeepAwake runs `caffeinate -i -w <pid>` on macOS only.
- The Review tab (`Library.decide`) settles a file parked in `_review/` against the library
  copy it was compared with: *new* trashes the copy, *old* trashes the parked file, *both*
  renames the parked file and requires its match key to differ from the copy's. A kept file
  goes back to the **root** for the pipeline to file. Its row becomes `compressed` (sort
  only), except a `downgrade` parked before any encode (`backend` empty), whose row is
  dropped so the pipeline encodes it as new input — `review` is in `DONE_STATUSES`, so
  keeping that row would file a raw source unencoded.
- `vidlib/library.py` is the Library tab's backend. Every action takes a root-relative path
  and first checks the *database* knows it (`_known`), so a crafted path (`../x`) can never
  reach a file outside the library. It shares the download manager's connection and lock.
  Rename builds the name with `naming.from_fields` (so the library's spellings and the same
  checks as a download), keeps the `[tags]`, moves the file into its collection's folder as
  the pipeline would find it (`cli.find_folder`, imported lazily — cli imports the server)
  and moves its row with `db.move`; it refuses to overwrite, except a case-only rename of the
  same file. Trash goes through `move_to_trash`. Both refuse the root file the pipeline is
  working on. Thumbnails are one ffmpeg frame at 10% (falling back to the first frame),
  cached in `.vidlib-cache/thumbs/` keyed by path + size + mtime, at most two ffmpeg runs at
  once. The hover preview is a filmstrip (`filmstrip`): `STRIP_FRAMES` (12) fast-seek frames tiled
  into one JPEG, cached alongside, stepped through by `background-position` — no video is
  streamed, so any container or codec works and nothing autoplays.
- `vidlib/server.py` is stdlib `ThreadingHTTPServer` on 127.0.0.1 only, with the page as
  package data in `vidlib/data/web/` (plain HTML/CSS/JS, no build step) and live state over
  Server-Sent Events (`/api/events`, throttled to `EVENT_INTERVAL`). Being on localhost isn't
  protection — any open web page can send requests there — so the Host header must be this
  server's (DNS rebinding), a request with a foreign Origin is refused, and every write takes a
  JSON body (a cross-site JSON POST needs a CORS preflight nothing answers). The page's script
  holds no naming logic: it posts the title and roles (or the hand-edited fields) to
  `/api/name` and renders the answer. The web page runs only in watch mode (`wants_web`): not
  with `-W`, `-n`, `-O`, reports or queries. `tests/test_vidlib_web.py` drives the API with a
  fake `yt-dlp` (POSIX only); `tests/test_vidlib_ui.py` is a Playwright smoke test that skips
  unless run with `uv run --with playwright` (CI doesn't install it; `CHROMIUM_PATH` picks the
  browser binary).
- `prunelib` deletes files the user *picked*, rather than files it
  produced, so everything it removes is listed and confirmed first, and it trashes by default
  (`-p/--permanent` requires typing the item count, not a y/n keypress). Sidecars are files
  named after the video (`stem.` / `stem-` prefix) **and** of a metadata/artwork/subtitle type
  (`core.is_leftover`), so an unrelated `Movie.zip` never rides along; each sidecar belongs to
  the video with the *longest* matching stem, which keeps `Show S01E01-E02.nfo` from being
  deleted with `Show S01E01.mkv`. Folders are only swept when what's left is leftover-type
  content, walking upward but never removing the scan root.
- `prunelib`'s history (`.prunelib-history.json`, in the directory it's run from) records
  files that were *shown and kept*, keyed by root-relative POSIX path with size + mtime: a
  file that changes (a `vidlib` re-encode in place) is suggested again. It's only
  written on a normal finish (`q`/EOF); Ctrl+C means "didn't really review these". The scan
  skips NAS recycle bins (`#recycle`, `@Recycle`, `$RECYCLE.BIN`) as well as dot-folders —
  trashing on a Synology SMB share lands files in `#recycle`, which would otherwise offer
  them straight back.
- `prunelib`'s review prompt is line-based (`1 3 5-7`, `o 3`, `d`, `q`) rather than a
  raw-keypress cursor UI: raw key handling is unreliable under Git Bash/mintty, and a typed
  multi-select needs no new dependency.
- `wallready`'s lone positional is a *directory or* a rename prefix, resolved by
  `cli.looks_like_path()`: an argument that names an existing folder, is `.`/`..`, starts with
  `~`, or contains a separator is the folder; anything else is a prefix. That keeps the
  original `wallready ~/Pictures/walls` working while `wallready "arc raiders"` renames the
  current folder's images, and it makes a mistyped *path* still die as a missing directory
  rather than silently becoming a prefix. `-p/--prefix` is the unambiguous form for both.
- `wallready`'s renamer (`core.extract_id`) identifies a download's id *structurally* — a run
  of 4+ digits, or a 5+ character mix of letters and digits — rather than matching a list of
  site names. That's what drops `wallhaven` out of `wallhaven-e204cm` without the list
  needing maintenance, and it's why resolution tokens (`1920x1080`, `4k`, `1080p`) and
  word-plus-number names (`wallpaper2`) need explicit exclusions: they'd otherwise read as
  ids. Keeping the source id rather than always generating one is what makes a re-run
  idempotent — `arc-raiders-e204cm` parses back out to `e204cm`, so prefixes don't stack.
  Collisions get `-2`/`-3` from `cli.reserve_name()`, which treats a file's own current name
  as free so a re-run doesn't renumber everything.

### Dependencies

Base dependencies (`rich`, `send2trash`) are required by most scripts — don't add new
third-party libraries without a clear reason: `argparse` (stdlib) is used for all CLI parsing,
and single-keypress confirmation is hand-rolled via `termios`/`tty`/`msvcrt` in
`_common/confirm.py` rather than pulling in a library for it. External tools (`ffmpeg`,
`ffprobe`, `HandBrakeCLI`, `magick`, `7z`) remain required system binaries invoked via
`subprocess` — there was a deliberate decision *not* to replace any of them with a pure-Python
library (e.g. no Pillow for `convertimg`, no rarfile for `cybermod`) except `mergemanga`'s move
from `7z` to stdlib `zipfile`, which was safe specifically because CBZ files are already zip
archives.

## Git Workflow

Standing permission from the repo owner — don't ask each time:

- When a change is finished, commit it on a feature branch (never directly on `main`), push,
  and open a pull request.
- Review the PR's diff yourself before merging — correctness, the conventions in this file,
  docstring/`--help` accuracy, and the smoke test above. Fix anything the review turns up and
  push the fix to the same branch.
- Once the review is clean, merge the PR. Don't merge if the review leaves an unresolved
  problem or something you couldn't verify that matters for safety (e.g. a change that could
  delete or overwrite user files) — report it instead and leave the PR open.

## When Modifying Scripts

- Reuse `_common` modules — don't reimplement logging, trash, confirmation prompts, or progress
  bars per-script. If you find yourself duplicating logic across two scripts, extract it into
  `_common` instead.
- Keep the module docstring in sync with the actual argparse flags, since it doubles as
  `--help` output.
- Keep `toolbox/cli.py`'s `SCRIPTS` list, `pyproject.toml`'s `[project.scripts]`, and the
  README's scripts table all in sync when adding or removing a script —
  `tests/test_project.py` fails when they drift.
- Test on Windows (Git Bash) where possible — the repo is primarily used there. CI runs the
  suite on Windows, but in PowerShell rather than Git Bash/mintty, so terminal behaviour
  (single-keypress prompts, live progress) still needs checking there by hand.
