# Toolbox

A collection of standalone CLI scripts for media processing, file organisation, and game modding. Each script is self-contained (no shared library, no build system) and meant to be run directly from `PATH`.

## Scripts

| Script | Language | Description |
|--------|----------|-------------|
| `addsub` | Bash | Merge a subtitle file into a video (soft-mux or hard-burn) via ffmpeg |
| `autosub` | Bash | Auto-match subtitle files to videos by episode code, then run `addsub` |
| `compressvid` | Python 3 | Watch a folder for videos and transcode them with HandBrake, keeping the smaller copy |
| `convertimg` | Bash | Batch-convert all images in the current folder to a target format via ImageMagick |
| `cutvid` | Bash | Trim a video to a start/end time using ffmpeg (stream-copy or re-encode) |
| `cybermod` | Bash | Extract and install Cyberpunk 2077 mods from zip/rar/7z archives |
| `finddupes` | Bash | Find duplicate filenames across a directory tree |
| `flatten` | Bash | Move all files from subdirectories up into the current directory |
| `jellyname` | Bash | Rename and organize media files into a Jellyfin-compatible folder structure |
| `kavitaname` | Bash | Rename chapter CBZ/CBR files for Kavita server compatibility |
| `mergemanga` | Bash | Merge individual One Piece chapter CBZ files into volume CBZ files with metadata |
| `sortmedia` | Python 3 | Move video files into folders based on the camelCase type tag in each filename |
| `toolbox` | Bash | List all custom scripts with a brief description |
| `x265ify` | Python 3 | Re-encode x264 videos to x265/HEVC using the best available hardware encoder |

---

## Installation

### Prerequisites

- **Bash** (Git Bash / WSL / Linux / macOS) — Homebrew bash on macOS is recommended (system bash is 3.2)
- **Python 3.6+** (for `compressvid`, `sortmedia`, `x265ify`)

### Clone the repo

```bash
git clone git@github.com:shubhs-dev/toolbox.git
cd toolbox
```

### Make scripts available system-wide

Add the repo directory to your `PATH`, or symlink the scripts you want:

```bash
# Option 1 — add to PATH (append to ~/.bashrc or ~/.zshrc)
export PATH="$HOME/toolbox:$PATH"

# Option 2 — symlink individual scripts
ln -s "$(pwd)/addsub" ~/.local/bin/addsub
ln -s "$(pwd)/compressvid" ~/.local/bin/compressvid
```

### Make scripts executable

```bash
chmod +x addsub autosub compressvid convertimg cutvid cybermod finddupes flatten jellyname kavitaname mergemanga sortmedia toolbox x265ify
```

---

## Usage

### addsub

Merge a subtitle file into a video using **ffmpeg**.

**Dependencies:** `ffmpeg`

```bash
# Soft-mux (default) — subtitle as a selectable stream
addsub movie.mkv movie.srt

# Specify output file
addsub movie.mp4 movie.ass output.mp4

# Hard-burn subtitles into the video (re-encodes video stream)
addsub --hard movie.mp4 movie.srt burned.mp4

# Set subtitle language and title
addsub --lang jpn --title "Japanese" movie.mkv movie.ass
```

| Option | Description |
|--------|-------------|
| `-s, --soft` | Mux subtitles as a selectable stream (default) |
| `-h, --hard` | Burn subtitles into the video (not reversible) |
| `-l, --lang LANG` | Language code for the subtitle track (default: `eng`) |
| `-t, --title TITLE` | Title for the subtitle track (default: subtitle filename) |
| `-u, --suffix` | Append ` - Sub` to the output filename instead of updating in place |
| `-k, --keep` | Keep the original video file (default: trash it when output differs) |
| `--help` | Show help message |

---

### autosub

Auto-match subtitle files to videos in the current directory by episode code, then run `addsub -u` for each match.

**Dependencies:** `addsub` (on `PATH`)

The episode code is the 2nd `' - '`-separated segment of the filename (e.g. `Show - S01E01 - Title.mkv` → `S01E01`). Video files are snapshotted upfront so files created by `addsub` are not re-processed.

```bash
# Prompt for confirmation before each match
autosub

# Skip all confirmation prompts
autosub -y
```

| Option | Description |
|--------|-------------|
| `-y, --yes` | Skip confirmation prompts and run `addsub` for all matches |

---

### compressvid

Watch a folder for video files and transcode them with **HandBrake**, keeping whichever copy is smaller.

**Dependencies:** `HandBrakeCLI`, Python package `rich` (install with `pip install rich`)  
**Optional:** `send2trash` (`pip install send2trash`) for cross-platform Recycle Bin support

```bash
# Watch current directory with default preset
compressvid

# Explicit preset and folder
compressvid -p hw-1080 ~/Videos

# Process existing files once and exit
compressvid --once

# Show available HandBrake presets
compressvid --list-presets
```

