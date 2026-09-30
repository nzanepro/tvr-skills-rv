---
name: rvio
description: "Converts and transcodes media with RV / OpenRV's rvio command-line tool: image sequence to movie and back, EXR / OpenEXR, DPX, TIFF, PNG and JPEG, MOV / MP4 (MJPEG, MPEG-4; ProRes or DNxHD only in builds that include them), resize, crop, frame ranges, fps, audio, colour (sRGB, log, Rec.709, ACES, LUTs, baked OCIO), slates, frame burn-ins, watermarks and mattes. Use when media must be written or re-encoded. Not for listing sequences (rvls), RV packages (rvpkg), or viewing in RV (rv-review)."
license: MIT
compatibility: Needs RV or OpenRV (rvio) and Python 3.9 or later for the helper scripts (standard library only). OpenRV's rvio renders through OpenGL, so it needs a desktop session or a virtual display (Linux installs may also ship rvio_sw). Desktop only (Claude Code CLI, desktop app or IDE extension on Windows, macOS or Linux); not claude.ai in a browser or the iOS / Android apps, which cannot run RV on your machine.
metadata:
  version: 0.2.0
---

# Convert and transcode media with rvio

rvio is RV's batch converter. It reads anything RV can play (image sequences, stills, movies,
`.rv` sessions, generated test patterns) and writes image sequences, stills, movies or audio,
applying colour transforms, resizing, slates and overlays on the way. It never opens a window.

## Find the tool

```bash
python scripts/rv_find.py              # JSON: bin folder, every tool's path, rvio/rvls versions
python scripts/rv_find.py --path rvio  # just the path
```

Search order: `--rv-bin`, `rv_bin` in `~/.config/tvr-skills-rv/config.json`, `PATH`, the
Windows registry, the usual install folders (Program Files OpenRV / Autodesk / ShotGrid,
`/Applications/*RV*.app`, `/opt/rv*`, `/usr/local/rv*`), then an OpenRV built from source
(`~/OpenRV`, or the checkout you are in: `_build/stage/app`). No shell variable is
read. If nothing is found, ask the user where RV is installed and pass `--rv-bin`.

Plugin setting "RV bin folder": [${user_config.rv_bin}]. When the text between those
brackets is a folder path, pass it to the scripts as `--rv-bin "<that path>"`. When it still
starts with a dollar sign, the setting is empty or the skill was installed on its own: pass
nothing for it, and never pass that text as a path.

## Scripts

Paths are relative to this skill's folder. Run each with `--help` first and treat it as a
black box.

| Script | Use |
|---|---|
| `scripts/rvio_cmd.py IN... -o OUT [options] [--run]` | builds the rvio argument list for common jobs, checks inputs, output naming and codecs first, prints paste-ready posix / PowerShell / cmd lines, and with `--run` runs it and counts the files written |
| `scripts/rv_tool.py rvio -- ARGS` | runs rvio with an argument list, closed stdin and a timeout; exit 3 when rvio printed `ERROR:` but exited 0 |
| `scripts/rvio_codecs.py` | encodes two frames of colour bars per codec to list the movie codecs this build can really write (about 20 s) |
| `scripts/rv_find.py` | finds rv, rvio, rvls, rvpkg, rvpush |

## Tasks

| Task | Key flags | Read |
|---|---|---|
| Sequence to movie, movie to sequence, still to still | `IN -o OUT`, `-t 1001-1100`, `-fps`, `-outfps` | `references/convert.md` |
| Resize, conform, crop, pad, flip | `-resize W H` (0 keeps aspect), `-scale`, `-outres`, `[ -crop ... ]`, `[ -uncrop ... ]`, `-flip`, `-flop` | `references/convert.md` |
| Renumber, cut, join clips | `[ -rs 1 ... ]`, `[ -ro N ... ]`, `[ -in A -out B ... ]`, several inputs | `references/convert.md` |
| Pick codec, quality, bit depth, EXR / TIFF / DPX compression | `-codec`, `-quality`, `-outformat 16 float`, `-outparams` | `references/codecs-formats.md` |
| Audio in, audio out | `[ pics.#.exr sound.wav ]`, `-audiocodec`, `-audiorate`, `IN.mov -o OUT.wav` | `references/convert.md` |
| Linear / sRGB / log / Rec.709 / ACES, gamma, exposure, LUTs, OCIO | `-insrgb`, `-inlog`, `-outsrgb`, `-outlog`, `-flut`, `-dlut`, `-outaces` | `references/colour.md` |
| Slate, frame burn-in, watermark, matte, logo bug | `-leader simpleslate ...`, `-overlay frameburn ...` | `references/slates-overlays.md` |
| EXR headers, multi-view / stereo, channels and layers | `-outparams name:s=value`, `-outstereo`, `[ -select ... ]`, `-outchannelmap` | `references/exr-stereo-channels.md` |
| See what really worked on a tested build | exact commands, timings, codec probe results | `references/verified-commands.md` |

