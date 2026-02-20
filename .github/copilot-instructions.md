# Copilot Instructions — Toolbox

## Project Overview

A collection of standalone CLI scripts for media processing and game modding. There is **no build system, no shared library, no tests**. Each script is a self-contained executable (Bash or Python) designed to run directly from `PATH`.

## Scripts at a Glance

| Script | Language | External deps | Purpose |
|--------|----------|---------------|---------|
| `addsub` | Bash | `ffmpeg` | Soft-mux or hard-burn subtitles into video files |
| `compressvid` | Python 3 | `HandBrakeCLI`, `rich`, opt. `send2trash` | Watch folder → transcode videos → keep smaller copy |
| `cybermod` | Bash | `7z`, PowerShell (Windows) | Extract & install Cyberpunk 2077 mods from archives |
| `mergemanga` | Bash | `7z` | Merge One Piece chapter CBZ files into volume CBZs |

## Key Conventions

- **No file extensions** — scripts use shebangs (`#!/usr/bin/env bash` or `#!/usr/bin/env python3`) and are meant to be `chmod +x` and invoked by name.
- **Trash-not-delete** — every script that removes files sends them to the OS Recycle Bin/Trash (via `send2trash`, `gio trash`, PowerShell `Microsoft.VisualBasic`, or `osascript`). Never permanently delete user files.
- **HandBrake presets** live in `handbrake-presets/` as JSON. `compressvid` resolves presets relative to its own `SCRIPT_DIR`, so the preset dir must stay a sibling of the script.
- **Argument parsing** in Bash scripts uses a manual `while/case` loop over `$@` with a `POSITIONAL` array for positional args. Follow the same pattern when adding flags.

## compressvid (Python) Patterns

- Uses `rich` for all terminal UI (panels, progress bars, tables). Console output goes through `console = Console()`.
- Thread-safe JSON log (`.compressvid.json`) in the output dir tracks processed files to avoid re-work. All log writes go through `update_log()` which holds `_log_lock`.
- Files are renamed to `*.processing` during transcode, outputs written as `*.tmp`, then atomically renamed on success. `recover_interrupted()` restores these sentinels on startup.
- HandBrake progress is parsed from stderr via `HB_PCT_RE` regex in a background thread (`_stream_reader`).
- Size comparison decides the winner: compressed kept only if strictly smaller than original.

## cybermod (Bash) Patterns

- `GAME_DIR` is hardcoded at the top of the script — tell users to edit it for their install path.
- `KNOWN_DIRS` regex (`archive|bin|engine|r6|red4ext|mods|tools`) identifies valid Cyberpunk mod directories. `find_mod_root()` searches up to 2 levels deep for these.
- Loose `.archive` files (no directory structure) are detected and routed to `archive/pc/mod/`.
- Interactive confirmation prompts use single-keypress `read -rn1`.

## mergemanga (Bash) Patterns

- Contains a hardcoded `VOLUME_MAP` array mapping volume numbers to chapter ranges and English titles (vols 1–113). Update this array to add new volumes.
- Output filenames follow Calibre-compatible naming: `Eiichiro Oda - One Piece XX - Title.cbz`.
- Generates `ComicInfo.xml` metadata inside each CBZ for comic reader compatibility.
- Chapters are extracted into zero-padded subdirs (`ch_0001/`) to preserve page sort order.

## When Modifying Scripts

- Keep scripts self-contained — no shared modules or imports between scripts.
- Preserve the colored logging helpers (`log_info`, `log_ok`, `log_warn`, `log_err` in Bash; `rich` console in Python).
- Maintain `set -euo pipefail` at the top of all Bash scripts.
- Test on Windows (Git Bash / PowerShell) — the repo is primarily used there. Paths may use `/d/...` (MSYS2/Git Bash style).
