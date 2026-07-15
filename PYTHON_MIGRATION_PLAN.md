# Plan: Standardize all scripts to Python (NOT YET ACTIONED)

Status: approved plan, deferred — not yet implemented. Read this file in full
before starting work on this task.

## TL;DR
Convert all 11 remaining Bash scripts (addsub, autosub, convertimg, cutvid, cybermod,
finddupes, flatten, jellyname, kavitaname, mergemanga, toolbox) to Python 3, matching
the style already established by compressvid/sortmedia/x265ify. Extract common
helpers (logging, trash, confirm prompts, human_size) into a new shared module
`_common.py`. Keep external CLI tool dependencies unchanged (ffmpeg, magick, 7z,
HandBrakeCLI) — only the orchestration language changes. Update README.md and
copilot-instructions.md as part of the same effort. Convert and review in small
phases, not all at once.

## Decisions
- Scope: all 11 Bash scripts converted. Final state: 0 Bash scripts in repo.
- Shared module: YES — new `_common.py` at repo root (leading underscore = not a
  CLI entry point). Not added to `toolbox`'s SCRIPTS registry, not part of the
  "chmod +x" list. Each script resolves it via
  `sys.path.insert(0, str(Path(__file__).resolve().parent)); import _common`
  (same `Path(__file__).resolve().parent` pattern already used by compressvid/
  mergemanga for SCRIPT_DIR — resolves correctly even if the script is symlinked
  elsewhere per README Option 2 install).
- rich: used everywhere for console output (Console, tables, progress/spinner),
  matching compressvid/x265ify.
- Fidelity: open to small improvements while porting (not required to be
  byte-for-byte identical), but keep flags/behavior recognizable.
- External tools: keep shelling out to ffmpeg/ffprobe, magick, 7z, HandBrakeCLI
  via subprocess — do NOT switch to native Python libraries (no Pillow/py7zr/etc).
  Only the glue/orchestration script changes from Bash to Python.
- addsub `--hard` flag: drop the `-h` short form (conflicts with argparse's
  reserved `-h/--help`). Use `-H/--hard` instead. `-h` is help only, everywhere,
  for consistency with argparse defaults across all scripts.
- Add a repo-root `requirements.txt` containing `rich` (required) and a comment
  noting `send2trash` as optional.
- Workflow: implement and present ONE PHASE (small group of scripts) at a time
  for review, not all 11 in one shot.

## Shared module: `_common.py`
Consolidate logic already duplicated across Bash scripts and existing Python
scripts (x265ify's `move_to_trash`/`fmt_size`, sortmedia's `getkey`):
- `console` — a shared `rich.console.Console()` instance.
- `log_info/log_ok/log_warn/log_err(msg)` — rich-colored prefixes matching the
  existing Bash convention (cyan `[INFO]`, green `[OK]`, yellow `[WARN]`,
  red `[ERROR]`); `log_warn`/`log_err` go to stderr.
- `die(msg)` — `log_err` + `sys.exit(1)`.
- `require(cmd)` — `shutil.which` check + `die` if missing (replaces Bash's
  `require()`).
- `move_to_trash(path)` — port verbatim from x265ify's `move_to_trash`
  (send2trash → Windows PowerShell/VisualBasic → Linux gio/trash-put → macOS
  osascript → RuntimeError). This becomes the single source of truth; x265ify/
  compressvid can optionally be left as-is (out of scope) or pointed at this too
  — default: leave compressvid/x265ify/sortmedia untouched, only new scripts use
  `_common`.
- `fmt_size(n)` — human-readable byte size, ported from x265ify.
- `confirm(prompt)` — single-keypress y/n prompt (bash `confirm()` equivalent),
  built on the cross-platform `getkey()` pattern from sortmedia (msvcrt on
  Windows, termios/tty otherwise), reading from stdin/tty.
- `confirm_replace(prompt)` — 3-way y/n/a(ll) prompt (flatten's
  `confirm_replace()`), returns a decision + whether "apply to all" was chosen.
- `SCRIPT_DIR` helper pattern documented (not a function — each script computes
  its own via `Path(__file__).resolve().parent`).

## Phases