## Common commands

```bash
rvio "plate.#.exr" -outsrgb -codec mjpeg -quality 0.9 -o plate.mov          # linear EXR to review movie
rvio shot.mov -o "frames/shot.#.png"                                         # movie to PNG frames (numbered from 1)
rvio "shot.#.png" -insrgb -outhalf -codec DWAA -quality 45 -o "shot.#.exr"   # PNG to half-float DWAA EXR
rvio "plate.#.dpx" -resize 1920 0 -o "proxy/plate.#.jpg"                     # proxies, aspect kept
rvio "shot.#.exr" -outsrgb -o review.mov -leader simpleslate "Studio" "Shot=sh010" "Version=v001" \
     -overlay frameburn .4 1.0 30.0                                          # slate + frame numbers
rvio [ "shot.#.exr" dialog.wav ] -outsrgb -o shot.mov                        # picture with sound
```

Quote anything with `#`, `@`, spaces or brackets for your shell, and put a space on each side
of `[` and `]`. `rvio_cmd.py` produces correctly quoted lines for posix shells, PowerShell and
cmd.

## Verify every output

rvio can exit 0 after a failure, so never trust the exit code alone.

1. Scan rvio's output for `ERROR:` lines (`rv_tool.py` and `rvio_cmd.py --run` do this).
2. Count frames and check size and type. With the rvls skill:
   `python ../rvls/scripts/rvls_check.py "out/shot.#.exr" --expect-range 1001-1100 --no-gaps
   --expect-res 1920x1080`. Without it: `rvls -l out/` (sequence, size, pixel type, channels,
   fps, frame count) and `rvls -x out/shot.mov` (codec, pixel format, timecode, audio).
3. Look at one frame: `rvio out.mov -t 12 -o check.png`, then read the PNG (or open it in
   RV with the rv-review skill). Compare pixel values against the source when colour matters.

## Gotchas

- **A missing input still "succeeds".** rvio prints `ERROR: Open of '...' failed`, exits 0 and
  writes a 1280 x 720 placeholder movie. `*` wildcards are not expanded by rvio (Windows has
  no shell to do it), which gives the same result; use `name.#.ext` or a folder.
- **Gaps are filled silently.** Missing frames in the input are held from the previous frame.
  Check the source with rvls before converting when gaps matter.
- **Image output needs frame notation.** `-o out.png` for a many-frame input overwrites one
  file; use `out.#.png`. rvio does not create output folders (it prints an error per frame).
- **Codecs depend on the build.** Stock OpenRV writes MJPEG (the default), MPEG-4 part 2 and
  PNG movies, audio as PCM. There is no H.264, HEVC, VP9, AV1 or AAC. ProRes, DNxHD and MPEG-2
  exist only in builds compiled with those encoders; run `rvio_codecs.py`. Autodesk RV adds
  H.264, ProRes, DNxHD and AAC. For H.264 delivery, write MJPEG or an image sequence and
  encode with ffmpeg.
- **`-quality` only affects MJPEG movies** (and is the DWA level for DWAA / DWAB EXR). Set other
  codecs' rate with `-outparams vcc:b=8000000` or a profile such as `vc:profile=hq`.
- **`-formats` lists only a few encoders.** Trust `rvio_codecs.py` instead.
- **Colour is not guessed.** Without `-insrgb` / `-inlog` the pixels are used as they are;
  going from linear EXR to an 8-bit movie needs `-outsrgb` (or `-out709` / a LUT).
- **PNG and TIFF output gain an alpha channel**; add `-outrgb` for RGB. TIFF without
  `-outformat` is written as 32-bit float; say `-outformat 8 int` or `16 int`.
- **Movies number frames from 1**; image sequences keep the source numbers. Use `[ -rs 1001 ... ]`
  to renumber.
- **`-leader` and `-overlay` take every following word until the next `-option`**, so values
  must not start with `-`. The bug overlay only reads TIFF logos; a PNG is silently skipped.
- rvio overwrites existing outputs without asking.

## Read when

| Read | When |
|---|---|
| `references/convert.md` | sequences, ranges, fps, resize / crop, joining, renumbering, audio, `.rv` input, test patterns |
| `references/codecs-formats.md` | choosing a container, codec, bit depth or compression; `-outparams`; OpenRV vs Autodesk RV |
| `references/colour.md` | any colour space, LUT, OCIO, ACES or gamma question |
| `references/slates-overlays.md` | slates, burn-ins, watermarks, mattes, logos, custom overlay scripts |
| `references/exr-stereo-channels.md` | EXR metadata, multi-view / stereo, layers and channel maps |
| `references/verified-commands.md` | exact commands, timings and codec results from a real build |
