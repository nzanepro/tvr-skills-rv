# Verified rvio commands, timings and codec results

Read this when you need a command that is known to work, want to know how long a job takes,
or need to explain why a codec or option failed. Everything below was run with OpenRV 3.1.0
(Windows build, FFmpeg 7.1) on synthetic test media: `src/shot_v001.1001-1024#.png`, 24 frames
of 320 x 180 sRGB PNG with the frame number drawn on each, a 1 s stereo 48 kHz tone
`tone.wav`, a 17-point inverting `invert.cube`, and a 64 x 64 logo. Outputs were read back
with `rvls -l`, `rvls -x` and by comparing pixels with the source. Times are wall-clock for
the whole command; about 1 s of each is start-up.

## Builds checked

| OS | Build | What was run |
|---|---|---|
| Windows 11 | OpenRV 3.1.0, FFmpeg 7.1, ProRes / DNxHD / DV / MPEG-2 encoders enabled | everything on this page |
| macOS 26 (Apple silicon) | OpenRV 3.0.0, local build (`RV.app`, no `rvio_hw` / `rvio_sw`) | the codec probe and the round trips in [macOS, OpenRV 3.0.0](#macos-openrv-300) |

## Conversions

| Command | Result | Time |
|---|---|---|
| `rvio "src/shot_v001.#.png" -o shot.mov` | MJPEG yuvj420p, 24 fps, 24 frames | 1.2 s |
| `rvio "src/shot_v001.#.png" -codec mpeg4 -o shot.mp4` | MPEG-4 part 2 | 1.3 s |
| `rvio "src/shot_v001.#.png" -codec prores_ks -outparams vc:profile=3 -o shot.mov` | ProRes 422 HQ, yuv422p10le | |
| `rvio "src/shot_v001.#.png" -codec prores_ks -outparams vc:profile=4 pix_fmt=yuva444p10le -o shot.mov` | ProRes 4444 with alpha | |
| `rvio "src/shot_v001.#.png" -resize 1920 1080 -codec dnxhd -outparams vcc:b=36000000 -o shot.mov` | DNxHD 36, yuv422p | |
| `rvio shot.mov -o "back/shot_v001.#.png"` | frames 0001-0024 | 1.2 s |
| `rvio shot.mov -t 5-10 -o "back/range.#.jpg"` | frames 0005-0010 | 1.0 s |
| `rvio [ -rs 1001 shot.mov ] -t 1001-1003 -o "m.#.png"` | frames 1001-1003 | |
| `rvio "src/shot_v001.#.png" -insrgb -outhalf -codec PIZ -o "exr/lin.#.exr"` | 16f, 4 channels, PIZ | 1.2 s |
| `rvio "src/shot_v001.#.png" -insrgb -outhalf -codec DWAA -quality 45 -o "exr/dwaa.#.exr"` | DWAA, level 45 in the header | 1.2 s |
| `rvio "exr/lin.#.exr" -outsrgb -o "back/fromexr.#.png"` | identical to the source pixels | 1.3 s |
| `rvio "exr/lin.#.exr" -outlog -o "dpx/log.#.dpx"` | 10-bit Cineon log DPX | 1.2 s |
| `rvio "dpx/log.#.dpx" -inlog -outsrgb -o "back/fromlog.#.png"` | within 1 code value of the source | 1.2 s |
| `rvio "src/shot_v001.#.png" -outformat 16 int -codec LZW -o "tif/t16.#.tif"` | 16-bit LZW TIFF, RGBA | 1.2 s |
| `rvio "src/shot_v001.#.png" -outformat 8 int -outrgb -o "tif/t8.#.tif"` | 8-bit RGB TIFF | |
| `rvio "src/shot_v001.#.png" -quality 0.7 -o "jpg/q70.#.jpg"` | JPEG | 1.1 s |
| `rvio "src/shot_v001.#.png" -resize 160 0 -o "res/half.#.jpg"` | 160 x 90 | 1.2 s |
| `rvio "src/shot_v001.#.png" -scale 0.25 -o "res/quarter.#.png"` | 80 x 45 | 1.2 s |
| `rvio "src/shot_v001.#.png" -outres 640 360 -o "res/outres.#.png"` | 640 x 360 | 1.3 s |
| `rvio [ -crop 0 0 159 89 "src/shot_v001.#.png" ] -o "res/crop.#.png"` | 160 x 90 | 1.1 s |
| `rvio [ -uncrop 320 240 0 30 "src/shot_v001.#.png" ] -o "res/uncrop.#.png"` | 320 x 240 letterbox | 1.2 s |
| `rvio "src/shot_v001.1001-1005#.png" "other.1010-1012#.png" -o concat.mov` | 8 frames | 1.2 s |
| `rvio "src/shot_v001.#.png" -fps 25 -o fps25.mov` / `-outfps 30` | 25 / 30 fps, 24 frames each | 1.2 s |
| `rvio [ "src/shot_v001.#.png" tone.wav ] -o with_audio.mov` | 2-channel PCM 16-bit | 1.2 s |
| `rvio [ "src/shot_v001.#.png" tone.wav ] -audiocodec pcm_s24le -o with_audio24.mov` | 24-bit PCM | 1.2 s |
| `rvio [ "src/shot_v001.#.png" tone.wav -ao 0.5 ] -audiorate 44100 -audiochannels 1 -o mono44.mov` | 1 channel | 1.2 s |
| `rvio with_audio.mov -o ripped.wav` | 2-channel WAV | 1.1 s |
| `rvio [ left.#.png right.#.png ] -outstereo separate -o "pair.#.exr"` | multi-view EXR, 8 channels | 1.3 s |
| `rvio [ left.#.png right.#.png ] -outstereo separate -o pair.mov` | two video tracks | 1.3 s |
| `rvio "src/shot_v001.#.png" -insrgb -outhalf -o "attr.#.exr" -outparams "comment:s=hello world" "shot:s=sh010"` | `EXR/comment`, `EXR/shot` | 1.2 s |
| `rvio "src/shot_v001.#.png" -codec mjpeg -outparams timecode=01:00:00:00 -o tc.mov` | `Timecode/Start 01:00:00:00` | |
| `rvio "src/shot_v001.#.png" -outpa 2.0 -comment "a comment" -copyright "c" -o meta.mov` | `PixelAspectRatio 2`, `Movie/Comment`, `Movie/Copyright` | 1.2 s |
| `rvio "src/shot_v001.#.png" -flut invert.cube -o "flut.#.png"` (also `[ -llut ... ]`, `-dlut`) | inverted pixels | 1.2-1.3 s |
| `rvio "smptebars,start=1,end=2,fps=24,width=64,height=64.movieproc" -codec prores_ks -o p.mov` | generated bars, no input files | 1.1 s |
| `rvio "src/shot_v001.#.png" -o session.rv` | RV session file (GTO text) | |
| `rvio session.rv -o fromsession.mov` | renders the session | |
| `rvio "src/shot_v001.#.png" -o throughput.null` | decodes only | 1.1 s |

Overlays and slates: see `slates-overlays.md` (all five scripts verified, 1.2-1.4 s each).

## Codec probe (`scripts/rvio_codecs.py`, 22 s for 22 codecs)

- Wrote `.mov`: mjpeg, mpeg4, png, prores_ks, prores_aw, dnxhd (1920 x 1080 at 36 Mbit/s),
  mpeg2video, mpeg1video, dvvideo (720 x 576), cfhd, v210, v410, jpeg2000, tiff.
- Refused ("Invalid video codec"): libx264, h264, hevc, libx265, libvpx-vp9, libaom-av1,
  qtrle, prores. Also refused: libopenh264, libvpx, libsvtav1, libopenjpeg, flv1.
- Refused by the container: ffv1, huffyuv, utvideo, msmpeg4v2 in `.mov`; png in `.mkv`;
  prores_ks in `.mp4`.
- Needs specific sizes: dnxhd (fixed profiles), dvvideo (PAL / NTSC), h263.
- rawvideo in `.mov` printed "cannot be written to mov, output file will be unreadable".
- Audio: pcm_s16be (default), pcm_s16le, pcm_s24le worked; aac failed in `.mov` and `.mp4`
  ("non monotonically increasing dts"); `.mp3` output failed.

The tested build had ProRes, DNxHD, DV and MPEG-2 encoders enabled; a stock OpenRV build
leaves those out. Probe your own build before promising a codec.

## macOS, OpenRV 3.0.0

A stock-style OpenRV 3.0.0 build on an Apple silicon Mac, probed with
`scripts/rvio_codecs.py` (`.mov`):

| Result | Codecs |
|---|---|
| Written | mjpeg, mpeg4, png, mpeg1video, cfhd, v210, v410, jpeg2000, tiff |
| Refused with `ERROR: Unsupported codec: <name>` | prores_ks, prores_aw, prores, mpeg2video, hevc |
| Refused with `ERROR: Invalid video codec: <name>` | dnxhd, dvvideo, libx264, h264, libx265, libvpx-vp9, libaom-av1, qtrle |

No ProRes, DNxHD, DV or MPEG-2 encoders, as expected for a stock build; the refusal wording
depends on the codec, so match either message. These round trips worked (0.35-0.46 s each):

| Command (through `scripts/rvio_cmd.py ... --run`) | Result |
|---|---|
| `seq/test.#.png -o test.mov --codec mjpeg --quality 0.9` | 10 frames, 640 x 360, 24 fps |
| `seq/test.#.png -o test.mp4 --codec mpeg4` | 10 frames, 640 x 360, 24 fps |
| `test.mov -o back/test.#.png` | frames 0001-0010, 4 channels (rvio adds alpha) |
| `seq/test.#.png -o exr/test.#.exr --in-colour srgb --outformat 16 float --exr-compression DWAA --quality 45` | 10 frames, 16f |
| `nonexistent.#.png -o nothing.mov` | stopped by `rvio_cmd.py` before rvio ran (exit 2), so no placeholder movie |

rvio printed a harmless Qt warning on stderr when the shell locale was `C` (`Detected locale
"C" with character encoding "US-ASCII" ... switched to "UTF-8"`); set `LANG` to a UTF-8
locale to silence it.

## Failures that still exit 0

| Command | What happened |
|---|---|
| `rvio nonexistent.#.png -o nothing.mov` | `ERROR: Open of ... failed`, exit 0, a 1280 x 720, 19-frame placeholder movie |
| `rvio "src/shot_v001.*.png" -o star.mov` | same placeholder (no wildcard expansion on Windows) |
| `rvio "src/shot_v001.#.png" -o "missing_dir/x.#.png"` | an error per frame, exit 0, nothing written |
| `rvio "gaps/plate.#.png" -o "fill.#.png"` (frames 6-7 missing) | frames 6 and 7 written as copies of frame 5, no warning |
| `rvio "src/shot_v001.#.png" -t 1001-1003 -o one.png` | one file, overwritten per frame |
| `rvio "src/shot_v001.#.png" -overlay bug logo.png ...` | no logo drawn (PNG logo ignored; TIFF works) |

Failures with a non-zero exit (-1): unknown codec, unsupported format extension
(`No plugins support (write) format`), codec / container mismatch, DNxHD or DV size mismatch,
AAC muxing error.