### Phase 0 — Foundation (do first, blocks everything else)
1. Create `_common.py` per the spec above.
2. Create `requirements.txt` at repo root: `rich` plus a comment for optional
   `send2trash`.
3. Verify import pattern works when a script is symlinked outside the repo
   (README Option 2) — `Path(__file__).resolve()` follows symlinks to the real
   file, so `_common.py` next to the real script is always found.

### Phase 1 — Pure-Python, no external CLI deps
*(good pilot batch — validates `_common.py` design before touching ffmpeg/7z scripts)*
1. `toolbox` — Python list of `(name, description)` tuples for the registry;
   render with a `rich.table.Table` (bonus of "rich everywhere") instead of
   manual `printf` column-width math. Must include entries for all 14 scripts
   (13 existing + itself) and must NOT include `_common.py`.
2. `finddupes` — replace the `find | sort | uniq -c | awk` pipeline with pure
   Python: walk directory with `os.walk`/`pathlib`, group by basename (optionally
   lowercased for `-i/--ignore-case`), sort groups by count for `-c/--count`.
   Use a `rich` spinner/progress indicator instead of the braille spinner loop.
3. `flatten` — pathlib-based move with conflict detection; use `_common.fmt_size`
   and `_common.confirm_replace` for the 3-way prompt; remove empty dirs
   bottom-up via `pathlib`/`os.walk` after moving (topdown=False).

### Phase 2 — ffmpeg-based scripts *(depends on Phase 0)*
1. `cutvid` — port `to_seconds()` time parsing (regex + split-on-`:`), replicate
   `-ss`-before-`-i` vs re-encode logic and the `-t` vs `-to` duration handling
   exactly (this nuance is critical — re-verify against the cataloged behavior).
   Build ffmpeg args as a list for `subprocess.run` (avoids shell quoting bugs).
2. `convertimg` — keep shelling out to `magick`; port extension discovery
   (case-insensitive), already-in-target-format skip, dry-run preview, quality
   validation (1-100), partial-output cleanup on failure.
3. `addsub` — port soft-mux vs hard-burn logic, in-place/suffix/explicit output
   modes, subtitle codec selection by container, filtergraph path escaping.
   **Flag change:** drop `-h` short form for `--hard` (now `-H/--hard`); `-h`
   reserved for `--help` via argparse everywhere.
4. `autosub` — depends conceptually on `addsub` being converted first (it still
   invokes `addsub` as an external subprocess, not via import — this preserves
   the standalone-executable architecture). Port episode-code extraction
   (split on `" - "`), snapshot-before-processing behavior, subtitle matching.

### Phase 3 — 7z-based scripts *(depends on Phase 0)*
1. `cybermod` — port `find_mod_root` (0/1/2-level directory search for known
   mod dirs, `LOOSE_ARCHIVE:` sentinel behavior — represent as a proper Python
   return type instead of a string-prefix sentinel, e.g. a small dataclass or
   tuple `(kind, path)`), overwrite detection, two-stage confirm flow, GAME_DIR
   hardcoded constant retained at top with instructions to edit.
2. `mergemanga` — port `one-piece-volumes` parsing (unchanged file format),
   volume padding logic, chapter collection with missing-chapter warnings
   (silent skip if zero chapters, warn if partial), page extraction into
   zero-padded `ch_XXXX` subdirs, ComicInfo.xml generation (use
   `xml.etree.ElementTree` or an f-string template — keep exact same XML shape/
   fields), `7z` subprocess calls for extract and zip-store creation.

### Phase 4 — Renaming scripts *(depends on Phase 0)*
1. `jellyname` — port TV vs movie detection regex, `strip_junk` junk-tag regex
   list (directly usable in Python `re` with `re.IGNORECASE` — no perl/sed
   workaround needed), `normalize_name`/`sanitize_for_path`, resolution
   extraction/canonicalization (incl. `4K` → `2160p`), TV/movie output path
   construction.
2. `kavitaname` — port volume-map file parsing (pipe-delimited, `#`
   comments/blank lines skipped, CRLF stripping) into a `dict[int, int]`
   chapter→volume map, decimal chapter number handling (`Chapter 10.5` →
   `c010.5`), prologue special case (`c0.N`), manga-with-map / manga-without-map
   / manhwa output naming.

