# Copilot Instructions — Toolbox

## Project Overview

A collection of standalone CLI scripts for media processing, file organisation, and game modding. There is **no build system, no shared library, no tests**. Each script is a self-contained executable (Bash or Python) that lives on `PATH` and is invoked by name.

**Target platforms:** macOS (primary) and Git Bash on Windows. Every script must run correctly on both.

---

## Scripts at a Glance

| Script | Language | External deps | Purpose |
|--------|----------|---------------|---------|
| `addsub` | Bash | `ffmpeg` | Soft-mux or hard-burn subtitles into a video file |
| `autosub` | Bash | `addsub` | Auto-match subtitle files to videos by episode code, then call `addsub` |
| `compressvid` | Python 3 | `HandBrakeCLI`, `rich`, opt. `send2trash` | Watch a folder → transcode videos → keep the smaller copy |
| `convertimg` | Bash | `magick` (ImageMagick 7) | Batch-convert images in cwd to a target format |
| `cutvid` | Bash | `ffmpeg` | Trim a video to a start/end time (stream-copy or re-encode) |
| `cybermod` | Bash | `7z` | Extract and install Cyberpunk 2077 mods from archives |
| `finddupes` | Bash | — | Find duplicate filenames across a directory tree |
| `flatten` | Bash | — | Move all files from subdirs up into cwd; handles conflicts |
| `jellyname` | Bash | — | Rename and organise media files into Jellyfin folder structure |
| `kavitaname` | Bash | — | Rename chapter CBZ/CBR files for Kavita server compatibility |
| `mergemanga` | Bash | `7z` | Merge One Piece chapter CBZs into volume CBZs with metadata |
| `sortmedia` | Python 3 | — | Move video files into folders by camelCase type tag in filename |
| `toolbox` | Bash | — | List all scripts with descriptions |
| `x265ify` | Python 3 | `ffmpeg`/`ffprobe`, `rich`, opt. `send2trash` | Re-encode x264 videos to x265/HEVC via hardware encoder |

---

## Keeping the README Updated

**`README.md` must always reflect the current state of the scripts in this repo.** Whenever you add, remove, or modify a script — including changing its language, options/flags, arguments, defaults, dependencies, or behavior — update the corresponding section(s) of `README.md` in the same change:

- New script → add a row to the `## Scripts` table and a new `###` usage section (dependencies, examples, options table), and add it to the `chmod +x` line and the `SCRIPTS` array in `toolbox`.
- Removed script → delete its table row, usage section, and entry from `toolbox`'s `SCRIPTS` array and the `chmod +x` line.
- Changed options/behavior/dependencies/language → update the matching table row and usage section so the README never drifts from actual script behavior.
- Also update the `Scripts at a Glance` table and the relevant `Script-Specific Conventions` entry below in this file for the same change.

Treat README and copilot-instructions updates as part of the definition of done for any script change, not a follow-up task.

---

## Universal Rules

### Shebangs and executability
- Bash scripts: `#!/usr/bin/env bash` — **never** `#!/bin/bash` (macOS system bash is v3.2)
- Python scripts: `#!/usr/bin/env python3`
- No file extensions; scripts are `chmod +x` and invoked by name

### Safety: set options
Every Bash script must start with:
```bash
set -euo pipefail
```

### Trash, never delete
Every script that removes user files must send them to the OS Trash / Recycle Bin. Use this exact `trash_file()` helper — never `rm` directly on user-owned files:

```bash
trash_file() {
  local f="$1"
  if command -v gio &>/dev/null; then
    gio trash "$f"                                         # Linux (GNOME)
  elif command -v trash-put &>/dev/null; then
    trash-put "$f"                                         # Linux (trash-cli)
  elif command -v osascript &>/dev/null; then
    osascript -e "tell app \"Finder\" to delete POSIX file \"$(realpath "$f")\"" &>/dev/null  # macOS
  elif command -v powershell.exe &>/dev/null; then
    local win_path
    win_path=$(cygpath -w "$f" 2>/dev/null || echo "$f")  # Git Bash / MSYS2
    powershell.exe -NoProfile -Command "
      Add-Type -AssemblyName Microsoft.VisualBasic
      [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(
        '${win_path//\'/\'\'}',
        'OnlyErrorDialogs',
        'SendToRecycleBin'
      )
    " 2>/dev/null && return
    rm -f "$f"
  else
    rm -f "$f"
  fi
}
```