| Option | Description |
|--------|-------------|
| `watch_dir` | Directory to watch for videos (default: `.`) |
| `-p, --preset NAME` | Preset filename stem from `handbrake-presets/` (default: `hw-1080-preset`) |
| `-o, --output DIR` | Sub-folder for transcoded results (default: `compressed`) |
| `-j, --workers N` | Number of parallel transcodes (default: `3`) |
| `--interval SECS` | Seconds between folder scans (default: `10`) |
| `--once` | Process existing files and exit (no continuous watch) |
| `--list-presets` | Show available HandBrake presets and exit |
| `-f, --copy-original` | Copy the original file to the output folder when it's smaller than the compressed version |

> **Note:** the preset files shipped in `handbrake-presets/` are named `hw-1080` and `hw-720`; pass `-p hw-1080` (or `-p hw-720`) unless you add a preset file matching the argparse default stem (`hw-1080-preset`).

---

### convertimg

Batch-convert all images in the current folder to a target format using **ImageMagick**.

**Dependencies:** `magick` (ImageMagick 7)

```bash
convertimg webp
convertimg --quality 90 jpg
convertimg --keep png
convertimg --dry-run avif
```

| Option | Description |
|--------|-------------|
| `<format>` | Target format extension (e.g. `jpg`, `png`, `webp`, `avif`, `tiff`) |
| `-q, --quality N` | Compression quality 1–100 (default: `85`; only for lossy formats) |
| `-k, --keep` | Keep original files (default: trash them after successful conversion) |
| `-n, --dry-run` | Show what would be converted without doing anything |
| `--help` | Show help message |

---

### cutvid

Trim a video to a start/end time using **ffmpeg**.

**Dependencies:** `ffmpeg`

```bash
cutvid -s 00:01:30 -e 00:05:00 movie.mkv        # cut from 1:30 to 5:00
cutvid -s 90 -d 120 movie.mp4 clip.mp4           # 2-minute clip starting at 1:30
cutvid -s 00:01:30 movie.mkv                     # trim start, keep to end
cutvid -e 00:05:00 movie.mkv                     # keep from beginning to 5:00
cutvid --start 1:30 --end 5:00 --reencode movie.mkv clip.mkv
```

| Option | Description |
|--------|-------------|
| `-s, --start TIME` | Start time (default: beginning). Accepts `HH:MM:SS`, `MM:SS`, or seconds |
| `-e, --end TIME` | End time (default: end of file). Accepts `HH:MM:SS`, `MM:SS`, or seconds |
| `-d, --duration DUR` | Duration of the cut instead of end time |
| `-r, --reencode` | Re-encode output (slower, frame-accurate). Default: stream-copy |
| `--help` | Show help message |

Stream-copy mode (default) is near-instant but cuts on keyframes, so the actual start may be slightly before the requested time. Use `--reencode` for frame-accurate cuts. Output filename is auto-derived as `<stem>.cut.<start>-<end>.<ext>` if not given.

---

### cybermod

Automatically install Cyberpunk 2077 mods from zip/rar/7z archives.

**Dependencies:** `7z` (7-Zip)

> **Note:** The default game directory is hardcoded (`GAME_DIR` at the top of the script). Edit it to match your installation path.

```bash
# Process all archives in the current directory
cd /path/to/mod/downloads
cybermod

# Install specific mod archives
cybermod mod1.zip mod2.rar mod3.7z
```

Recognises mod roots up to 2 levels deep inside extracted archives (looking for `archive`, `bin`, `engine`, `r6`, `red4ext`, `mods`, or `tools` folders). Loose `.archive` files with no recognisable structure are routed to `archive/pc/mod/` automatically. Shows an overwrite preview before each install and sends processed archives to Trash.

---

### finddupes

Find duplicate filenames across a directory tree.

```bash
finddupes
finddupes ~/Documents
finddupes -c -i /path/to/dir
```

| Option | Description |
|--------|-------------|
| `directory` | Directory to scan recursively (default: `.`) |
| `-c, --count` | Sort results by duplicate count (highest first) |
| `-i, --ignore-case` | Case-insensitive filename comparison |
| `--help` | Show help message |

---

### flatten

Recursively move all files from subdirectories into the current directory. Empty subdirectories are removed afterwards.

```bash
flatten
flatten --yes
flatten --dry-run
```

| Option | Description |
|--------|-------------|
| `-y, --yes` | Replace all conflicting files without prompting |
| `-n, --dry-run` | Show what would be moved without making changes |
| `--help` | Show help message |

When a filename conflict occurs (and `-y` is not set), you're prompted with size, modification date, and path info for both files (`[y/n/a]`, where `a` replaces all subsequent conflicts).

---

### jellyname

Rename and organize media files for Jellyfin compatibility. Scans the top level of a directory for video files, parses each filename for title/year/season/episode/resolution, then renames and moves each file into a Jellyfin-compatible folder structure.

```bash
jellyname
jellyname ~/Downloads/movies
jellyname --dry-run /mnt/media/shows
```

| Option | Description |
|--------|-------------|
| `directory` | Directory to scan (default: current directory) |
| `-n, --dry-run` | Preview what would be renamed without moving any files |
| `--help` | Show help message |

Output structure:
- Movies: `Movie Name (Year)/Movie Name (Year) - 1080p.mkv`
- TV/Anime: `Show Name/Season 01/Show Name S01E01 - 720p.mkv`

