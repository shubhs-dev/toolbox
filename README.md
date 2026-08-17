# Toolbox

A collection of CLI tools for media processing, file organization, and game modding, written
in Python and installed as a single pipx package.

## Scripts

| Script | Description |
|--------|-------------|
| `addsub` | Merge a subtitle file into a video (soft-mux or hard-burn) via ffmpeg |
| `autosub` | Auto-match subtitle files to videos by episode code, then run `addsub` |
| `check-deps` | Scan subdirectories for specific npm package versions |
| `compressvid` | Watch a folder for videos and transcode them with HandBrake; keeps the smaller copy |
| `concatvid` | Concatenate split video parts in a folder using ffmpeg (stream-copy, re-encoding only mismatched parts) |
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
| `optimiselib` | Watch a library root: compress new videos to 1080p, tag the resolution and sort by trip |
| `sortmedia` | Move video files into folders based on the camelCase type tag in each filename |
| `toolbox` | List all scripts in this repo with a brief description |

`inventory.html` is a standalone, dependency-free HTML dashboard for tracking Death Stranding
materials — just open it in a browser, no install needed.

---

## Installation

### Prerequisites

- **Python 3.9+**
- **[pipx](https://pipx.pypa.io/)** — installs each command as an isolated, real executable on your PATH
- Per-script external tools (only needed for the scripts you actually use):

| Tool | Required by |
|------|-------------|
| `ffmpeg` | `addsub`, `concatvid`, `cutvid`, `downscalevid`, `flipvid` |
| `ffprobe` (ships with ffmpeg) | `concatvid`, `downscalevid`, `optimiselib` |
| `HandBrakeCLI` | `compressvid`, `optimiselib` |
| `magick` (ImageMagick 7) | `convertimg` |
| `7z` (7-Zip) | `cybermod` |
| `addsub` (on PATH) | `autosub`, `optimiselib` (optional — enables subtitle merging) |

### macOS

```bash
brew install pipx
pipx ensurepath
```

```bash
git clone git@github.com:shubhs-dev/toolbox.git
cd toolbox
pipx install -e .
```

### Windows (Git Bash)

```bash
python -m pip install --user pipx
python -m pipx ensurepath
```

Restart your shell, then:

```bash
git clone git@github.com:shubhs-dev/toolbox.git
cd toolbox
pipx install -e .
```

This gives you real commands on your PATH — identically on Windows Git Bash and macOS. No
symlinking, no manual `PATH` exports.

### Upgrading

Since the install is editable (`-e`), pulling new commits (`git pull`) is enough — changes
under `src/toolboxcli/` take effect immediately. If `pyproject.toml` itself changes (new
dependency, new script), re-run:

```bash
pipx install -e . --force
```

### Uninstall

```bash
pipx uninstall shubhs-toolbox
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
PCM + DTS + AAC source. These presets are shared with `optimiselib`.

---

### concatvid

Concatenate split video parts in a folder using **ffmpeg**'s concat demuxer with
`-c copy` — no re-encoding, unless the parts don't match each other.

```bash
concatvid                     # scan cwd, concat groups after confirmation
concatvid ~/Videos/raw
concatvid -y ~/Videos/raw     # skip confirmation prompts
concatvid -n ~/Videos/raw     # preview groups without concatenating
concatvid -g cpu ~/Videos/raw # force software encoding when re-encoding
```

| Flag | Description |
|------|-------------|
| `-y, --yes` | Skip confirmation prompts |
| `-n, --dry-run` | Preview groups without concatenating |
| `-g, --gpu` | Encoder to use when parts need re-encoding: `auto` (default), `nvidia`, `amd`, `apple`, `cpu`. Env: `CONCATVID_GPU` |

Groups files by a common base name with a trailing sequence marker — `Movie 1.mp4`/
`Movie 2.mp4`, `Movie_pt1.mkv`/`Movie_pt2.mkv`, `Movie (01).mp4`/`Movie (02).mp4`, etc. — and
concatenates each group in sequence order. Files with no marker, or whose marker has no
siblings sharing the same base name and extension, are left untouched. The output is named
after the shared base name (falling back to `<base>.concat<ext>` on a collision); original
part files are trashed after a successful concat.

Before concatenating, each group is checked via **ffprobe** for stream-copy compatibility —
resolution, video codec, pixel format, audio codec, sample rate and channel count. Parts that
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
convertimg -k png
convertimg -n avif
```

| Flag | Description |
|------|-------------|
| `<format>` | Target format extension (e.g. `jpg`, `png`, `webp`, `avif`, `tiff`) |
| `-q, --quality N` | Compression quality 1-100 (default: `85`; only for lossy formats) |
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
```

| Flag | Description |
|------|-------------|
| `-s, --start TIME` | Start time (default: beginning). Accepts `HH:MM:SS`, `MM:SS`, or seconds |
| `-e, --end TIME` | End time (default: end of file). Accepts `HH:MM:SS`, `MM:SS`, or seconds |
| `-d, --duration DUR` | Duration of the cut instead of end time |
| `-r, --reencode` | Re-encode output (slower, frame-accurate). Default: stream-copy |

Stream-copy mode (default) is near-instant but cuts on keyframes, so the actual start may be
slightly before the requested time. Use `-r`/`--reencode` for frame-accurate cuts. Output
filename is auto-derived as `<stem>.cut.<start>-<end>.<ext>` if not given.

---

### flipvid

Flip a video horizontally or vertically using **ffmpeg**.

```bash
flipvid -x movie.mp4                 # flip horizontally (mirror)
flipvid -y movie.mp4                 # flip vertically (upside down)
flipvid -x -y movie.mp4 out.mp4      # flip both axes
flipvid -x -q 20 movie.mp4           # custom encode quality
```

| Flag | Description |
|------|-------------|
| `-x, --horizontal` | Flip horizontally (mirror left-right) |
| `-y, --vertical` | Flip vertically (upside down) |
| `-q, --quality CRF` | x264 CRF quality, lower is better (default: `18`) |

Flipping re-encodes the video stream (filters can't stream-copy); audio is always
stream-copied unchanged. Output filename is auto-derived as `<stem>.flip.<h|v|hv>.<ext>` if
not given.

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
```

| Flag | Description |
|------|-------------|
| `-r, --resolution RES` | Target resolution: preset (`8k`, `4k`/`2160p`, `1440p`, `1080p`, `720p`, `480p`, `360p`), a height, or `WIDTHxHEIGHT` |
| `-c, --codec {h264,h265}` | Video codec to re-encode with (default: `h264`) |
| `-g, --gpu {auto,amd,apple,nvidia,cpu}` | Which encoder to use (default: `auto`). Env: `DOWNSCALEVID_GPU` |
| `-f, --force` | Allow upscaling (default: refuse and skip) |

Re-encodes the video stream (resolution changes can't stream-copy); audio is always
stream-copied unchanged. Output filename is auto-derived as `<stem>.<resolution>.<ext>` if
not given.

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

### optimiselib

Watch a library root, optimise new videos to 1080p, tag them with their resolution and sort
them into trip folders. Built to run continuously on a media server — it's `compressvid` and
`sortmedia` welded into one unattended pipeline.

```bash
optimiselib /srv/library              # watch, poll every 30s
optimiselib -O                        # process what's there, then exit
optimiselib -n                        # dry run: show what would happen
optimiselib -r                        # report sub-1080p files and the review queue
optimiselib -H 01:00-07:00 -L 4       # only encode off-hours, and back off under load
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
3. **Deduplicate** — a higher-resolution or more complete copy replaces the one already in the
   library (the old one goes to the Recycle Bin). Anything ambiguous — higher res but shorter,
   longer but lower res — is parked in `_review/` rather than decided unattended.
4. **Sort** — moved into the folder named by the **Trip** field, the last `' - '`-separated part
   of the name. A name without all three fields is tagged, left in root and reported.

Subfolders are never scanned, so trip folders and any folder you use to stage files by hand are
left completely alone.

**Subtitles** — on every poll, optimiselib also looks for subtitle files sitting in the root
and matches each one to a video by the first `' - '`-separated field of its name (e.g. `People`
in `People - Title - Trip`). Only an unambiguous 1:1 match is merged — a subtitle matching more
than one video, or a video claimed by more than one subtitle, is left alone and retried next
poll rather than guessed at. A match is merged in with `addsub` and tagged `[... Sub]` in the
bracket group instead of getting `addsub -u`'s `" - Sub"` filename suffix. This works whether
the subtitle is already there when the video is first processed, or dropped in later for a
video that's already been optimised and filed into a trip folder — the video is found wherever
it currently lives. Requires the `addsub` command; if it's missing, subtitle merging is silently
disabled rather than blocking the rest of the pipeline. Disable it with `-S/--no-subs`.

| Flag | Default | Purpose |
|------|---------|---------|
| `-t, --interval` | `30` | Seconds between scans |
| `-O, --once` | off | Process existing files and exit |
| `-n, --dry-run` | off | Show planned rename, destination and duplicate verdict; change nothing |
| `-r, --report` | off | List files below 1080p and the review queue, then exit |
| `-F, --force` | off | Re-process files the log already marked done |
| `-g, --gpu` | `auto` | `auto`/`nvidia`/`amd`/`apple`/`intel`/`cpu`; env `OPTIMISELIB_GPU` |
| `-Q, --quality` | preset's | Override the preset's quality value |
| `-B, --bit-depth` | `auto` | `auto` matches the source (10-bit stays 10-bit, 8-bit isn't inflated); `8`/`10` force it |
| `-H, --hours` | none | Only start encodes inside e.g. `01:00-07:00` |
| `-L, --max-load` | none | Skip a scan while the 1-min load average exceeds this (POSIX) |
| `-d, --on-duplicate` | `auto` | `auto` (replace on a clear win, park anything ambiguous) \| `review` (park every duplicate) \| `ignore` (file both copies) |
| `-N, --no-nice` | off | Don't de-prioritise the encoder process |
| `-p, --preset-1080` | `hw-1080` | Preset for sources above 720p |
| `-q, --preset-720` | `hw-720` | Preset for sources at 720p or below |
| `-S, --no-subs` | off | Don't scan for or merge matching subtitle files |

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
`-H`/`-L` keep it out of the way of live playback. State lives in `.optimiselib.json` at the
library root, and a run killed mid-encode restores the source file on restart.

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