### Cross-platform compatibility checklist
When writing or editing Bash scripts, avoid these GNU-only features:
- `find … -printf` → use `find … | sed 's|.*/||'` or `-exec basename {} \;`
- `sed -E "s/…/gI"` (case-insensitive flag) → use `perl -pe "s/…/gi"` instead
- `ls --reverse` → use `-r` short flag (`ls -1tr`)
- `stat -c '…'` → include BSD fallback: `stat -c '%s' "$f" 2>/dev/null || stat -f '%z' "$f"`
- `date -d` → use Python or `perl` for date arithmetic
- `sort -V` (version sort) — available on macOS 12+ and GNU; safe to use
- `realpath` — available on macOS 10.15+ and Git Bash; safe to use
- `mapfile` / `readarray` — bash 4+ only; fine since `#!/usr/bin/env bash` picks up Homebrew bash
- `${var,,}` / `${var^^}` — bash 4+ only; same caveat, safe with env bash

---

## Bash Patterns

### Argument parsing
All Bash scripts use a manual `while/case` loop with a `POSITIONAL` array. Follow this pattern exactly when adding flags:

```bash
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--flag)   FLAG=true; shift ;;
    -v|--value)  [[ $# -ge 2 ]] || die "--value requires an argument"; VALUE="$2"; shift 2 ;;
    --help)      usage 0 ;;
    -*)          die "unknown option: $1" ;;
    *)           POSITIONAL+=("$1"); shift ;;
  esac
done
```

### Logging helpers (standard across all Bash scripts)
```bash
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

log_info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }
log_err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()       { log_err "$*"; exit 1; }
require()   { command -v "$1" &>/dev/null || die "'$1' is not installed or not in PATH"; }
```

### Usage / help text
Embed the usage block in the file header comment (lines 3–N) and extract it with:
```bash
usage() { sed -n '3,Np' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
```

### Interactive single-keypress prompt
```bash
confirm() {
    local prompt="$1" reply
    while true; do
        echo -en "${YELLOW}[?]${NC} ${prompt} [y/n] "
        read -rn1 reply; echo ""
        case "${reply,,}" in
            y) return 0 ;; n) return 1 ;;
            *) echo "  Please press y or n." ;;
        esac
    done
}
```

### Temp files
```bash
TMPFILE=$(mktemp "${TMPDIR:-/tmp}/scriptname-XXXXXX")
trap 'rm -f "$TMPFILE"' EXIT
```

### Script directory (for sibling assets)
```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
```

---

## Python Patterns

### Terminal UI
`compressvid` uses `rich` for all output — `Console()`, `Progress()`, `Table()`, `Panel()`. Never print directly; always go through `console`.

### Cross-platform single-keypress input (sortmedia)
```python
def getkey(prompt: str) -> str:
    sys.stdout.write(prompt); sys.stdout.flush()
    if sys.platform == "win32":
        import msvcrt; ch = msvcrt.getwch()
    else:
        import tty, termios
        fd = sys.stdin.fileno(); old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd); ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stdout.write(ch + "\n"); sys.stdout.flush()
    return ch.lower()
```

### Git Bash path conversion (sortmedia)
```python
msys_match = re.match(r'^/([a-zA-Z])(/.*)?$', raw)
if msys_match:
    drive = msys_match.group(1).upper()
    rest  = (msys_match.group(2) or "").replace("/", "\\")
    raw   = f"{drive}:{rest}"
```

### Trash (compressvid)
Use `send2trash` if available, then fall back to `powershell` (Windows) → `gio`/`trash-put` (Linux) → `osascript` (macOS).

---

## Script-Specific Conventions

