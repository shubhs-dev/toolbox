# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A collection of CLI tools for media processing, file organization, and game modding, all
written in Python and packaged as a single pipx-installable distribution
(`shubhs-toolbox`, importable as `toolboxcli`). There is no build step beyond standard Python
packaging and no test suite.

Primarily used on Windows via Git Bash/MSYS2 and on macOS. Once installed with
`pipx install -e .`, every script is a real executable on `PATH` — `addsub`, `autosub`,
`check-deps`, `compressvid`, `concatvid`, `convertimg`, `cutvid`, `cybermod`,
`downscalevid`, `finddupes`, `flatten`, `flipvid`, `jellyname`, `kavitaname`,
`mergemanga`, `sortmedia`, `toolbox` —
identically on both platforms. `inventory.html` is a standalone static HTML dashboard,
unrelated to the Python package.

## Commands

```bash
# Install/reinstall after changes to pyproject.toml (dependencies, entry points)
pipx install -e . --force

# Run a script directly during development, without going through the installed entry point
python -m toolboxcli.<script>.cli --help

# Smoke-test everything after a change
for cmd in addsub autosub check-deps compressvid concatvid convertimg cutvid cybermod \
           downscalevid finddupes flatten flipvid jellyname kavitaname mergemanga \
           sortmedia toolbox; do
  $cmd --help > /dev/null || echo "FAILED: $cmd"
done
```

No lint/format/test commands exist — there's no configured linter or test runner in this repo.

## Architecture

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
├── sortmedia/cli.py
└── toolbox/cli.py
```

Each script is a `<name>/cli.py` with a `main()` function wired up in `pyproject.toml`'s
`[project.scripts]` table as the console-script entry point. `concatvid`, `cybermod`,
`downscalevid` and `jellyname` additionally split their logic into `core.py`, keeping `cli.py`
to argparse wiring, filesystem I/O and subprocess calls only — follow this `cli.py`/`core.py`
split for any future script whose logic grows beyond simple argument-parsing-and-dispatch.

**Bundled data**: `compressvid`'s HandBrake presets and `mergemanga`'s volume map are package
data (declared in `pyproject.toml`'s `[tool.setuptools.package-data]`), resolved at runtime via
`importlib.resources.files(...)` — **never** `Path(__file__).resolve().parent`-relative
lookups, since `__file__` points into the pipx-managed venv's site-packages, not the repo.
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
- **`confirm.py`** — `confirm(prompt, choices="yn", default=None)`. Single-keypress prompt, no
  Enter required; falls back to `input()` when stdin isn't a tty.
- **`humanize.py`** — `human_size(n)`. Byte-size formatting.
- **`progress.py`** — `spinner()` / `bar()`. rich `Progress` factories for indeterminate and
  determinate work respectively.
- **`tooling.py`** — `require_tool(name, install_hint=None)`. Check an external CLI binary is
  on `PATH` before shelling out to it; dies with a clear message if missing.
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
  `7z`, `addsub`) are checked with `_common.tooling.require_tool()` before use, not assumed
  present.
- `mergemanga` deliberately does **not** use `7z` — CBZ files are zip archives, so it uses the
  stdlib `zipfile` module directly (`ZIP_STORED` to match the original's "no compression"
  behavior). Don't reintroduce a 7z dependency there.
- `cybermod`'s Cyberpunk install directory is resolved in this order: `-g/--game-dir` flag →
  `CYBERMOD_GAME_DIR` env var → hardcoded default. There's no "edit the script" story anymore
  since it's installed via pipx into site-packages — new configurable paths in any script
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
- `autosub` shells out to the installed `addsub` command (`subprocess.run(["addsub", "-u", ...])`)
  rather than importing `toolboxcli.addsub`'s internals — keeps each script independently
  invocable, matching the original design where every script is a standalone executable.
- `kavitaname`'s filename-parsing regexes were ported directly from the original Bash
  (`perl`/`sed`/`grep -oE` patterns) — when touching them, verify against real filename
  fixtures rather than reasoning about the regex in isolation, since this is fragile/fiddly
  logic. Two known, deliberate deviations from the original Bash behavior: chapter-number
  parsing is case-insensitive for the `Chapter`/`Prologue` prefix (the original was
  case-sensitive despite case-insensitive file *discovery*, silently skipping mixed-case
  files); and it uses plain `int()` parsing instead of replicating a bash `printf '%d'`
  octal-misparse bug that affected zero-padded chapter numbers like `Chapter 010`.
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
  verify against real filename fixtures (still no test framework in this repo) rather than
  reasoning about the regex in isolation — this is still the most fragile/fiddly logic here.

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

## When Modifying Scripts

- Reuse `_common` modules — don't reimplement logging, trash, confirmation prompts, or progress
  bars per-script. If you find yourself duplicating logic across two scripts, extract it into
  `_common` instead.
- Keep the module docstring in sync with the actual argparse flags, since it doubles as
  `--help` output.
- Keep `toolbox/cli.py`'s `SCRIPTS` list, `pyproject.toml`'s `[project.scripts]`, and the
  README's scripts table all in sync when adding or removing a script.
- Test on Windows (Git Bash) where possible — the repo is primarily used there.
