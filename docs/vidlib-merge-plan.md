# vidlib: merging parts — plan

Videos that come in parts (`Part 1`, `P2`, `Ep3`, `Part 1-5`, …) can be found and joined
into one from the web page. This file records the decisions from the grilling and the
stages; it's deleted once the work is done, with what lasts moved into CLAUDE.md.

## Decisions

### What's merged
- Library videos (already in the database: sorted, badly named, anywhere but `_review/`).
  Parts arriving as downloads may come later as an option.
- Suggestions you confirm, plus a manual "merge these". Nothing merges by itself.

### Finding parts (loose on purpose)
- People and collection don't have to match: crossovers and half-credited parts exist.
  Grouping is by the **title**; a badly named file's whole name is its title.
- A part marker, found at the start or end of the title or as a segment of its own (never
  mid-sentence):
  - keywords `Part`, `Pt`, `P`, `Ep`, `Episode`, `Chapter`, `Ch`, `Vol`, `CD`, `Disc`,
    `No.`, `#`, with or without a space or dot (`P1`, `Part1`, `Ep.1`, `#1`);
  - after a keyword, Roman numerals (`Part II`) and number words (`Part Two`) too;
  - `N/M`, `N of M`; ranges (`Part 1-5` covers 1–5, so `Part 6-10` follows);
  - a bare trailing number only when two or more videos share the rest of the title and
    the numbers run in sequence (else "Marketing Monday 112" would be a part).
- Titles with the marker removed are compared:
  - **sure** — equal after `naming.loose_key` (case, punctuation, spaces, accents);
  - **maybe** — most words shared (≥ 80% overlap); shown separately, marked as maybes.
- The merge screen lists candidates with checkboxes (untick what doesn't belong) and a
  search to add a missed part.
- **Not parts** remembers a dismissed set in the database; it returns only if a new video
  joins it.
- Gaps (1, 3, 4) are suggested but flagged, and need an extra confirmation. A doubled part
  number blocks the set until the duplicate is resolved.

### The merged video
- People: everyone named in any part. Collection: the one most parts share (Part 1's on a
  tie). Title: the shared title without the marker. All editable, with the same pills.
- Ordered by part number; drag to reorder.
- Parts that don't match (resolution, codec) are allowed: the screen says what will be
  re-encoded or downscaled first.

### Running a merge
- Queued with the pipeline: one heavy job at a time; Pause / Stop now / `-H` apply; shown
  in the Encoding panel.
- Joined by the existing `concatvid` command (as vidlib runs `addsub`): its compatibility
  check, re-encoding only mismatched parts, the length check, and trashing the parts.
  Progress is a step name, not a percentage.
- Output goes to `_incoming/merge-<n>/` (never scanned), and moves into the root only once
  `concatvid` has trashed the parts (its length check passed). If it didn't, the parts stay
  and the page offers to discard the unverified join.
- In the root the pipeline tags, de-duplicates and files it: sorted only when every part
  was encoded by vidlib (`backend` set), encoded like a new arrival otherwise. The parts'
  rows are removed.

## Stages
1. **Finding parts.** `vidlib/parts.py` (pure, strict) — marker parsing and grouping — with
   a fixture of real-world titles; a dismissals table (schema 4); a *Parts* view in the
   Library tab listing sure and maybe groups, with Not parts.
2. **Merging.** The merge screen (checkboxes, order, name, what will be re-encoded), the
   queued merge job running `concatvid`, the staging/root hand-off and the database.
3. **By hand.** Select videos in the Library grid → Merge selected.