### addsub
- Two modes: `soft` (default, copy subtitle as a stream) and `hard` (burn into video frames via `-vf subtitles=`)
- Subtitle codec is inferred from container: `mov_text` for MP4, `copy`/`srt` for MKV
- In-place mode: write to a temp file then `mv` over the original; `-u` mode writes `<stem> - Sub.<ext>` alongside
- Always trash both the original video (unless `--keep`) and the subtitle file after processing

### autosub
- Snapshots video files with `mapfile -t` before running anything, so files created by `addsub` are excluded from the loop
- Episode code = 2nd `' - '`-separated segment of the filename (e.g. `Show - S01E01 - Title.mkv` → `S01E01`)
- `-y` flag skips all confirmation prompts

### compressvid
- HandBrake preset JSONs live in `handbrake-presets/` (sibling of the script); resolved via `SCRIPT_DIR`
- Sentinel file pattern: source renamed to `*.processing` during transcode; output written as `*.tmp`; renamed atomically on success
- `recover_interrupted()` restores sentinels on startup
- Thread-safe JSON log at `<output_dir>/.compressvid.json` via `_log_lock`; all writes through `update_log()`
- Size comparison: keep compressed only if strictly smaller; otherwise discard and leave original in place
- HandBrake progress parsed from stderr via `HB_PCT_RE = re.compile(r'(\d+\.\d+)\s*%')`

### convertimg
- Uses ImageMagick 7 (`magick` command, not `convert`)
- `compgen -G "*.${ext^^}"` matches uppercase extensions (e.g. `.JPG`)
- `--dry-run` mode shows planned conversions without touching anything
- Skips files already in the target format

### cutvid
- Stream-copy mode (default): `-ss` placed before `-i` for efficient seek; cuts on keyframes
- Re-encode mode (`--reencode`): `-ss` placed after `-i` for frame-accurate seek
- `-e`/`--end` and `-d`/`--duration` are mutually exclusive
- Output filename auto-derived as `<stem>.cut.<start>-<end>.<ext>` with colons replaced by dashes

### cybermod
- `GAME_DIR` is hardcoded at the top — instruct users to edit it for their install path
- `KNOWN_DIRS="archive|bin|engine|r6|red4ext|mods|tools"` identifies valid mod directories
- `find_mod_root()` searches up to 2 levels deep inside extracted archives
- Loose `.archive` files (no recognisable structure) route to `archive/pc/mod/` automatically (`LOOSE_ARCHIVE:` prefix sentinel)
- Shows overwrite preview before each install confirmation
- Archives are sent to Trash after successful install via `trash_file()`

### finddupes
- Builds a `filename<TAB>path` flat file with `find … -print0`, then uses `sort | uniq -c` to find duplicates
- Braille spinner animation via `SPINNER_CHARS='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'`; updated every 50 files
- `-i` flag: keys lowercased with `tr` before comparison
- `-c` flag: sorts output groups by count (highest first)

### flatten
- `file_info()` uses `stat -c '%s' 2>/dev/null || stat -f '%z'` for portable file size (GNU + BSD)
- Conflict prompt is a 3-way choice: `[y/n/a(ll)]` via `confirm_replace()`; `REPLACE_ALL=true` on `a`
- Empty directories are removed bottom-up after flattening: `find … -mindepth 1 -type d -empty -delete`

