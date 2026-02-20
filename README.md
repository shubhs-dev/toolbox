# Toolbox

A collection of CLI scripts and utilities for media processing and game modding.

## Scripts

| Script | Language | Description |
|--------|----------|-------------|
| `addsub` | Bash | Merge subtitle files into video files (soft-mux or hard-burn) |
| `compressvid` | Python 3 | Watch a folder for videos and transcode them with HandBrake |
| `cybermod` | Bash | Automatically install Cyberpunk 2077 mods from zip/rar/7z archives |
| `mergemanga` | Bash | Merge individual One Piece chapter CBZ files into volume CBZ files |

---

## Installation

### Prerequisites

- **Bash** (Git Bash / WSL / Linux / macOS)
- **Python 3.6+** (for `compressvid`)

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
ln -s "$(pwd)/cybermod" ~/.local/bin/cybermod
ln -s "$(pwd)/mergemanga" ~/.local/bin/mergemanga
```

### Make scripts executable

```bash
chmod +x addsub compressvid cybermod mergemanga
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
| `-t, --title TITLE` | Title for the subtitle track |
| `--help` | Show help message |

---

### compressvid

Watch a folder for video files and transcode them with **HandBrake**, keeping whichever copy is smaller.

**Dependencies:** `HandBrakeCLI`, Python package `rich` (install with `pip install rich`)  
**Optional:** `send2trash` (`pip install send2trash`) for cross-platform Recycle Bin support

```bash
# Watch current directory with default preset
compressvid

# Explicit preset and folder
compressvid -p hw-1080-preset ~/Videos

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

---

### cybermod

Automatically install Cyberpunk 2077 mods from zip/rar/7z archives.

**Dependencies:** `7z` (7-Zip)

> **Note:** The default game directory is set to `/d/SteamLibrary/steamapps/common/Cyberpunk 2077`. Edit the `GAME_DIR` variable in the script if your installation path is different.

```bash
# Process all archives in the current directory
cd /path/to/mod/downloads
cybermod

# Install specific mod archives
cybermod mod1.zip mod2.rar mod3.7z
```

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

Chapter files must be named `Chapter N.cbz` (e.g., `Chapter 1.cbz`, `Chapter 42.cbz`). The script contains a built-in volume map covering volumes 1–113 with official English titles.

---

## HandBrake Presets

Custom HandBrake preset files live in the `handbrake-presets/` directory. The included `hw-1080-preset` is a hardware-accelerated 1080p H.264 preset. Drop additional `.json` preset files into this directory to use them with `compressvid -p <name>`.

## License

[MIT](LICENSE)