### Phase 5 — Documentation & cleanup *(after all scripts converted)*
1. `README.md`:
   - Update the `## Scripts` table's Language column to Python for all 11.
   - Update each script's usage section for any flag changes (currently only
     addsub's `--hard` short-form removal).
   - Add a short note near Installation/Dependencies that `_common.py` is an
     internal shared module, not a standalone script (skip when symlinking
     individual scripts per Option 2 — copy or symlink it alongside any script
     that needs it, or just keep the whole repo dir on PATH).
   - Add `requirements.txt` mention (`pip install -r requirements.txt`).
2. `copilot-instructions.md`:
   - Update "Scripts at a Glance" Language column.
   - Rewrite "Universal Rules" for Python-first (shebang `#!/usr/bin/env
     python3`, no extension + chmod +x, trash via `_common.move_to_trash()`,
     argparse-based CLI, rich Console for logging) — remove the now-obsolete
     Bash-only checklist (GNU vs BSD, `set -euo pipefail`, bash `trash_file()`),
     but note the underlying cross-platform concerns are now naturally handled
     by `pathlib`/`argparse`/`re`.
   - Remove/replace the "Bash Patterns" section entirely with a "Python
     Patterns" section describing `_common.py`'s contents and the argparse/
     rich conventions.
   - Update each "Script-Specific Conventions" entry (11 of them) to describe
     the new Python implementation details in place of the old bash-isms
     (perl -pe, sed, compgen, mapfile, awk, associative arrays).
   - Update "When Adding a New Script" checklist to be Python-only.
3. Verify `chmod +x` still applies to all 14 scripts (not `_common.py`).

## Relevant files
- `_common.py` — new shared module (Phase 0).
- `requirements.txt` — new (Phase 0).
- `toolbox`, `finddupes`, `flatten` — Phase 1 rewrites.
- `cutvid`, `convertimg`, `addsub`, `autosub` — Phase 2 rewrites.
- `cybermod`, `mergemanga` — Phase 3 rewrites (mergemanga reads `one-piece-volumes`, unchanged format).
- `jellyname`, `kavitaname` — Phase 4 rewrites.
- `README.md`, `.github/copilot-instructions.md` — Phase 5 doc updates.
- Reference/reuse (unchanged, existing Python style templates):
  `x265ify` (`move_to_trash`, `fmt_size`, rich UI),
  `sortmedia` (`getkey()`, MSYS path conversion),
  `compressvid` (SCRIPT_DIR pattern, argparse+rich style).

## Verification
1. Per script: `<script> --help` shows correct usage via argparse.
2. Per script: manual smoke test in a scratch directory with sample files
   (e.g. sample video for cutvid/addsub, sample images for convertimg, dummy
   nested dirs for flatten/finddupes, dummy `Chapter N.cbz` files for
   mergemanga/kavitaname, dummy archive for cybermod using `-n`/dry-run modes
   where available).
3. `toolbox` output lists all 14 scripts correctly, aligned.
4. Confirm `_common.py` import works both when running a script directly from
   the repo and via a symlink elsewhere (README Option 2).
5. Basic `python3 -m py_compile <script>` on each converted file to catch
   syntax errors before hand-off.
6. Re-verify README and copilot-instructions.md no longer reference Bash for
   any of the 11 converted scripts, and the "Scripts at a Glance" table and
   `SCRIPTS` array in `toolbox` are in sync with reality.

## Scope boundaries
- Included: all 11 Bash→Python conversions, `_common.py`, `requirements.txt`,
  README + copilot-instructions updates.
- Excluded: compressvid, sortmedia, x265ify internals are NOT refactored to use
  `_common.py` (left as-is, out of scope) unless asked separately.
- Excluded: no switch to native Python libraries for image/archive/video
  handling — external CLI tools (ffmpeg, magick, 7z, HandBrakeCLI) stay as
  subprocess dependencies.
- Excluded: not fixing the previously-noted compressvid preset default mismatch
  (hw-1080-preset vs hw-1080.json) — unrelated to this task.