### jellyname
- `strip_junk()` removes codec/quality/source tags using `perl -pe "s/${junk_re}/ /gi"` (NOT `sed /gI` — BSD sed doesn't support the `I` flag)
- `normalize_name()` replaces `.` and `_` word separators with spaces
- `sanitize_for_path()` strips Jellyfin-illegal characters: `< > : " / \ | ? *`
- TV detection: `S##E##` or `##x##` patterns; movie detection: 4-digit year `1900–2099`
- Output: movies → `Title (Year)/Title (Year) - 1080p.mkv`; TV → `Show/Season 01/Show S01E01 - 720p.mkv`
- Resolution is preserved as a Jellyfin version-label suffix (` - 1080p`)

### kavitaname
- Takes `type` (`manga` or `manhwa`) as first positional arg; optional series name (defaults to folder name)
- Manga mode: prompts for a pipe-delimited volume map file (`vol|ch_start|ch_end|Title`); builds `CH_VOL` associative array
- Output format: manga → `{Series} c{ch:03d} (v{vol:02d}).cbz`; manhwa → `{Series} c{ch:03d}.cbz`
- Decimal chapter numbers preserved: `Chapter 10.5` → `c010.5`
- Volume map file format is the same as `one-piece-volumes` (shared format; `# comments` and blank lines ignored)

### mergemanga
- Volume data loaded at runtime from `one-piece-volumes` (sibling data file, pipe-delimited, `# comments` supported); not hardcoded in the script
- Output naming: `Eiichiro Oda - One Piece XX - Title.cbz` (Calibre-compatible)
- Pages extracted into zero-padded subdirs (`ch_0001/`) to preserve sort order across chapters
- `ComicInfo.xml` generated inside each CBZ for comic reader metadata
- Volumes with no chapter files at all are silently skipped; volumes with some missing chapters warn and build anyway

### sortmedia
- Extracts the last `' '`/`_`/`-`/`.`-separated segment of the filename stem as the "type" identifier
- Expands camelCase to words via two regex substitutions (abbreviation boundary first, then lowercase→uppercase)
- Searches `location_a` recursively for a folder whose name matches the expanded type (case-insensitive)
- If no folder is found, prompts `[y/N]` to create it; uses `getkey()` for single-keypress input
- Git Bash MSYS2 paths (`/d/foo`) are converted to Windows paths (`D:\foo`) before `Path()` resolution

### toolbox
- `SCRIPTS` array of `"name|description"` pairs; add new scripts here when creating them
- Column width auto-calculated from the longest script name for alignment

### x265ify
- Encoder chosen automatically via `ENCODER_PRIORITY` list (NVENC → VideoToolbox → QSV → AMF → VAAPI → software `libx265` fallback); override with `--encoder`
- Sentinel file pattern mirrors `compressvid`: source renamed to `*.processing` during transcode; `recover_interrupted()` restores sentinels on startup
- Resolution, audio, subtitles, and HDR metadata (HDR10/HLG) always passed through unchanged
- Size comparison: keep transcoded file only if strictly smaller; otherwise discard and leave original in place
- `--watch [SECS]` re-scans on an interval (default 60s when flag given with no value, via `nargs="?", const=60`)
- Original sent to Trash via `send2trash` → `powershell` → `gio`/`trash-put` → `osascript` fallback chain (same pattern as `compressvid`)

---

## Data Files

| File | Used by | Format |
|------|---------|--------|
| `one-piece-volumes` | `mergemanga`, `kavitaname` | `vol\|ch_start\|ch_end\|Title` per line; `#` comments; blank lines ignored |
| `handbrake-presets/*.json` | `compressvid` | Standard HandBrakeCLI preset JSON; `PresetList[0].PresetName` used as display name |

---

## When Adding a New Script

1. Use `#!/usr/bin/env bash` or `#!/usr/bin/env python3`
2. Embed usage/help in the file header comment; extract with `sed` in `usage()`
3. Copy the standard logging helpers and `trash_file()` from an existing script
4. Follow the `POSITIONAL` array argument-parsing pattern
5. Add an entry to the `SCRIPTS` array in `toolbox`
6. Make it executable: `chmod +x <scriptname>`
7. Verify it works on both macOS and Git Bash on Windows — check the cross-platform checklist above
8. Update `README.md` (table row + usage section) and this file's `Scripts at a Glance` table + `Script-Specific Conventions` entry

## When Modifying or Removing a Script

1. Update `README.md`'s table row and usage section (options, examples, dependencies) to match the new behavior, or remove them entirely if the script is deleted
2. Update this file's `Scripts at a Glance` table and `Script-Specific Conventions` entry to match
3. If removed, also drop the entry from the `SCRIPTS` array in `toolbox` and the `chmod +x` line in the README