Junk tags (BluRay, x264, HEVC, WEB-DL, etc.) are stripped, and characters illegal in Jellyfin paths (`< > : " / \ | ? *`) are removed.

---

### kavitaname

Rename chapter CBZ/CBR files for Kavita server compatibility.

```bash
kavitaname manga "One Piece"
kavitaname manhwa
kavitaname --dry-run manga "Berserk"
```

| Argument/Option | Description |
|------------------|-------------|
| `type` | Content type: `manga` or `manhwa` |
| `series-name` | Series name override (default: current folder name) |
| `-n, --dry-run` | Preview renames without making any changes |
| `--help` | Show help message |

Scans the current folder for `Chapter N.cbz`/`.cbr` (and `Prologue N`) files. For manga, optionally prompts for a pipe-delimited volume-map file (same format as [`one-piece-volumes`](one-piece-volumes)) — press Enter to skip.

Output format:
- Manga (with map): `{Series} c{ch:03d} (v{vol:02d}).cbz` → e.g. `One Piece c001 (v01).cbz`
- Manga (no map) / Manhwa: `{Series} c{ch:03d}.cbz` → e.g. `Solo Leveling c001.cbz`

---

### mergemanga

Merge individual One Piece chapter CBZ files into volume CBZ files with proper metadata.

**Dependencies:** `7z` (7-Zip)

```bash
# Merge chapters in the current directory, output to ./Volumes
mergemanga

# Specify source and output directories
mergemanga /path/to/chapters /path/to/output
```

Chapter files must be named `Chapter N.cbz` (e.g., `Chapter 1.cbz`, `Chapter 42.cbz`). The volume map is loaded at runtime from the sibling [`one-piece-volumes`](one-piece-volumes) data file (pipe-delimited, `vol|ch_start|ch_end|Title`), covering volumes 1–113 with official English titles. Output naming: `Eiichiro Oda - One Piece XX - Title.cbz`.

---

### sortmedia

Move video files into folders based on the camelCase type tag in each filename.

**Dependencies:** none beyond Python 3 standard library

```bash
sortmedia
sortmedia ~/Videos/LocationA
```

For each video file in the current directory, the last `' '`/`_`/`-`/`.`-separated segment of the filename stem is treated as a camelCase type identifier (e.g. `elephantHerd` → `elephant Herd`, `ATCRecording` → `ATC Recording`). The base folder (Location A — passed as an argument, or prompted for interactively) is searched recursively for a matching subfolder (case-insensitive); if found, the video is moved there, otherwise you're prompted `[y/N]` to create it.

---

### toolbox

List all custom scripts in this repo with a brief description of each.

```bash
toolbox
```

Add new scripts to the `SCRIPTS` array at the top of the script to keep this listing current.

---

### x265ify

Recursively re-encode H.264 videos to H.265/HEVC using the best available hardware encoder (NVIDIA NVENC, AMD AMF/VAAPI, Intel Quick Sync, Apple VideoToolbox, or software `libx265` as fallback). Replaces the original with the transcoded file only if it's strictly smaller.

**Dependencies:** `ffmpeg`/`ffprobe`, Python package `rich` (install with `pip install rich`)  
**Optional:** `send2trash` (`pip install send2trash`) for cross-platform Recycle Bin support

```bash
x265ify                       # scan cwd once, then exit
x265ify ~/Videos              # scan a specific folder
x265ify --watch               # scan, then re-scan every 60 seconds
x265ify --watch 120           # re-scan every 120 seconds
x265ify --crf 24              # quality: lower = better (default 28, range 0-51)
x265ify --dry-run             # show what would be converted; make no changes
x265ify --encoder hevc_nvenc  # force a specific ffmpeg encoder
```

| Option | Description |
|--------|-------------|
| `folder` | Folder to scan (default: current directory) |
| `--crf N` | Quality level 0–51, lower = better (default: `28`) |
| `--watch [SECS]` | Re-scan every `SECS` seconds after finishing (default when flag given: `60`) |
| `--dry-run` | Show which files would be converted; make no changes |
| `--encoder NAME` | Force a specific encoder (e.g. `hevc_nvenc`, `hevc_qsv`, `libx265`) |

Resolution, audio tracks, subtitles, and HDR metadata (HDR10, HLG) are always preserved. The original file is sent to the OS Trash, never permanently deleted. Interrupted transcodes (`*.processing` sentinels) are restored on the next run.

---

## Data Files

| File | Used by | Format |
|------|---------|--------|
| [`one-piece-volumes`](one-piece-volumes) | `mergemanga`, `kavitaname` | `vol\|ch_start\|ch_end\|Title` per line; `#` comments; blank lines ignored |
| `handbrake-presets/*.json` | `compressvid` | Standard HandBrakeCLI preset JSON; `PresetList[0].PresetName` used as display name |

## HandBrake Presets

Custom HandBrake preset files live in the `handbrake-presets/` directory (`hw-1080.json`, `hw-720.json` — hardware-accelerated H.264 presets). Drop additional `.json` preset files into this directory to use them with `compressvid -p <name>`.

## License

[MIT](LICENSE)
