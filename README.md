# Toolbox

A collection of CLI tools for media processing, file organization, and game modding, written
in Python and installed as a single package with [uv](https://docs.astral.sh/uv/).

## Scripts

| Script | Description |
|--------|-------------|
| `addsub` | Merge a subtitle file into a video (soft-mux or hard-burn) via ffmpeg |
| `autosub` | Auto-match subtitle files to videos by episode code, then run `addsub` |
| `check-deps` | Scan subdirectories for specific npm package versions |
| `compressvid` | Watch a folder for videos and transcode them with HandBrake; keeps the smaller copy |
| `concatvid` | Join videos end to end with ffmpeg — ones you list, or numbered parts found in a folder (stream-copy, re-encoding only mismatched parts) |
| `convertimg` | Batch-convert all images in the current folder to a target format via ImageMagick |
| `cutvid` | Trim a video to a start/end time using ffmpeg (stream-copy or re-encode) |
| `cybermod` | Extract and install Cyberpunk 2077 mods from zip/rar/7z archives |
| `downscalevid` | Reduce a video's resolution using ffmpeg, GPU-accelerated when available (never upscales) |
| `finddupes` | Find duplicate filenames across a directory tree |
| `flatten` | Move all files from subdirectories up into the current directory |
| `flipvid` | Flip a video horizontally or vertically using ffmpeg |
| `jellyname` | Rename and organize media files into a Jellyfin-compatible folder structure |
| `kavitaname` | Rename manga/manhwa/book files for Kavita server compatibility |
| `mergemanga` | Merge individual One Piece chapter CBZ files into volume CBZ files with metadata |
| `prunelib` | Review the biggest, oldest or least-watched videos in a library and delete the ones you pick |
| `sortmedia` | Move video files into folders based on the camelCase type tag in each filename |
| `toolbox` | List all scripts in this repo with a brief description |
| `vidlib` | Video library with a web page: download videos, compress compress new videos to 1080p, tag the resolution and sort by trip |
| `wallready` | Prepare a folder of images for wallpaper use: JPG, downscaled and centre-cropped to 4K |

`inventory.html` is a standalone, dependency-free HTML dashboard for tracking Death Stranding
materials — just open it in a browser, no install needed.

---

## Installation

### Prerequisites

- **[uv](https://docs.astral.sh/uv/)** — installs every command as a real executable on your
  PATH, running on a Python that uv downloads and manages itself. You don't need Python
  installed separately, and upgrading the system Python (Homebrew, the Windows installer) can't
  break the install.
- Per-script external tools (only needed for the scripts you actually use):

| Tool | Required by |
|------|-------------|
| `ffmpeg` | `addsub`, `concatvid`, `cutvid`, `downscalevid`, `flipvid` |
| `ffprobe` (ships with ffmpeg) | `concatvid`, `downscalevid`, `vidlib` |
| `HandBrakeCLI` | `compressvid`, `vidlib` |
| `magick` (ImageMagick 7) | `convertimg`, `wallready` |
| `7z` (7-Zip) | `cybermod` |
| `yt-dlp`, `aria2c` | `vidlib` (downloads) |
| `addsub` (on PATH) | `autosub`, `vidlib` (optional — enables subtitle merging) |

### macOS

```bash
brew install uv
uv tool update-shell      # puts ~/.local/bin on your PATH; restart the shell afterwards
```

### Windows (Git Bash)

```bash
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

(`winget install astral-sh.uv` works too.) The installer adds `~/.local/bin` — where both `uv`
and the toolbox commands live — to your PATH; restart Git Bash so it picks that up. If a later
`uv tool install` warns that the folder isn't on your PATH, run `uv tool update-shell` and
restart again.

### Install

The same on both platforms:

```bash
git clone git@github.com:shubhs-dev/toolbox.git
cd toolbox
uv tool install --managed-python -e .
```

This gives you real commands on your PATH — identically on Windows Git Bash and macOS. No
symlinking, no manual `PATH` exports. `--managed-python` makes uv use its own Python (it
downloads one the first time), never the system's.

### Moving from pipx

If you installed with pipx before, remove that install first so the two don't fight over the
same command names:

```bash
pipx uninstall shubhs-toolbox
uv tool install --managed-python -e .
```

### Upgrading

Since the install is editable (`-e`), pulling new commits (`git pull`) is enough — changes
under `src/toolboxcli/` take effect immediately. If `pyproject.toml` itself changes (new
dependency, new script), re-run:

```bash
uv tool install --managed-python -e . --reinstall
```

### Uninstall

```bash
uv tool uninstall shubhs-toolbox
```

---

## Usage

Every command supports `-h`/`--help` for full usage, and every flag has both a short and long
form.

### addsub

Merge a subtitle file into a video using **ffmpeg**.

```bash
addsub movie.mkv movie.srt                          # soft-mux (default), update in place
addsub movie.mp4 movie.ass output.mp4                # soft-mux to a new file
addsub -b movie.mp4 movie.srt burned.mp4             # hard-burn (not reversible)
addsub -l jpn -t "Japanese" movie.mkv movie.ass      # custom language/title
addsub -u movie.mkv movie.srt                        # write "movie - Sub.mkv" instead of in-place
```

| Flag | Description |
|------|-------------|
| `-s, --soft` | Mux subtitles as a selectable stream (default) |
| `-b, --hard` | Burn subtitles into the video (not reversible) |
| `-l, --lang LANG` | Language code for the subtitle track (default: `eng`) |
| `-t, --title TITLE` | Title for the subtitle track (default: subtitle filename) |
| `-u, --suffix` | Append ` - Sub` to the output filename instead of updating in place (ignored if an output file is given) |
| `-k, --keep` | Keep the original video file (default: trash it when output differs from the original) |

The subtitle file is always trashed after a successful merge; the original video is also
trashed (unless `-k`/`--keep`) whenever the output is a separate file from the input.

---

### autosub

Auto-match subtitle files to videos in the current directory by episode code, then run
`addsub -u` for each match.

**Requires:** `addsub` (on PATH)

The episode code is the 2nd `' - '`-separated segment of the filename (e.g.
`Show - S01E01 - Title.mkv` → `S01E01`). Video files are snapshotted upfront so files created
by `addsub` are not re-processed.

```bash
autosub       # prompt for confirmation before each match
autosub -y    # skip all confirmation prompts
```

| Flag | Description |
|------|-------------|
| `-y, --yes` | Skip confirmation prompts and run `addsub` for all matches |

---

### check-deps

Scan subdirectories of `root` for specific npm package versions, by reading each
`package-lock.json` directly (no `npm install` required).

```bash
check-deps                                    # scan cwd's subdirectories
check-deps ~/projects
check-deps -p axios@1.14.1,0.30.4 -p plain-crypto-js
```

| Flag | Description |
|------|-------------|
| `-p, --package NAME[@VER1,VER2,...]` | Check for a package, optionally at specific versions (repeatable). Overrides the built-in default check list. |

---

### compressvid

Watch a folder for video files and transcode them with **HandBrake**, keeping whichever copy
is smaller.

```bash
compressvid                          # watch cwd, default preset
compressvid -p hw-1080 ~/Videos      # explicit preset & folder
compressvid -O                       # process existing files once and exit
compressvid -l                       # show available presets
compressvid -f                       # copy the original into the output folder when it's smaller
```

| Flag | Description |
|------|-------------|
| `-p, --preset NAME` | Preset stem (default: `hw-1080`) |
| `-o, --output DIR` | Sub-folder for transcoded results (default: `compressed`) |
| `-j, --workers N` | Number of parallel transcodes (default: `3`) |
| `-t, --interval SECS` | Seconds between folder scans (default: `10`) |
| `-O, --once` | Process existing files and exit (no continuous watch) |
| `-l, --list-presets` | Show available HandBrake presets and exit |
| `-P, --presets-dir DIR` | Use a custom presets directory instead of the bundled one |
| `-f, --copy-original` | Copy the original file to the output folder when it's smaller than the compressed version |

Presets ship inside the installed package (`hw-1080`, `hw-720` — hardware-accelerated H.265).
Use `-P` to point at your own directory of preset `.json` files instead.

**Audio:** both presets keep *every* audio track and encode each to stereo AAC at 160 kbps.
Surround is downmixed rather than kept as 5.1 — measured, that's the better trade, since
ffmpeg's AAC encoder handles multichannel poorly. Passthrough was measured ~28% larger on a
PCM + DTS + AAC source. These presets are shared with `vidlib`.

---

### concatvid

Join videos end to end using **ffmpeg**'s concat demuxer with `-c copy` — no re-encoding,
unless the parts don't match each other. List the videos yourself, in order, or let `-a` find
numbered parts in a folder.

```bash
concatvid a.mp4 b.mp4 c.mp4                  # join in this order → a.concat.mp4
concatvid "Day 1.mp4" "Day 2.mp4" -o Holiday # → Holiday.mp4, next to Day 1.mp4
concatvid a.mp4 b.mp4 -o ~/out/joined.mkv    # explicit path and container
concatvid a.mp4 b.mp4 -o Holiday -f          # replace an existing Holiday.mp4
concatvid -a                                 # scan the current folder for parts
concatvid -a ~/Videos/raw -y                 # scan a folder, no prompts
concatvid -a ~/Videos/raw -n                 # preview groups without joining
concatvid -g cpu a.mp4 b.mp4                 # force software encoding when re-encoding
```

| Flag | Description |
|------|-------------|
| `VIDEO ...` | Videos to join, in the order given |
| `-o, --output NAME` | Name of the joined video. A bare name goes next to the first part; with no video extension, the first part's is added. Default: the parts' shared base name |
| `-f, --force` | Replace an existing `-o` file (the old one goes to the Trash) |
| `-a, --auto [DIR]` | Instead of a list, scan `DIR` (default: `.`) for numbered parts and join every group |
| `-y, --yes` | Skip confirmation prompts |
| `-n, --dry-run` | Show what would be joined, and into what, without doing it |
| `-g, --gpu` | Encoder to use when parts need re-encoding: `auto` (default), `nvidia`, `amd`, `apple`, `cpu`. Env: `CONCATVID_GPU` |

**Listing videos** joins exactly those files in exactly that order — names don't need to match
or carry numbers. Without `-o` the result is named after the parts' shared base name
(`Movie 1.mp4` + `Movie 2.mp4` → `Movie.mp4`), or `<first>.concat` when there isn't one, and
never over an existing file. The output can never be one of the parts.

**`-a/--auto`** groups files by a common base name with a trailing sequence marker —
`Movie 1.mp4`/`Movie 2.mp4`, `Movie_pt1.mkv`/`Movie_pt2.mkv`, `Movie (01).mp4`/`Movie (02).mp4`,
etc. — and joins each group in sequence order. Files with no marker, or whose marker has no
siblings sharing the same base name and extension, are left untouched. Each output is named
after the shared base name (falling back to `<base>.concat<ext>`, `<base>.concat2<ext>`, … on a
collision — an existing file is never overwritten).

Either way, every video, audio and subtitle track is kept, and the original part files are trashed only once the output's duration is confirmed
to cover all of them; ffmpeg reports success even when it silently skipped an unreadable or
truncated part, so otherwise they're left in place with a warning.

Before joining, the parts are checked via **ffprobe** for stream-copy compatibility —
resolution, video codec, pixel format, audio codec, sample rate, channel count, and the
number and format of any further audio/subtitle tracks (cover art is ignored). Parts that
disagree are normalized to the group's dominant format first, after a prompt (auto-accepted
with `-y`):

- The **smallest** resolution in the group wins, so parts are only ever downscaled.
- Every other property is a majority vote across the parts; codecs with no available encoder
  fall back to H.264 / AAC.
- Parts already in the target format are left alone, and a part whose video already matches
  gets `-c:v copy` — so a group differing only in, say, channel count never has its video
  re-encoded.
- Normalized copies are temporary; only the originals get trashed.

Re-encoding runs on the GPU when one is available (NVENC / AMF / VideoToolbox), falling back
to the CPU encoder — same probing and auto-retry behavior as [`downscalevid`](#downscalevid).

One case still can't be repaired automatically: a group where some parts have an audio track
and others have none. Those are skipped with a warning. Where parts carry extra streams
(subtitles, secondary audio) *and* a re-encode is needed anyway, every part is normalized down
to its primary video + audio stream so the stream layouts agree.

---

### convertimg

Batch-convert all images in the current folder to a target format using **ImageMagick**.

**Requires:** `magick` (ImageMagick 7)

```bash
convertimg webp
convertimg -q 90 jpg
convertimg -f png jpg          # only PNGs → JPG
convertimg -f png -f webp jpg  # PNGs and WebPs → JPG
convertimg -k png
convertimg -n avif
```

| Flag | Description |
|------|-------------|
| `<format>` | Target format extension (e.g. `jpg`, `png`, `webp`, `avif`, `tiff`) |
| `-f, --from EXT` | Only convert images with this extension; repeatable (default: all image types) |
| `-q, --quality N` | Compression quality 1-100 (default: `85`; only for lossy formats) |
| `-b, --background COLOR` | Fill colour for transparent areas when the target has no alpha, e.g. `jpg` (default: `white`) |
| `-k, --keep` | Keep original files (default: trash them after successful conversion) |
| `-n, --dry-run` | Show what would be converted without doing anything |

---

### cutvid

Trim a video to a start/end time using **ffmpeg**.

```bash
cutvid -s 00:01:30 -e 00:05:00 movie.mkv        # cut from 1:30 to 5:00
cutvid -s 90 -d 120 movie.mp4 clip.mp4           # 2-minute clip starting at 1:30
cutvid -s 00:01:30 movie.mkv                     # trim start, keep to end
cutvid -e 00:05:00 movie.mkv                     # keep from beginning to 5:00
cutvid -s 1:30 -e 5:00 -r movie.mkv clip.mkv     # re-encode for frame accuracy
cutvid -s 1:30 -e 5:00 -f movie.mkv clip.mkv     # replace an existing clip.mkv
cutvid -s 1m30s -e 1.05.00 movie.mkv             # unit suffixes and dotted HH.MM.SS work too
```

| Flag | Description |
|------|-------------|
| `-s, --start TIME` | Start time (default: beginning). Accepts seconds, `MM:SS`, `HH:MM:SS`, `HH.MM.SS` or `1h02m03s` |
| `-e, --end TIME` | End time (default: end of file). Accepts seconds, `MM:SS`, `HH:MM:SS`, `HH.MM.SS` or `1h02m03s` |
| `-d, --duration DUR` | Duration of the cut instead of end time (same formats as `--start`) |
| `-r, --reencode` | Re-encode output (slower, frame-accurate). Default: stream-copy |
| `-f, --force` | Replace the output file if it exists (the old one is moved to the Trash) |

Times accept:

| Format | Example | Meaning |
|--------|---------|---------|
| `SS` | `90`, `90.25` | seconds |
| `MM:SS` | `1:30`, `1:30.5` | minutes and seconds |
| `HH:MM:SS` | `1:02:03`, `1:02:03.5` | hours, minutes and seconds |
| `HH.MM.SS` | `1.02.03` | same, with dots — only with all three fields, since a single dot is a decimal point (`1.30` is 1.3 seconds) |
| unit suffixes | `45s`, `1m30s`, `2h`, `1h02m03.5s` | any combination of `h`/`m`/`s`; a trailing bare number is the next unit down (`1h30` = 1h30m, `1m30` = 1m30s) |

The suffix form is the same style as the auto-derived output filename, so a time can be copied
straight out of one. Anything else is rejected up front, as is a start past the end of the
video. Every video, audio and subtitle
track is kept, along with chapters and (for MKV output) attachments such as subtitle fonts.
Cutting into a different container converts text subtitles to one it supports; image
subtitles MP4 can't hold are dropped with a warning.

Stream-copy mode (default) is near-instant but can only start on a keyframe. MKV output
starts at the last keyframe at or before the requested time, and cutvid warns you how much
earlier that is. MP4/MOV output keeps the same lead-in but hides it with an edit list, so it
plays from the exact requested time. Use `-r`/`--reencode` for a frame-accurate start in any
container.

The output is written to a temp file and only moved into place once ffmpeg succeeds. It can
never be the input file (however the path is spelled), and an existing file is only replaced
with `-f`. Output filename is auto-derived as `<stem>.cut.<start>-<end>.<ext>` if not given,
e.g. `movie.cut.1m30s-5m00s.mkv`.

---

### flipvid

Flip a video horizontally or vertically using **ffmpeg**.

```bash
flipvid -x movie.mp4                 # flip horizontally (mirror)
flipvid -y movie.mp4                 # flip vertically (upside down)
flipvid -x -y movie.mp4 out.mp4      # flip both axes
flipvid -x -q 20 movie.mp4           # custom encode quality
flipvid -x -f movie.mp4 out.mp4      # replace an existing out.mp4
```

| Flag | Description |
|------|-------------|
| `-x, --horizontal` | Flip horizontally (mirror left-right) |
| `-y, --vertical` | Flip vertically (upside down) |
| `-q, --quality CRF` | x264 CRF quality, lower is better (default: `18`) |
| `-f, --force` | Replace the output file if it exists (the old one is moved to the Trash) |

Flipping re-encodes the video stream (filters can't stream-copy); audio is always
stream-copied unchanged. Every audio and subtitle track is kept, along with chapters and (for
MKV output) attachments. The output is written to a temp file and only moved into place once
ffmpeg succeeds; it can never be the input file, and an existing file is only replaced with
`-f`. Output filename is auto-derived as `<stem>.flip.<h|v|hv>.<ext>` if not given.

---

### cybermod

Automatically install Cyberpunk 2077 mods from zip/rar/7z archives.

**Requires:** `7z` (7-Zip)

```bash
cd /path/to/mod/downloads
cybermod                              # process all archives in cwd
cybermod mod1.zip mod2.rar mod3.7z    # install specific archives
cybermod -g "D:/Games/Cyberpunk 2077" mod.zip
```

| Flag | Description |
|------|-------------|
| `-g, --game-dir DIR` | Cyberpunk 2077 install directory (env: `CYBERMOD_GAME_DIR`) |
| `-y, --yes` | Skip confirmation prompts |
| `-n, --dry-run` | Preview actions without installing |

Set `CYBERMOD_GAME_DIR` in your shell profile once instead of passing `-g` every time.
Recognizes mod roots up to 2 levels deep inside extracted archives (looking for `archive`,
`bin`, `engine`, `r6`, `red4ext`, `mods`, or `tools` folders). Loose `.archive` files with no
recognizable structure are routed to `archive/pc/mod/` automatically. Shows an overwrite
preview before each install and sends processed archives to Trash.

---

### downscalevid

Reduce a video's resolution using **ffmpeg**, encoding on the GPU when one is available.
Preserves aspect ratio unless an explicit `WIDTHxHEIGHT` is given; never upscales unless
forced.

```bash
downscalevid movie.mkv -r 1440p
downscalevid movie.mkv -r 1920x1080 movie.1080p.mkv
downscalevid movie.mkv -r 720 -c h265
downscalevid movie.mkv -r 1440p -g cpu   # force software encoding
downscalevid movie.mkv -r 1440p -f       # allow upscaling too
downscalevid movie.mkv -r 720 out.mkv -o # replace an existing out.mkv
```

| Flag | Description |
|------|-------------|
| `-r, --resolution RES` | Target resolution: preset (`8k`, `4k`/`2160p`, `1440p`, `1080p`, `720p`, `480p`, `360p`), a height, or `WIDTHxHEIGHT` |
| `-c, --codec {h264,h265}` | Video codec to re-encode with (default: `h264`) |
| `-g, --gpu {auto,amd,apple,nvidia,cpu}` | Which encoder to use (default: `auto`). Env: `DOWNSCALEVID_GPU` |
| `-f, --force` | Allow upscaling (default: refuse and skip) |
| `-o, --overwrite` | Replace the output file if it exists (the old one is moved to the Trash) |

Re-encodes the video stream (resolution changes can't stream-copy); audio is always
stream-copied unchanged. Every audio and subtitle track is kept, along with chapters and (for
MKV output) attachments. Output filename is auto-derived as `<stem>.<resolution>.<ext>` if
not given. The output is written to a temp file and only moved into place once ffmpeg
succeeds; it can never be the input file, and an existing file is only replaced with
`-o/--overwrite` (the old one goes to the Trash).

**GPU acceleration.** With `-g auto` (the default) the first working hardware encoder is
used, in platform preference order — **Apple VideoToolbox** on macOS, **NVIDIA NVENC** then
**AMD AMF** elsewhere — falling back to the CPU encoder (`libx264`/`libx265`) when none is
usable. Availability is confirmed by encoding a throwaway frame, since `ffmpeg -encoders`
lists encoders the build was compiled with whether or not the hardware is present; on Intel
Macs this correctly rejects VideoToolbox, which has no constant-quality mode there. If an
auto-selected GPU then fails on the actual input, the encode is retried once on the CPU. Ask
for a vendor explicitly (`-g nvidia`) and it's used or the run fails — no silent fallback.
Only encoding is offloaded; decoding and scaling stay on the CPU. Hardware encoders are much
faster but not bit-for-bit equal to `libx264` at the same nominal quality — use `-g cpu` when
size/quality matters more than time.

---

### finddupes

Find duplicate filenames across a directory tree.

```bash
finddupes
finddupes ~/Documents
finddupes -c -i /path/to/dir
```

| Flag | Description |
|------|-------------|
| `-c, --count` | Sort results by duplicate count (highest first) |
| `-i, --ignore-case` | Case-insensitive filename comparison |

---

### flatten

Recursively move all files from subdirectories up into the current directory. Empty
subdirectories are removed afterwards.

```bash
flatten
flatten --yes
flatten --dry-run
```

| Flag | Description |
|------|-------------|
| `-y, --yes` | Replace all conflicting files without prompting |
| `-n, --dry-run` | Show what would be moved without making changes |

When a filename conflict occurs (and `-y` is not set), you're prompted with size,
modification date, and path info for both files (`[y/n/a]`, where `a` replaces all subsequent
conflicts).

---

### jellyname

Rename and organize media files for Jellyfin compatibility. Scans the top level of a directory
for video files, parses each filename for title/year/season/episode/media-info tags, then
renames and moves each file into a Jellyfin-compatible folder structure.

```bash
jellyname
jellyname ~/Downloads/movies
jellyname --dry-run /mnt/media/shows
```

| Flag | Description |
|------|-------------|
| `-n, --dry-run` | Preview what would be renamed without moving any files |

Output structure:
- Movies: `Movie Name (Year) [tags] - 1080p.mkv`
- TV/Anime: `Show Name/Season 01/Show Name S01E01 [tags] - 720p.mkv`

Resolution is preserved as a Jellyfin version-label suffix (` - 1080p`), since that's the only
tag Jellyfin itself understands for sorting multiple versions of the same title. Other detected
media info is preserved in a bracketed suffix instead of being discarded:

- **Source/edition**: BluRay, WEB-DL, REMUX, IMAX, PROPER, REPACK, EXTENDED, etc.
- **Video codec/bit-depth**: x264, x265, HEVC, 10bit, etc.
- **HDR/Dolby Vision variant**: HDR10, HDR10+, Dolby Vision, HDR, SDR
- **Audio codec**: Atmos, TrueHD, DTS, DDP5.1, etc.

e.g. `Dune Part Two (2024) [BluRay IMAX x265 HDR10 DDP5.1 Atmos] - 2160p.mkv`. Unrecognized
tokens (release-group names, hashes, fansub tags) are still dropped, and multi-episode files
(`S01E01-E02`) are supported. Characters illegal in Jellyfin paths (`< > : " / \ | ? *`) are
removed.

---

### kavitaname

Rename manga/manhwa/book files for Kavita server compatibility.

```bash
kavitaname manga                                    # current folder is the series
kavitaname -s "One Piece" -m volumes.json manga
kavitaname -n manhwa ~/manga/Solo\ Leveling         # preview only
kavitaname -r manga ~/manga                         # every subfolder, one run
kavitaname book ~/novels/Overlord
```

| Argument/Flag | Description |
|----------------|-------------|
| `type` | Content type: `manga`, `manhwa`, or `book` |
| `directory` | Series folder to process (default: current directory) |
| `-s, --series` | Series name override |
| `-S, --series-from` | Where to take the series name from: `folder` (default), `comicinfo`, `filename` |
| `-m, --volume-map` | Chapter-to-volume map file (JSON or pipe-delimited) |
| `-r, --recursive` | Treat each immediate subfolder as its own series |
| `-C, --no-comicinfo` | Don't read `ComicInfo.xml` from inside archives |
| `-n, --dry-run` | Preview renames without making any changes |

Filenames do **not** have to already look like `Chapter 12.cbz` — scanlator releases are
parsed directly, with release groups, hashes and `(Digital)`-style tags dropped:

| Input | Output |
|-------|--------|
| `[Hidoi]_Amaenaideyo_MS_vol01_chp02.rar` | `Amaenaideyo MS c002 (v01).rar` |
| `Series - Vol. 04 Ch. 054.5.cbz` | `Series c054.5 (v04).cbz` |
| `Series 018.5 (2019) (Digital).cbz` | `Series c018.5.cbz` |
| `Series_v11_c90-98.zip` | `Series c090-098 (v11).zip` |
| `Series - Side Story.cbz` | `Specials/Series SP01 - Side Story.cbz` |

Output format:
- Chapter + volume: `{Series} c{ch:03d} (v{vol:02d}).cbz` → e.g. `One Piece c001 (v01).cbz`
- Chapter only: `{Series} c{ch:03d}.cbz` → e.g. `Solo Leveling c001.cbz`
- Whole volume: `{Series} v{vol:02d}.cbz`
- Prologue: `{Series} c000.N.cbz`
- Special: `Specials/{Series} SP{NN} - {Title}.cbz`

Specials are detected the way Kavita detects them: an existing `SP##` marker always wins, and
a keyword (Omake, Extra, Side Story, One-Shot, TPB, …) only counts when the filename carries
no volume or chapter number — so `v20 c171-180 Omake` stays a chapter. A trailing keyword on a
numbered file is kept as a descriptor (`One Piece v01 - Omake.cbz`) rather than dropped, so it
can't collide with the real volume archive.

For `.cbz`/`.zip` archives, `ComicInfo.xml` inside the archive is read as a fallback and fills
in only what the filename left out (Series, Volume, Number, Title, and a `Format` that marks
the file special). Values parsed from the filename are never overridden. `.cbr`/`.rar` are RAR
archives and are skipped for this — no extra dependency — but their filenames are still parsed.

The volume map (`-m`) accepts two auto-detected formats: JSON, as in
[`mergemanga`'s bundled data](src/toolboxcli/mergemanga/data/volume_map.json)
(`[{"volume": 1, "start": 1, "end": 8}, …]`), and the legacy pipe-delimited form
(`vol|first_chapter|last_chapter|Title`, with `#` comments). It only ever fills a gap — a
volume stated in the filename or in `ComicInfo.xml` wins. Chapters absent from the map are
named without volume info rather than skipped.

Cover images (`cover.jpg`, `folder.png`, `!cover.*`) and OS junk files are left alone.

---

### mergemanga

Merge individual One Piece chapter CBZ files into volume CBZ files with proper metadata.
Uses the stdlib `zipfile` module directly — no external archiving tool required.

```bash
mergemanga                          # merge chapters in cwd, output to ./Volumes
mergemanga /path/to/chapters /path/to/output
mergemanga --dry-run
```

| Flag | Description |
|------|-------------|
| `-n, --dry-run` | Preview which volumes would be built, without writing anything |

Chapter files must be named `Chapter N.cbz` (e.g., `Chapter 1.cbz`, `Chapter 42.cbz`). Includes
a built-in volume map covering volumes 1–115 with official English titles.

---

### vidlib

A video library: download videos into it from a web page, optimise new videos to 1080p, tag
them with their resolution and sort them into trip folders. Built to run continuously next to
the library — the pipeline is `compressvid` and `sortmedia` welded into one unattended process.

```bash
vidlib /Volumes/Media/Library         # watch, poll every 30s, serve the web page
vidlib -W                             # just the pipeline, no web page
vidlib -X ~/Downloads/tmp             # download only, into that folder
vidlib . -A ~/Desktop/clip.mp4        # add a video as is: tagged and filed, never encoded
vidlib -P 9000 -b                     # web page on port 9000, don't open a browser
vidlib -O                             # process what's there, then exit
vidlib -n                             # dry run: show what would happen
vidlib -r                             # report sub-1080p files and the review queue
vidlib -l                             # list every video in the library, alphabetically
vidlib -l library.txt                 # write that list to library.txt instead
vidlib -a alice                       # everything with Alice in it
vidlib -R 720p -c Lakes               # filters combine: 720p videos in Lakes
vidlib -T Restored                    # by [tag]
vidlib -U                             # files not named People - Title - Collection
vidlib -D                             # videos the library holds more than once
vidlib -s                             # merge waiting subtitles only, then exit
vidlib -H 01:00-07:00 -L 4            # only encode off-hours, and back off under load
```

For every video that appears **directly in the library root**:

1. **Transcode** — the `hw-720` preset for ≤720p sources, `hw-1080` otherwise. Neither preset
   upscales. Every audio track is kept and encoded to stereo AAC 160k. The result is kept only
   when it's smaller than the original; the original goes to the Recycle Bin, never deleted
   outright.
2. **Tag** — `People - Title - Trip` → `People - Title - Trip [1080p].mp4`, plus `10bit` when
   the file is 10-bit (`[1080p 10bit]`; 8-bit carries no token, since it's the norm). Tags you
   added yourself survive, and the managed tokens are replaced rather than duplicated, so
   `[Restored 720p]` becomes `[Restored 1080p]` after an external upscale. The tag always
   describes the finished file — if a transcode is discarded for being larger, it reverts.
3. **Deduplicate** — a copy of the same video anywhere in the library is found: the same people,
   title and collection, whatever the order or case and whatever the tags, so
   `Alice, Bob - Day 1 - Lakes [1080p]` and `bob and alice - day 1 - lakes` are one video.
   People may be separated by commas, `&` or `and`. Only names with all three parts take part:
   a file named any other way (`Movie - Director's Cut`, `IMG_1234`) is never matched as a
   duplicate, and `-U` lists those files so you can rename them. A higher-resolution or more complete copy
   replaces the one already in the library (the old one goes to the Recycle Bin). Anything
   ambiguous — higher res but shorter, longer but lower res — is parked in `_review/` rather
   than decided unattended.
4. **Sort** — moved into the folder named by the **Trip** field, the last `' - '`-separated part
   of the name. A name without all three fields is tagged, left in root and reported.

Subfolders are never scanned, so trip folders and any folder you use to stage files by hand are
left completely alone.

**Progress** — while a batch runs, the current file's encode bar sits above an overall bar
pinned to the bottom of the screen: files done and left (`Overall 3/10 · 7 left`), a running
tally (`✓ 3 done  1 skipped`), elapsed time and an ETA. The overall bar advances during each
encode rather than only when a file finishes, so the ETA is meaningful even with a few long
files.

**Subtitles** — on every poll, vidlib also looks for subtitle files sitting in the root
and matches each one to the video with the same name — language/flag suffixes (`.en`, `.pt-BR`,
`.forced`) and the video's `[tags]` ignored, people in any order, case and spacing ignored, as
duplicate detection reads names. Only when no video has the same name does it fall back to the
video's first `' - '` field appearing in the subtitle's name (clip codes like `A001C003`). Only an unambiguous 1:1 match is merged — a subtitle matching more
than one video, or a video claimed by more than one subtitle, is left alone and retried next
poll rather than guessed at. A match is merged in with `addsub` and tagged `[... Sub]` in the
bracket group instead of getting `addsub -u`'s `" - Sub"` filename suffix. This works whether
the subtitle is already there when the video is first processed, or dropped in later for a
video that's already been optimised and filed into a trip folder — the video is found wherever
it currently lives. Requires the `addsub` command; if it's missing, subtitle merging is silently
disabled rather than blocking the rest of the pipeline. Disable it with `-S/--no-subs`, or run
*only* this step with `-s/--subs-only` — one merging pass, then exit, encoding and sorting
nothing. It needs just `addsub` and `ffprobe`, and is dispatched before the HandBrake check and
the encoder probe, so it stays quick and works on a machine that can't encode at all.

**Querying** — `-l/--list` prints the library's videos as a table (file, resolution, bit depth,
folder), alphabetically, and exits. Unlike the pipeline this covers the *whole* library, trip
folders included; `_review/` and files that have gone are left out. Narrow it with any mix of:

- `-a/--person NAME` — videos with that person (`-a ali` finds Alice)
- `-c/--collection NAME` — videos in that collection
- `-R/--resolution RES` — videos at that resolution (`720p` or just `720`)
- `-T/--tag TAG` — videos carrying that `[tag]`
- `-U/--unnamed` — videos whose names don't follow `People - Title - Collection`. They get no
  people or collection, so person/collection searches and duplicate matching skip them; the
  `-r` report says how many there are

All are case-insensitive, and every text filter matches part of a value. A filter on its own
implies `-l`, so `vidlib -a alice` is enough. Pass a filename (`-l library.txt`) to write the
matching names there, one per line, instead of printing a table; you're asked before an existing
file is overwritten.

`-D/--duplicates` lists every video the library holds more than once — the same people, title and
collection, in any order or case and whatever the tags — with each copy's resolution, duration,
size and folder. It's a report only: nothing is moved or deleted.

| Flag | Default | Purpose |
|------|---------|---------|
| `-t, --interval` | `30` | Seconds between scans |
| `-O, --once` | off | Process existing files and exit |
| `-n, --dry-run` | off | Show planned rename, destination and duplicate verdict; change nothing |
| `-r, --report` | off | List files below 1080p and the review queue, then exit |
| `-l, --list` | off | List the library's videos as a table, then exit; with a `FILE` argument, write their names there instead |
| `-a, --person` | — | With `-l`: only videos with this person (case-insensitive, partial) |
| `-c, --collection` | — | With `-l`: only videos in this collection (case-insensitive, partial) |
| `-R, --resolution` | — | With `-l`: only videos at this resolution, e.g. `720p` or `720` |
| `-T, --tag` | — | With `-l`: only videos carrying this `[tag]` (case-insensitive, partial) |
| `-U, --unnamed` | off | With `-l`: only videos not named `People - Title - Collection` |
| `-D, --duplicates` | off | List videos the library holds more than once, then exit (report only) |
| `-A, --adopt` | — | Add these videos without encoding them: moved into the root, then tagged, deduplicated and filed (give `ROOT` first) |
| `-F, --force` | off | Re-process files the library database already knows as done |
| `-g, --gpu` | `auto` | `auto`/`nvidia`/`amd`/`apple`/`intel`/`cpu`; env `VIDLIB_GPU` |
| `-Q, --quality` | preset's | Override the preset's quality value |
| `-B, --bit-depth` | `auto` | `auto` matches the source (10-bit stays 10-bit, 8-bit isn't inflated); `8`/`10` force it |
| `-H, --hours` | none | Only start encodes inside e.g. `01:00-07:00` |
| `-L, --max-load` | none | Skip a scan while the 1-min load average exceeds this (POSIX) |
| `-d, --on-duplicate` | `auto` | `auto` (replace on a clear win, park anything ambiguous) \| `review` (park every duplicate) \| `ignore` (file both copies) |
| `-N, --no-nice` | off | Don't de-prioritise the encoder process |
| `-p, --preset-1080` | `hw-1080` | Preset for sources above 720p |
| `-q, --preset-720` | `hw-720` | Preset for sources at 720p or below |
| `-S, --no-subs` | off | Don't scan for or merge matching subtitle files |
| `-s, --subs-only` | off | Merge waiting subtitle files and exit; encode and sort nothing |
| `-W, --no-web` | off | Run the pipeline without the web page (and so without downloads) |
| `-X, --download-only` | off | Serve only downloads, into `DIR` (default: `.`); encode nothing |
| `-P, --port` | `8642` | Port for the web page on 127.0.0.1 |
| `-b, --no-browser` | off | Don't open the web page in a browser on start |
| `-j, --jobs` | `2` | Downloads to run at once |

The library root defaults to env `VIDLIB_LIBRARY`, else the current folder.

**Downloads (the web page)** — while vidlib watches, it serves a page at
`http://127.0.0.1:8642/` and opens it in your browser. Paste a URL and the page's title; the
title is split into segments on ` - ` (or a spaced `–`/`—`) and `|`, and each segment gets a
role — **P**eople, **T**itle, **C**ollection or skip — which you can change with a click:

```
DougDoug - Atrioc - The Conspiracy Episode - Part 1 - Lemonade Stand | YouTube
P          P        T                        T        C                 ×
→ DougDoug, Atrioc - The Conspiracy Episode - Part 1 - Lemonade Stand.mp4
```

Any number of segments can be people (each split into names on `,` `&` `and` `x` `+`) or
title (kept in order, joined with ` - `); there's one collection; the part after the last `|`
is usually the site's name and starts out skipped. The order in the title doesn't matter. The
first guess comes from the people and collections the library already has: a name written
differently only in case or spacing takes the library's spelling, and one that's close
(`Doug-Doug`, `Jose`) offers it — "did you mean DougDoug?" — without changing anything until you
click. The People and Collection boxes autocomplete from the library too. **Add to queue** is
only enabled once the name has people, a title and a collection; "Download anyway" is there for
the odd video you'll name later.

Under each field are pills. Known people and collections that the title mentions are offered
there: clicking one moves the name out of the title and into its field, which makes short
work of a badly named file whose whole name is its title. The fill-ins **Unknown** (every field)
and **Clips** (collection) are there for a field you can't name yet. Both the Add form and the
Library's rename form have them, and both warn when the name is one the library already has: duplicate
detection would then treat the two files as the same video and keep only the better copy —
so two different clips can't both be `Unknown - Unknown - Clips`. When renaming from the Library,
a field left blank is taken as **Unknown**.

Downloads run `-j` at a time (yt-dlp with aria2c), each in its own folder under `_incoming/`,
which the pipeline never looks in. A finished download is moved into the root, where the
pipeline picks it up like any other new file; a name that's already taken there waits on the
page instead of overwriting anything. The page shows each download's progress, speed and time
left, an overall bar, and Retry / Cancel / Remove. **Paste many URLs** fetches each one's title
and lists them under *Needs naming* to name one by one. The queue is kept in the library
database, so quitting mid-download resumes next time. Paste a link you've downloaded before
— even written differently, like `youtu.be/…` for a `youtube.com/watch?v=…` — and the form says
when, under which name, and where that video is now (or that it's gone). The page only listens on `127.0.0.1`
and refuses requests from other websites.

**The library on the page** — the **Library** tab is a browsable view of the database: search
by name, filter by person, collection and resolution, or switch to the duplicates, the badly
named files (`-U`), what's waiting in `_review/`, or **Parts**: sets of videos that look like
parts of one (`P1`, `Part 2`, `Ep 3`, `Part II`, `2/3`, `Part 1-5`…), matched loosely by title
whatever their people or collection, with gaps and doubled parts flagged and a **Not parts**
button for a wrong guess. **Merge…** opens the merge screen: the parts in order (tick
what belongs, drag or ↑/↓ to reorder, search to add a missed one), the merged video's name
(everyone from every part, the most shared collection, the title without the marker — all
editable), and what it takes: "joined as they are", or which part will be re-encoded or
downscaled to match. Merges wait for the pipeline (one heavy job at a time; Pause applies)
and are done by `concatvid`, which only sends the parts to the Trash once the joined video is
confirmed to be as long as all of them. The result is filed like any new video, without
being encoded again (the parts already were). To merge videos the Parts view didn't find, tick them
in the grid (a checkbox appears on hover) and choose **Merge selected…**. Each video has a thumbnail (one frame,
made with ffmpeg the first time it's shown and cached in `.vidlib-cache/` at the library
root). Rest the pointer on one for a moment and it steps through twelve frames from across the
video (moving across the picture scrubs them). Click one to **Play** it, **Show in Finder**,
**Rename** it — the same People / Title /
Collection fields and suggestions as a download, keeping its `[tags]`, and moved into the new
collection's folder with its history — or **Move to Trash** (after a confirmation; never
deleted outright). Only files the database knows can be touched, and the file the pipeline is
working on is left alone.

**Review on the page** — the **Review** tab (with a count when anything is waiting) shows each
file parked in `_review/` beside the library copy it was compared with — resolution, bit depth,
length, size, subtitles and tags, the better value highlighted. **Keep this one** sends the
library's copy to the Trash; **Keep the library's** trashes the parked one; **Keep both** asks
for a different title (or people) first, since two copies of the same video can't both stay.
A kept file goes back to the library root and the pipeline files it — encoding it first if it
was parked before its encode.

**The pipeline on the page** — the page also shows what the pipeline is doing: the file being
encoded with its preset, encoder, progress and time left; the rest of the batch; and what
happened to the last few files (sorted into which folder, parked for review, skipped). **Pause**
stops after the current file; **Stop now** also stops its encode, leaving the file in the root
untouched and not counted as a failure. New files wait in the root until you **Resume**. The
`-H`/`-L` settings are shown but only changed on the command line. While anything downloads or
encodes, vidlib keeps a Mac from idle-sleeping (`caffeinate -i`), and lets it sleep again once
idle. If the library's disk is unplugged, the pipeline waits — nothing is synced, so nothing is
marked missing — and the page says so until the disk is back. An imported download's row
follows its video to wherever the pipeline files it.

**Download-only** (`-X DIR`) — for when the library's disk is full: nothing is encoded, and
downloads go into `DIR` (default: the current folder) instead, still recorded in the library's
database. Once there's room, **Move into library** on the page moves them into the root
(copying across disks safely, checking the size before removing the original), and the
pipeline takes it from there.

**Adopting** (`-A FILE...`) — for a video that's already in good shape: it joins the library
without being encoded. Each file is moved into the root (from anywhere — another disk, one of
the library's folders) and recorded as already compressed, so the pipeline only tags it,
compares it against the library's copies and files it. If vidlib is running on the library
that happens on its next poll — `-A` doesn't need the library's lock, so it runs alongside;
otherwise `-A` files it right away. A name the root already has is refused, never
overwritten. `-n` previews it.

**Encoder** — the bundled presets specify AMD's `vce_h265`, but the encoder is chosen at
runtime: if the preset's own encoder is available it's used untouched, otherwise HandBrake's
detected encoders are checked in a per-platform order (NVENC / VCE / QSV / VideoToolbox, then
x265 on the CPU). Only `-e` and `-q` are overridden on the command line, so preset files are
never edited. VideoToolbox's quality scale is inverted relative to the others and is remapped
accordingly. An auto-selected GPU that fails mid-encode is retried once on the CPU; an
explicitly requested one is not.

**Bit depth** — every backend has a 10-bit encoder too (`vce_h265_10bit`, `nvenc_h265_10bit`,
`qsv_h265_10bit`, `vt_h265_10bit`, `x265_10bit`). On `auto` it follows the source: a 10-bit or
HDR grade stays 10-bit instead of being flattened, while 8-bit sources are left at 8-bit
(pushing them through a 10-bit encoder gains nothing real). 12-bit sources encode as 10-bit. If
the chosen backend has no 10-bit encoder in your HandBrake build, it stays 8-bit and says so.

**Running it as a service** — it's a plain foreground process, so `systemd`, `nohup` or a
Windows scheduled task all work. It de-prioritises the encoder by default, and
`-H`/`-L` keep it out of the way of live playback. A run killed mid-encode restores the source
file on restart.

**Keeping files out** — files in folders are left alone, but they *are* recorded as part of the
library (shown on the page, compared as duplicates). To keep a folder out altogether, put an
empty `.vidlib-ignore` file in it (`touch "Not mine/.vidlib-ignore"`): nothing in it is recorded,
shown, matched as a duplicate or filed into, and anything already recorded there is forgotten.

**Library database** — everything vidlib knows lives in `.vidlib.db` at the library
root: a single SQLite file, no server, and you can open it with any SQLite viewer. Before each
poll it's synced with the disk — a file you rename or move by hand keeps its history and is
never re-encoded (it's recognised by its size and modified time), files you add to folders
yourself are recorded as they are, and files that have gone are marked missing. The old
`.optimiselib.json` log is imported on the first run and kept as
`.optimiselib.json.migrated`, and a library last used as `optimiselib` has its `.optimiselib.db`
renamed `.vidlib.db` (a `.video-download-queue.json` in the download folder is imported the
same way, once). A dry run (`-n`) works on an in-memory copy, so it changes nothing. Keep the library on a
local disk: SQLite's locking isn't reliable over a network share.

---

### prunelib

Free up space in a media library without buying another disk. Run it from the library root:
it ranks the videos below the current directory and lists the top few for you to review,
open and delete.

```bash
prunelib              # 10 biggest videos
prunelib 10 size
prunelib oldest 25    # count and type in either order
prunelib 20 stale -p
```

| Type | Ranks by |
|------|----------|
| `size` | Biggest first (default) |
| `oldest` | Oldest modified date first |
| `stale` | Least recently opened first (last-access time). Unreliable where the drive doesn't record access times (Windows turns that off on large NTFS volumes); prunelib warns when it looks that way |

| Flag | Description |
|------|-------------|
| `-a, --all-files` | Rank every file, not just videos |
| `-p, --permanent` | Delete outright instead of moving to the Recycle Bin/Trash (you type the item count to confirm) |
| `-r, --reset-history` | Forget which files were already reviewed in this directory |

At the prompt, type row numbers or ranges (`1 3 5-7`) to toggle them, `a`/`n` to select
all/none, `o 3` to open row 3 in your default player, `d` to delete the selection and `q` to
finish. Before anything is deleted you see the full list and confirm it.

Deleting a video also removes its sidecars: subtitles, `.nfo`, artwork and `.trickplay`
named after it. A folder left holding only Jellyfin metadata, artwork or subtitles is removed
too, so a finished show goes entirely once its last episode does. Files go to the Recycle
Bin/Trash by default, so empty it to actually free the space. On a network share there may be
no Recycle Bin, in which case the server's own recycle folder (Synology `#recycle`) is used or
the file is deleted outright.

Files you were shown and kept are recorded in `.prunelib-history.json` in the directory you ran
it from and skipped next time, so each run moves on to new suggestions. A file comes back if
its size or modified date changes, for example after `vidlib` re-encodes it. History is
saved when you finish with `q`; Ctrl+C leaves it untouched. Dot-folders and NAS
recycle-bin/snapshot folders (`#recycle`, `@Recycle`, `$RECYCLE.BIN`, `@eaDir`) are never
scanned.

---

### sortmedia

Move video files into folders based on the camelCase type tag in each filename.

```bash
sortmedia
sortmedia ~/Videos/LocationA
```

For each video file in the current directory, the last `' '`/`_`/`-`/`.`-separated segment of
the filename stem is treated as a camelCase type identifier (e.g. `elephantHerd` →
`elephant Herd`, `ATCRecording` → `ATC Recording`). The base folder ("Location A" — passed as
an argument, or prompted for interactively) is searched recursively for a matching subfolder
(case-insensitive); if found, the video is moved there, otherwise you're prompted `[y/N]` to
create it.

---

### toolbox

List all scripts in this repo with a brief description of each.

```bash
toolbox
```

---

### wallready

Prepare every image in a folder for use as a wallpaper, using **ImageMagick**.

**Requires:** `magick` (ImageMagick 7)

```bash
wallready                      # prepare the current folder for 4K
wallready ~/Pictures/walls     # ...or another folder
wallready "arc raiders"        # ...and rename everything arc-raiders-<id>.jpg
wallready -p "arc raiders" ~/Pictures/walls
wallready -n                   # dry run — show the plan, change nothing
wallready -s 1440p             # target 2560x1440 instead of 4K
wallready -s 3440x1440         # or any WIDTHxHEIGHT
wallready -q 95 -S             # higher JPEG quality, strip metadata
wallready -k                   # move originals into originals/ instead of trashing
```

| Flag | Description |
|------|-------------|
| `[directory\|prefix]` | A folder of images to prepare (default: the current directory), or — when it doesn't name one — a prefix to rename every image after, as `<prefix>-<id>` |
| `-p, --prefix NAME` | Rename every image `<prefix>-<id>`, keeping the id its download name carried and generating one when it carried none |
| `-s, --size SIZE` | Target size: a preset (`8k`, `5k`, `4k`, `2160p`, `1440p`, `1080p`, `ultrawide`) or `WIDTHxHEIGHT` (default: `4k`) |
| `-q, --quality N` | JPEG quality 1-100 (default: `92`) |
| `-b, --background COLOR` | Fill colour for transparent areas, which JPEG can't store (default: `white`) |
| `-k, --keep` | Move originals into `originals/` instead of the Trash |
| `-S, --strip` | Strip metadata (EXIF, colour profiles) from the finished wallpapers |
| `-f, --force` | Replace an existing `.jpg` of the same name (it goes to the Trash) |
| `-n, --dry-run` | Show what would happen without changing anything |

Every image becomes a JPG at the target size — 3840x2160 by default:

| Source | What happens |
|--------|--------------|
| Bigger than the target | Scaled down until it just *covers* the target, then centre-cropped to it |
| Exactly the target size | Re-encoded to JPG, or left alone if it already is one |
| Stored sideways under an EXIF tag | Re-encoded even at the target size, so the rotation is baked in |
| Portrait (landscape target) | Moved untouched into `portrait/` |
| Smaller on either axis | Moved untouched into `too-small/` |

"Covers, then crops" is the difference between a wallpaper and a zoomed-in slice of one: a
6000x4000 photo is scaled to 3840x2560 and loses 16% off its height, rather than having a
3840x2160 rectangle cut out of its middle at full resolution. A 16:9 source crops nothing, so
it's a pure downscale. Nothing is ever upscaled — an image short of the target on either axis
is set aside instead, since a stretched wallpaper is worse than no wallpaper, and a portrait
photo is set aside separately because no crop of it fills a wide screen.

EXIF orientation is applied *before* anything is measured or cropped (otherwise a portrait
phone photo measures as landscape and gets cropped along the wrong axis), and a file that only
reaches the target size *once rotated* is re-encoded rather than skipped, so viewers that
ignore the orientation tag still see it the right way up, transparency is
flattened onto `--background`, CMYK and wide-gamut sources are converted to sRGB, and animated
GIF/WebP files contribute their first frame. Each original goes to the Trash once its wallpaper
has been written successfully, `-k/--keep` moves it into `originals/` instead, and the encode
lands via a temp file so an interrupted run can't leave a half-written JPG where the original
used to be. Subfolders aren't scanned, so re-running on the same folder won't re-process
what's already been set aside.

#### Renaming a batch

Wallpapers come off a site named after the site: `1402962.jpg`, `wallhaven-e204cm.jpg`. Give
`wallready` a prefix and each one is renamed after what's actually in it, keeping the id so
the file stays traceable back to where it came from:

| Downloaded as | `wallready "arc raiders"` |
|---------------|---------------------------|
| `1402962.jpg` | `arc-raiders-1402962.jpg` |
| `wallhaven-e204cm.jpg` | `arc-raiders-e204cm.jpg` |
| `pexels-photo-1402962.jpg` | `arc-raiders-1402962.jpg` |
| `cool space shot.png` | `arc-raiders-blrkdg.jpg` |
| `arc-raiders-e204cm.jpg` | `arc-raiders-e204cm.jpg` |

The id is whatever the name already carried — a run of digits, or a letters-and-digits token
like `e204cm` — which is also how the site's own name drops out without `wallready` keeping a
list of sites. A name with nothing identifiable in it (`cool space shot`) gets a fresh random
id instead, and a name that's already `<prefix>-<id>` keeps it, so re-running the same
command doesn't stack prefixes or renumber anything. Resolution tokens (`1920x1080`, `4k`)
never count as the id, and two files that land on the same name get `-2`, `-3` appended
rather than overwriting each other.

The lone argument is the folder when it names one and the prefix otherwise, so
`wallready ~/Pictures/walls` still works unchanged — use `-p/--prefix` to pass both at once,
or to force a prefix that happens to share its name with a real folder. Images set aside into
`portrait/` or `too-small/` are renamed too; the originals keep their download name.

---

### inventory.html

A standalone, single-file HTML dashboard for tracking Death Stranding materials. No server or
dependencies required — just open it in a browser.

```bash
open inventory.html        # macOS
start inventory.html       # Windows
xdg-open inventory.html    # Linux
```

---

## Development

The install is editable, so changes under `src/toolboxcli/` take effect immediately without
reinstalling. During development you can also run a script directly without going through the
installed entry point:

```bash
python -m toolboxcli.finddupes.cli --help
```

Shared code (logging, trash, confirmation prompts, progress bars, size formatting, tool
presence checks, git helpers, secret storage) lives in `src/toolboxcli/_common/` and is reused
across scripts — see [CLAUDE.md](CLAUDE.md) for the full architecture.

## License

[MIT](LICENSE)
