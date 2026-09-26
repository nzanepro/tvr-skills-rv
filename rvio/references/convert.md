# Converting with rvio: sources, frames, size, audio

Read this for any conversion job: how to name inputs and outputs, pick frames, change fps,
resize or crop, join or renumber clips, add or extract audio, render an `.rv` session, or use
generated test patterns.

Everything here was run against OpenRV 3.1 unless marked *(docs)*. Command lines are shown for
a posix shell; in PowerShell quote the same arguments with single quotes.

## Command shape

```text
rvio [global options] SOURCE [SOURCE ...] -o OUTPUT [output options]
```

Option order is free except for three rules:

- Per-source options go inside `[ ... ]` together with their source, with a space on each side
  of each bracket: `[ -rs 1 plate.#.exr ]`.
- `-leader`, `-overlay`, `-outparams`, `-inparams` and `-flags` take every following word until
  the next word that starts with `-`.
- `-o` takes exactly one output.

## Sequence notation

| Notation | Meaning |
|---|---|
| `name.#.exr` | all frames, 4-digit padding |
| `name.####.exr` | all frames, 4-digit padding (Nuke style; `-ns` is no longer needed) |
| `name.@@@.exr` | all frames, one `@` per digit (3-digit padding) |
| `name.@.exr` | no padding (`name.7.exr`, `name.12.exr`) |
| `name.%04d.exr` | printf style; `name.%04d.exr 1-100` adds a range |
| `name.1001-1100#.exr` | explicit range |
| `name.1-5,8-10#.exr` | list of ranges (rvls prints sequences this way) |
| `name.-100--200#.exr` | negative frames |
| `folder/` or `.` | every sequence and file in the folder |
| `name.#.%V.exr` / `%v` | stereo pair: `%V` becomes left / right, `%v` becomes L / R |

- `name.*.exr` is documented but did not work on Windows (rvio wrote a placeholder); use `#`.
- Output names use the same notation: `-o out.#.exr`, `-o out.@@@@@@.exr` (6 digits),
  `-o out.%06d.exr`. Without notation, an image output receives only one frame.
- Frame numbers carry over from the source. Movies count from 1.

## Stills, sequences and movies

```bash
rvio in.tif -o out.jpg                                   # still to still
rvio "in.#.tif" -o "out.#.jpg"                           # sequence to sequence
rvio "in.#.png" -o out.mov                               # sequence to movie (MJPEG, 24 fps)
rvio in.mov -o "out.#.png"                               # movie to sequence, frames 1..N
rvio in.mov -t 5-10 -o "clip.#.jpg"                      # part of a movie (movie frames count from 1)
rvio "in.#.png" -t 1010 -o frame1010.tif                 # one frame out of a sequence
rvio in.mov -o out.wav                                   # rip a movie's audio
rvio "in.#.exr" -o out.null                              # decode only: throughput test (-v shows speed)
```

Movie containers that write: `.mov`, `.mp4`, `.m4v`, `.avi`, `.mkv`, `.mxf`, `.mpg`, `.flv`
(and `.gif`). Pick the codec with `-codec`; see `codecs-formats.md`.

## Frames and time

| Flag | Effect | Verified result |
|---|---|---|
| `-t 1001-1100` | output range, in source frame numbers | movie outputs count from 1 again |
| `-t 1050` | a single frame | |
| `-tio` | range from the in / out points stored in an `.rv` session | *(docs)* |
| `-fps 25` | input rate (global, or per source inside `[ ]`) | movie tagged 25 fps, 24 frames |
| `-outfps 30` | output rate tag; frames are not dropped or repeated | 24 frames at 30 fps |
| `[ -rs 1001 in.mov ]` | renumber a source to start at 1001 | movie frames 1..24 became 1001..1024 |
| `[ -ro -1000 in.#.png ]` | shift frame numbers by an offset | 1001..1024 became 1..24 |
| `[ -in 1010 -out 1050 in.#.exr ]` | cut a source | |

A gap in an input sequence is filled by holding the previous frame, with no warning. Check
inputs with `rvls` first when gaps matter.

## Joining clips

```bash
rvio "a.1001-1005#.png" "b.1010-1012#.png" c.mov -o joined.mov   # plays back to back
```

When the clips differ in size, set the output size explicitly with `-outres W H`.

## Size and geometry

| Flag | Effect | Verified with a 320 x 180 source |
|---|---|---|
| `-resize 160 0` | exact size; 0 keeps the aspect on that side | 160 x 90 |
| `-resize 640 480` | stretch to exactly this size | 640 x 480 |
| `-scale 0.25` | scale factor | 80 x 45 |
| `-outres 640 360` | conform: fit inside and pad; accepts arithmetic such as `1556/2` | 640 x 360 |
| `[ -crop 0 0 159 89 in ]` | crop to an inclusive pixel box x0 y0 x1 y1 | 160 x 90 |
| `[ -uncrop 320 240 0 30 in ]` | place the image on a larger canvas at x, y (letterbox) | 320 x 240 |
| `[ -pa 2.0 in ]` | treat the source as anamorphic (pixel aspect 2) | |
| `-outpa 2.0` | write pixel-aspect metadata only | `PixelAspectRatio 2` in the movie |
| `-flip`, `-flop` | vertical, horizontal mirror | |

## Audio

```bash
rvio [ "shot.#.exr" dialog.wav ] -o shot.mov                              # pcm_s16be by default
rvio [ "shot.#.exr" dialog.wav ] -audiocodec pcm_s24le -o shot.mov        # 24-bit PCM
rvio [ "shot.#.exr" dialog.wav -ao 0.5 ] -audiorate 44100 -audiochannels 1 -o shot.mov
rvio [ -noMovieAudio shot.mov ] -o silent.mov                             # drop a movie's own sound
```

- `-ao` shifts the audio in seconds; `-volume` scales it.
- AAC failed with a muxer timestamp error in OpenRV 3.1 (.mov and .mp4); use PCM.
- An `.mp4` with MPEG-4 video accepted `-audiocodec pcm_s16le`.

## Session files and test patterns

- `rvio session.rv -o out.mov` renders an RV session (sources, edits, colour settings stored
  in it). `-view NAME` picks a view other than the default sequence; `-tio` uses its in / out
  points.
- `-o session.rv` writes a session file instead of pixels (leaders and overlays are ignored).
  `gtoinfo session.rv` prints its structure.
- Generated sources need no files, which makes them useful for tests:
  `smptebars,start=1,end=24,fps=24,width=1920,height=1080.movieproc`,
  `solid,red=1,green=0,blue=0,start=1,end=10,width=64,height=64.movieproc`.

## Speed and diagnostics

- `-v` prints progress per frame; `-vv` is very verbose.
- `-rthreads N` reads and renders with N threads; `-wthreads N` for writers that support it;
  `-exrcpus N` sets EXR decode threads.
- A 24-frame 320 x 180 job took 1.1 to 1.4 s on a desktop GPU, mostly start-up time, so batch
  many sources into one run where possible.
- `-err-to-out` sends errors to stdout; `-debug CATEGORY` enables debug output.
- `-init script.mu` or the `RVIO_INIT` variable replaces the (empty) start-up script;
  `RVIO_OUTPARAMS` supplies default `-outparams`.
