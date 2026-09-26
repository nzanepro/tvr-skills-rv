# Formats, codecs, bit depth and -outparams

Read this when choosing an output container, video codec, image compression or bit depth, when
passing codec or header parameters with `-outparams`, or when a codec is refused.

## Movie codecs

What rvio can encode depends on how the RV build's FFmpeg was configured, and `rvio -formats`
lists only a short fixed set (dvvideo, mjpeg, rawvideo, pcm_s16be). Run
`scripts/rvio_codecs.py` to probe the build you have.

| Codec (`-codec`) | Stock OpenRV | Tested OpenRV 3.1 build | Autodesk RV | Notes |
|---|---|---|---|---|
| `mjpeg` | yes (default) | yes | yes | only codec that `-quality` 0-1 controls |
| `mpeg4` | yes | yes | yes | MPEG-4 part 2; set the rate with `-outparams vcc:b=8000000` |
| `png` | yes | yes | yes | lossless, keeps alpha |
| `prores_ks`, `prores_aw` | no | yes | yes (own ProRes) | 10-bit 4:2:2 by default; `vc:profile=hq`, `vc:profile=4` (4444) with `pix_fmt=yuva444p10le` |
| `dnxhd` | no | yes, fixed profiles only | yes (DNxHD / DNxHR) | 1920 x 1080 with `-outparams vcc:b=36000000` worked; other sizes / rates are rejected |
| `mpeg2video`, `mpeg1video`, `svq1` | blocked | yes | | |
| `cfhd`, `v210`, `v410`, `jpeg2000`, `tiff`, `gif`, `cinepak` | build dependent | yes | | |
| `dvvideo` | disabled | yes, PAL / NTSC sizes only | | 720 x 576 or 720 x 480 |
| `rawvideo` | | writes an unreadable `.mov` | | use `png` or an image sequence |
| `libx264`, `h264`, `libopenh264` | no | no ("Invalid video codec") | yes (libx264) | encode H.264 with ffmpeg from an MJPEG movie or image sequence |
| `hevc`, `libx265`, `libvpx-vp9`, `libaom-av1`, `qtrle`, `prores` | no | no | varies | |

"Stock OpenRV" follows the OpenRV build files (`cmake/dependencies/ffmpeg.cmake`: encoders such
as prores, dnxhd, dvvideo and aac are disabled unless the build lists them in
`RV_FFMPEG_NON_FREE_ENCODERS_TO_ENABLE`; H.264 is never linked). "Tested build" is the
Windows OpenRV 3.1.0 build this skill was checked with, which had several of them enabled.

