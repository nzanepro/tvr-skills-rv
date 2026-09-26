# EXR headers, multi-view / stereo, layers and channels

Read this when an EXR must carry metadata, when a job reads or writes stereo or multi-view
media, or when only some layers or channels should be used.

## EXR header attributes

```bash
rvio "in.#.png" -insrgb -outhalf -o "out.#.exr" -outparams "comment:s=first pass" "shot:s=sh010"
rvio exif.jpg -insrgb -o out.exr -outparams "passthrough=.*EXIF.*"   # copy input attributes
```

`NAME:TYPE=VALUE[,VALUE...]` creates an attribute. Types: `f` float, `i` int, `s` string,
`sv` string vector, `v2i` `v2f` `v3i` `v3f` vectors, `b2i` `b2f` boxes (4 values), `m33f`
`m44f` matrices, `c` chromaticities (8 values). Verified: `comment:s=hello world` and
`shot:s=sh010` appear as `EXR/comment` and `EXR/shot` in `rvls -x`.

Compression and levels: `-codec PIZ|ZIP|ZIPS|RLE|PXR24|B44|B44A|DWAA|DWAB|NONE`; for DWA
`-quality 45` sets `dwaCompressionLevel` (verified in the header). PIZ is the default.

## Reading EXR windows and channels

| Flag | Effect |
|---|---|
| `-exrReadWindow 0/1/2/3` | use the data window, display window (default), their union, or data inside display |
| `-exrReadWindowIsDisplayWindow` | treat the data window as the display window |
| `-exrRGBA` | always read as RGBA |
| `-exrInherit` | guess channel inheritance between layers |
| `-exrNoOneChannel` | never read single-channel planar images |
| `-exrcpus N` | decode threads (default 16) |

rvio writes a full data window equal to the display window.

## Layers, views and channels

| Need | Flags |
|---|---|
| Pick a layer, view or channel of a source | `[ -select layer diffuse in.#.exr ]`, `[ -select view left in.#.exr ]`, `[ -select channel R in.#.exr ]` |
| Map input channels by name | `[ -cmap R,G,B in.#.exr ]` or `-inchannelmap ...` |
| Choose / reorder output channels | `-outchannelmap R G B`, `-outrgb` (drops alpha); `-outchannelmap R R R` gave a grey image of the red channel |
| Chroma-subsampled planar EXR | `-yryby 1 2 2`, `-yrybya 1 2 2 1`, `-yuv 1 2 2` (with B44 / B44A / DWA) |

## Stereo and multi-view

Two files become one stereo source when they share a bracket: `[ left.#.exr right.#.exr ]`, or
use `name.#.%V.exr` (left / right) or `name.#.%v.exr` (L / R). `-so` / `-rso` offset the eyes.

`-outstereo MODE` decides what is written:

| Mode | Result (verified where marked) |
|---|---|
| `separate` (default) with `.exr` or `.sxr` | one multi-view EXR per frame, 8 channels, `EXR/multiView left, right` (verified) |
| `separate` with `.mov` | a movie with two video tracks (verified) |
| `anaglyph` | red / cyan image (verified) |
| `left`, `right` | one eye (`right` verified) |
| `pair`, `mirror` | side by side, mirrored side by side |
| `hsqueezed`, `vsqueezed` | side by side / over-under at the original size |
| `checker`, `scanline` | interleaved for passive displays |

Reading a multi-view EXR back, RV shows the views as `left` / `right`; pick one with
`[ -select view right in.#.exr ]` (verified on a two-view EXR).
