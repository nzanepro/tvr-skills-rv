# rvls options and output

Read this for the full option list, the exact shape of each output mode, and which header
keys to look at for each file type. Examples are real OpenRV 3.1 output on test files.

## Options

| Option | Effect |
|---|---|
| `PATH ...` | folders and files (no sequence specs, no wildcards on Windows) |
| `-l` | long listing: size, pixel type, channels, fps, frames, audio channels |
| `-x` | every attribute and the image structure, per sequence or file |
| `-yaml` | YAML instead of text; only with `-x` |
| `-s` | sequences only (hide files that are not part of a sequence) |
| `-a` | include hidden files |
| `-nr` | print sequences as `name.#.ext` without ranges |
| `-ns` | do not build sequences; one line per file |
| `-min N` | files needed to form a sequence (default 3) |
| `-b` | brute force: try every reader when the extension is unknown |
| `-o FILE` | write the listing to FILE instead of stdout |
| `-formats` | readable formats and their codecs (same list as `rvio -formats`) |
| `-version` | version number, e.g. `3.1.0` |
| `-debug plugins` | show which reader plugins load |

The listing goes to stdout, mixed with some `INFO: Read image info from ...` lines (so skip
lines starting with `INFO:` when parsing); reader errors go to stderr. The exit code is 0 in
every case tried, including a path that does not exist.

## Default listing

```text
$ rvls gaps
gaps/plate.1-5,8-10#.png
gaps/readme.txt
```

Padding tokens: `#` = 4 digits, `@` repeated = that many digits, a single `@` = no padding.
Range lists use `a-b`, `a`, `a-bxN` (step N) and negative numbers (`-2-1` is -2 to 1).

## Long listing (-l)

```text
     w x h     typ   #ch   fps   #fr   #ach   file
   320 x 180    8i   3      24    24      2   out/with_audio.mov
                                          2   out/ripped.wav
    64 x 64     8i   3       0    10          gaps/plate.1-5,8-10#.png
   320 x 180   16f   8       0    24          out/pair.1001-1024#.exr
     0 x 0      11   0                        out/broken.mp4
```

- `typ`: bits and `i` (integer) or `f` (float). DPX shows `8i` even when 10-bit.
- `fps` is 0 for image sequences (they carry no rate) and the movie's rate for movies.
- `#fr` is the frame span of a sequence (last - first + 1) or the frame count of a movie.
- `#ach` appears only when at least one listed file has audio.
- An audio-only file has only the `#ach` column filled.
- `0 x 0  11  0` means unreadable.

## Extended listing (-x)

Keys from several test files, shown together:

```text
out/shot.mov:
                   Resolution   320 x 180, 3ch, 8 bits/ch
                     Channels   Y, U, V
                     Duration   24 frames, 1 sec
                          FPS   24
                   VideoCodec   MJPEG (Motion JPEG)
             VideoPixelFormat   yuvj420p
              Track0/Timecode   01:00:00:00
               Timecode/Start   01:00:00:00 (86400)
                        Audio   Yes
                AudioChannels   2 total (1 tracks)
                   AudioCodec   PCM signed 24-bit little-endian
            AudioSamplingRate   48 kHz
                Movie/Comment   a comment
             PixelAspectRatio   1
```

`-x -yaml` prints the same data as YAML (`Attributes`, `Channels`, `Resolution` with `Width`,
`Height`, `Channels`, `Depth: {Bits, Type}`).

## Keys worth checking

| File type | Keys |
|---|---|
| Any image | `Resolution`, `Channels`, `PixelAspectRatio`, `ColorSpace/Transfer`, `ColorSpace/Primaries` |
| EXR | `EXR/compression`, `EXR/dwaCompressionLevel`, `EXR/dataWindow`, `EXR/displayWindow`, `EXR/multiView`, `EXR/type`, custom attributes (`EXR/<name>`) |
| DPX | `DPX/BitSize`, `DPX-0/Transfer`, `DPX-0/Colorimetric`, `DPX-0/Packing`, `DPX-TV/TimeCode`, `DPX/Project`, `DPX/Creator` |
| TIFF / PNG | `TIFF/*`, `PNG/ColorType` |
| Movie | `Duration`, `FPS`, `VideoTracks`, `VideoCodec`, `VideoPixelFormat`, `COLR/*`, `Timecode/Start`, `Track0/Timecode`, `Movie/Comment`, `Movie/Copyright`, `Movie/Encoder` |
| Audio | `Audio`, `AudioChannels`, `AudioCodec`, `AudioSampleFormat`, `AudioSamplingRate`, `AudioSamples` |
| Stereo movie | `VideoTracks 2` |

## Reader options

rvls accepts no reader flags of its own; RV's reader variables apply, for example
`RV_MOVIEFFMPEG_ARGS="--codecThreads 4"` or `RV_IOEXR_ARGS`. `-b` helps with files that have
an unusual or missing extension.