Audio: PCM (`pcm_s16be` default, `pcm_s16le`, `pcm_s24le`) works. `aac` failed with a muxer
timestamp error. Audio-only outputs `.wav` and `.aiff` worked; `.mp3` failed ("Failed to
determine codec for audio").

Container notes: MJPEG and MPEG-4 wrote `.mov`, `.mp4`, `.avi` and `.mkv`; PNG wrote `.mov`,
`.mp4` and `.avi` but not `.mkv`. ProRes belongs in `.mov`; it failed in `.mp4`.

## -quality

- MJPEG movies: 0.0 to 1.0, default 0.9 (0.1 gave 39 KB, 0.99 gave 126 KB for the same clip).
- Other movie codecs ignore it; use `-outparams vcc:b=BITS_PER_SECOND` or
  `vcc:flags=qscale vcc:global_quality=N`.
- JPEG images: 0.0 to 1.0.
- EXR DWAA / DWAB: the DWA compression level (45 is the OpenEXR default; higher is smaller).

## -outparams

One `-outparams` followed by several `key=value` words, or the flag repeated; put it last on
the command line because it takes every word up to the next `-option`.

| Form | Applies to | Examples (verified) |
|---|---|---|
| plain keys | the movie writer | `timecode=01:00:00:00` (`;` before the frames means drop-frame), `reelname=A001`, `pix_fmt=yuvj444p`, `pix_fmt=yuva444p10le`, `comment=...`, `title=...`, `copyright=...` |
| `vcc:` / `acc:` | video / audio codec context | `vcc:b=8000000`, `vcc:g=12`, `vcc:bf=0`, `acc:b=160000` |
| `vc:` / `ac:` | encoder-private options | `vc:profile=hq` (ProRes), `vc:profile=4` (ProRes 4444) |
| `of:` | muxer-private options | `of:movflags=faststart` (stream-ready `.mov` / `.mp4`) |
| `NAME:TYPE=VALUE` | EXR / ACES header attributes | `shot:s=sh010`, `pi:f=3.14`, `size:v2i=1,2`, `chromaticities:c=...` |
| `passthrough=REGEX` | copy matching input attributes to the EXR header | `passthrough=.*EXIF.*` |
| DPX keys | DPX header | `transfer=LOG`, `colorimetric=REC709`, `project=...`, `tv/time_code=01:00:00:00`, `film/frame_rate=24` |

Unknown keys print `WARNING: Could not match option` and the encode continues, so check the
result with `rvls -x`. `rvio -formats` lists every key per format (thousands of lines).

## Image formats

| Format | Read | Write | Compression (`-codec`) | Bit depths (`-outformat`) |
|---|---|---|---|---|
| OpenEXR `.exr`, `.sxr` (multi-view), `.aces` | yes | yes | PIZ (default), ZIP, ZIPS, RLE, PXR24, B44, B44A, DWAA, DWAB, NONE | `16 float` (`-outhalf`), `32 float` |
| DPX `.dpx`, Cineon `.cin` | yes | yes | none | 10-bit packed; `-outformat 16 int` still wrote 10-bit in the tested build (rvls shows `8i`; `rvls -x` shows `DPX/BitSize 10`) |
| TIFF `.tif` / `.tiff` | yes | yes | NONE, LZW, DEFLATE, ADOBE_DEFLATE, PACKBITS, JPEG, SGILOG, SGILOG24, ... | `8 int`, `16 int`, `32 float` (the default when nothing is said) |
| PNG `.png` | yes | yes | | `8 int`, `16 int`; RGBA unless `-outrgb` |
| JPEG `.jpg` | yes | yes | `-quality` | 8 |
| Targa `.tga` | yes | yes | RLE, RAW | 8 |
| Radiance `.hdr`, SGI, PNM, BMP, DDS, WebP, IFF, RLA, FITS | yes | yes | | |
| PSD, GIF, JPEG 2000 (`.jp2`, `.j2k`, `.j2c`), camera raw (CR2, CR3, NEF, ARW, DNG, ...), PTex, Z-depth | yes | no | | |

Other `-outformat` forms: `-out8` = `8 int`, `-outhalf` = `16 float`. EXR B44 / B44A are
usually combined with chroma subsampling: `-yryby 1 2 2` or `-yrybya 1 2 2 1`.

## OpenRV compared with Autodesk RV for command-line work

| Topic | OpenRV 3.x | Autodesk RV (ShotGrid RV) |
|---|---|---|
| Licence | none; `-lic` and `-strictlicense` are ignored | licensed; rvio may consume a licence |
| GPU converter name | `rvio` (hardware / OpenGL build); Linux also ships `rvio_sw` | `rvio_hw`; `rvio` is the software build (Linux) |
| H.264, AAC, DNxHD / DNxHR, ProRes, ARRI / RED camera files | only what the build's FFmpeg enables; never H.264 | included |
| Old QuickTime codec names in the manual (`-codec "H.264"`, `-audiocodec "Apple Lossless"`, `-audioquality`) | do not work | older versions only |
| `-inlut`, `-outlut`, `-exrcompression`, `-floatLUT`, `-resampleMethod` | do not exist; use `-flut` / `-llut` / `-dlut`, `-codec` | some appear in old manuals |

Sources: OpenRV `src/bin/imgtools/rvio/main.cpp` and `cmake/dependencies/ffmpeg.cmake` on
GitHub; the OpenRV RV User Manual chapter 16 (rvio) at aswf-openrv.readthedocs.io; Autodesk's
"RV and Open RV" comparison page in the ShotGrid RV help.
