---
name: rvls
description: "Lists and inspects image sequences and movies with RV / OpenRV's rvls command-line tool: sequences collapsed to frame ranges, missing frames and gaps, resolution, bit depth, channels, fps, codec, timecode, audio, and full file headers and metadata for EXR, DPX, TIFF, MOV and MP4. Use to see what is on disk or to check that a render, copy or conversion wrote the expected frames. Not for converting media (rvio), RV packages (rvpkg), or viewing in RV (rv-review)."
license: MIT
compatibility: Needs RV or OpenRV (rvls) and Python 3.9 or later for the helper scripts (standard library only); rvls_check.py --parse also works on saved rvls output without RV. Desktop only (Claude Code CLI, desktop app or IDE extension on Windows, macOS or Linux); not claude.ai in a browser or the iOS / Android apps, which cannot run RV on your machine.
metadata:
  version: 0.2.0
---

# List and inspect sequences with rvls

rvls is RV's `ls` for media: it collapses numbered files into sequences with frame ranges and
reads each sequence's or movie's header, using the same readers RV plays with. It opens no
window and is fast (a fraction of a second per folder).

## Find the tool

```bash
python scripts/rv_find.py --path rvls    # or without --path for every RV tool as JSON
```

Search order: `--rv-bin`, `rv_bin` in `~/.config/tvr-skills-rv/config.json`, `PATH`, the
Windows registry, the usual install folders, then an OpenRV built from source (`~/OpenRV`,
or the checkout you are in). No shell variable is read. If nothing is found, ask the
user where RV is installed and pass `--rv-bin`.

Plugin setting "RV bin folder": [${user_config.rv_bin}]. When the text between those
brackets is a folder path, pass it as `--rv-bin "<that path>"` to `scripts/rv_find.py`,
`scripts/rv_tool.py` and `scripts/rvls_check.py`, placed before the tool name or before
`--push`, where all other options of a script go (anything after `--push`, or after the tool
name in `rv_tool.py`, is sent on as an argument). When the brackets are empty or the text
still starts with a dollar sign, the setting is unset or the skill was installed on its own:
pass nothing for it, and never pass that text as a path.

## Scripts

Paths are relative to this skill's folder; run each with `--help` first.

| Script | Use |
|---|---|
| `scripts/rvls_check.py PATH... [--expect-range A-B] [--no-gaps] [--expect-res WxH] [--expect-count N] [--expect-channels N] [--expect-type 16f] [--readable]` | runs `rvls -l` and prints JSON per sequence or file: first / last frame, count, missing frames, padding, size, pixel type, channels, fps, audio; exit 1 when an expectation fails. Accepts folders, files and sequence specs (`shot.#.exr`) |
| `scripts/rv_tool.py rvls -- ARGS` | runs rvls with an argument list and a timeout |
| `scripts/rv_find.py` | finds rv, rvio, rvls, rvpkg, rvpush |

## Tasks

| Task | Command |
|---|---|
| What sequences and movies are in a folder | `rvls DIR` (add `-s` to hide single files) |
| Size, pixel type, channels, fps, frame count, audio channels | `rvls -l DIR` |
| Every header attribute of a file (codec, pixel format, timecode, EXR compression, DPX bit size, colour tags) | `rvls -x FILE` (add `-yaml` for YAML) |
| Missing frames, exact frame count, first / last frame | `python scripts/rvls_check.py DIR` or `SPEC` |
| Check a render or conversion is complete | `python scripts/rvls_check.py "out/shot.#.exr" --expect-range 1001-1100 --no-gaps --expect-res 1920x1080` |
| One line per file, no sequences | `rvls -ns DIR` |
| Sequence names without ranges | `rvls -nr DIR` |
| Include hidden files; treat 2 files as a sequence | `-a`; `-min 2` |
| Formats this RV can read | `rvls -formats` |

## Reading rvls output

- Sequences print as prefix + frame ranges + padding + suffix: `plate.1-5,8-10#.exr` means
  frames 1-5 and 8-10 with 4-digit padding (`#` = 4 digits, `@@@` = 3 digits, `@` = none),
  `s.1-9x2#.exr` means every second frame, `neg.-2-1@@@.exr` means frames -2 to 1.
- `rvls -l` columns: `w x h`, `typ` (`8i`, `16i`, `16f`, `32f`), `#ch`, `fps` (0 for stills),
  `#fr`, `#ach` (audio channels, only when some file has audio), `file`.
- `#fr` is the span from first to last frame, not the number of files: the sequence above
  shows 10 for 8 files. `rvls_check.py` reports both (`span`, `count`) and the `missing` list.
- A row `0 x 0  11  0` means rvls could not read the file (damaged, truncated, or not media).

## Verify with rvls

rvls exits 0 even when a path does not exist or a file cannot be read, and prints `ERROR:`
lines for every non-image file in a folder. So:

1. Prefer `rvls_check.py` for pass / fail checks; it exits 1 on a missing path, a missing
   frame (`--no-gaps`), a wrong range, size, channel count or pixel type, or an unreadable
   file (`--readable`).
2. For a movie, check `Duration`, `FPS`, `VideoCodec`, `VideoPixelFormat` and, when relevant,
   `Timecode/Start`, `Audio` and `AudioCodec` in `rvls -x`.
3. For DPX, `rvls -l` shows `8i` even for 10-bit files; read `DPX/BitSize` in `rvls -x`.

## Gotchas

- rvls takes folders and files, not sequence specs: `rvls "shot.#.exr"` prints nothing. Pass
  the folder (or let `rvls_check.py` do it for you).
- Two numbered files are not a sequence by default (`-min 3`); use `-min 2`.
- Files with different padding (`t.0004.png`, `t.00005.png`) are merged into one sequence.
- Movie rows count the frames inside the movie; `rvls_check.py` sets `count` to that number.
- `-yaml` only works together with `-x`.
- Converting, re-encoding or adding slates is the rvio skill; opening media to look at it is
  the rv-review skill.

## Read when

| Read | When |
|---|---|
| `references/rvls-output.md` | all options, output formats, header keys worth checking per format |
| `references/checking-outputs.md` | recipes for checking renders, copies and rvio conversions, and scripting against rvls |
