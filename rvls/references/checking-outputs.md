# Checking renders, copies and conversions with rvls

Read this when you must prove that a set of frames or a movie is complete and correct: after a
render, a copy or transfer, an rvio conversion, or before handing media on.

## The checks

| Question | How |
|---|---|
| Are all frames there? | `rvls_check.py "dir/shot.#.exr" --expect-range 1001-1100 --no-gaps` |
| Is the frame count right? | `--expect-count 100` (files on disk; for a movie, frames inside it) |
| Right size, channels, depth? | `--expect-res 1920x1080 --expect-channels 4 --expect-type 16f` |
| Can every file be read? | `--readable` (fails on `0 x 0` rows, e.g. truncated movies) |
| Right codec, fps, timecode, audio? | `rvls -x movie.mov` and compare the keys in `rvls-output.md` |
| Right EXR compression / attributes? | `rvls -x "dir/shot.1001.exr"` (one frame is enough when all were written by one job) |
| Same frames as the source? | run `rvls_check.py` on both and compare `first`, `last`, `count`, `missing` |

`rvls_check.py` prints one JSON object (`ok`, `problems`, `entries`) and exits 0 when every
expectation holds, 1 otherwise, so it can gate a script:

```bash
python scripts/rvls_check.py "renders/shot.#.exr" --expect-range 1001-1100 --no-gaps \
  --expect-res 1920x1080 || echo "render incomplete"
```

Several paths can be checked in one call; expectations apply to every entry, so check
different shots separately when their ranges differ.

## After an rvio conversion

rvio can exit 0 after writing a placeholder (missing input) or nothing at all (missing output
folder), and it fills input gaps by repeating frames. Check both ends:

1. Source: `rvls_check.py "in/plate.#.dpx" --no-gaps` before converting.
2. Output sequence: `--expect-range` with the source range (plus leader frames if a slate was
   added: one slate frame on 1001-1100 gives 1000-1100).
3. Output movie: `--expect-count` with the number of frames, and `rvls -x` for `VideoCodec`,
   `FPS` and `Duration`. A 1280 x 720 movie of 19 frames where you expected your own size is
   rvio's "missing media" placeholder.

## After a copy or transfer

Compare listings of the source and the copy: same entries, same `first` / `last` / `count`,
no `missing`, and every file readable (`--readable`). rvls reads one header per sequence for
`-l`, so a single truncated frame in the middle is not detected (a sequence with one
truncated EXR still listed as complete). When that matters, decode every frame:
`rvio "copy/shot.#.exr" -o check.null` reads all frames without writing anything; with a
truncated EXR in the sequence, rvio 3.1 crashed with a non-zero exit, so treat any non-zero
exit or `ERROR:` line as a bad copy.

## Scripting against rvls directly

- Use `rvls -l` for machine reading, skip lines that start with `INFO:`, and read the
  `file` column from the header's `file` position (names may contain spaces).
- Expand ranges yourself: split on `,`, each item is `a`, `a-b` or `a-bxN`; watch for
  negative numbers (`-2-1`, `-100--98`).
- `#fr` is a span, not a file count.
- `rvls_check.py --parse saved.txt` parses saved `rvls` or `rvls -l` output (from another
  machine, a log, or a CI artefact) without running RV.
