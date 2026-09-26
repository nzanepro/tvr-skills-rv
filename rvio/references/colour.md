# Colour with rvio: transfer functions, LUTs, OCIO, ACES

Read this whenever an rvio job changes colour space, bit depth or look: linear EXR to an sRGB
movie, log DPX, gamma, exposure, LUT files, OpenColorIO configs, ACES output or white point.

## How rvio thinks about colour

rvio works in floating point. Input flags linearise each source, the look and display steps
run, then output flags encode for the target file. Nothing is inferred from file type: a
linear EXR written to an 8-bit movie without `-outsrgb` (or `-out709`, or a display LUT) looks
dark, and an sRGB PNG read without `-insrgb` is treated as if it were already linear.

Order of operations, following RV's colour pipeline: input transfer (`-insrgb`, `-inlog`, file LUT `-flut`) -> exposure ->
look LUT (`-llut`) -> display LUT (`-dlut`) -> output transfer (`-outsrgb`, `-outlog`,
`-outgamma`) -> bit depth (`-outformat`).

## Transfer functions

| Input flag | Converts from | Output flag | Converts to |
|---|---|---|---|
| (none) | already linear | (none) | stays linear |
| `-insrgb` | sRGB | `-outsrgb` | sRGB |
| `-in709` | Rec.709 | `-out709` | Rec.709 |
| `-inlog` | Cineon log (printing density) | `-outlog` | Cineon log |
| `-inredlog`, `-inredlogfilm` | RED log / RED log film | `-outredlog`, `-outredlogfilm` | RED log / log film |
| `-ingamma G`, `-filegamma G` | gamma G | `-outgamma G` | gamma G |

Other pixel controls: `-exposure STOPS`, `-inpremult` / `-inunpremult`, `-outpremult` /
`-outunpremult`, `-inchannelmap` / `-outchannelmap`, `-q` (best-quality conversions, slower).

Verified on a 320 x 180 sRGB PNG sequence (OpenRV 3.1):

| Chain | Result |
|---|---|
| PNG `-insrgb` -> half EXR, then EXR `-outsrgb` -> PNG | identical to the source (max difference 0) |
| the same EXR -> PNG without `-outsrgb` | dark: (110, 60, 149) became (40, 12, 77) |
| EXR `-outlog` -> DPX, then DPX `-inlog -outsrgb` -> PNG | max difference 1 code value |
| PNG `-exposure 1` (no `-insrgb`) | values doubled in the encoded space: 110 -> 220 |
| PNG `-outgamma 2.2` | 110 -> 174 |

For a correct exposure change on display-referred input, linearise first:
`rvio in.#.png -insrgb -exposure 1 -outsrgb -o out.#.png`.

## LUT files

| Flag | Where it applies | Scope |
|---|---|---|
| `-flut FILE` | file LUT: applied to the source pixels first, usually to linearise them | per source, or global before the sources |
| `-llut FILE` | look LUT, after linearisation | per source: `[ -llut look.cube in.#.exr ]` |
| `-dlut FILE` | display LUT, applied last, before the output transfer | global |
| `-pclut FILE` | pre-cache LUT, applied in software as frames are read | per source |
| `-fcdl FILE`, `-lcdl FILE` | ASC CDL as file or look correction | per source |

A 17-point `.cube` 3D LUT worked with `-flut`, `[ -llut ... ]` and `-dlut` (an inverting LUT
inverted the pixels in all three positions). RV also reads its own `.csp`, `.rv3dlut` /
`.rvchlut` and other common LUT formats; `RV_LUT_PATH` adds folders to search.

## OpenColorIO

rvio has no OCIO command-line flag, and its start-up script is empty, so RV's optional
"OpenColorIO Basic Color Management" package (which reads the `OCIO` variable inside RV) does
not run in rvio. Setting `OCIO` alone changed nothing in a test. Three ways to get an OCIO
transform into an rvio render:

1. **Bake a LUT** with OpenColorIO's own tool (installed separately):
   `ociobakelut --inputspace "ACEScg" --displayview "sRGB - Display" "ACES 1.0 - SDR Video"
   --format resolve_cube acescg_to_srgb.cube`, then `rvio in.#.exr -dlut acescg_to_srgb.cube
   -o out.mov`. Colour-space names come from the config (`ociocheck` lists them). A 3D LUT
   cannot hold unbounded scene-linear range, so shaper options (`--shaperspace`) matter for
   HDR sources.
2. **Render an RV session.** Set up the OCIO view in RV (with the OCIO package opted in),
   save the session as `.rv`, then `rvio session.rv -o out.mov`; the OCIO nodes are part of
   the session.
3. **Custom init script** (`-init file.mu` or `RVIO_INIT`) that adds OCIO nodes to every
   source. This needs Mu scripting against RV's node graph; prefer 1 or 2.

## ACES and white point

- `-outaces` converts to the ACES (AP0) gamut and, with a `.aces` output, writes ACES container
  EXRs: `rvio in.#.dpx -inlog -outhalf -outaces out.#.aces`.
- `-outillum NAME` sets the output white (A, B, C, D50, D55, D65, D65REC709, D75, E,
  F1-F12); `-outwhite x y` gives it as CIE xy. `-outillum D65REC709` with `-outaces` skips
  chromatic adaptation.
- EXR chromaticities can be written directly: `-outparams chromaticities:c=x_r,y_r,x_g,y_g,x_b,y_b,x_w,y_w`.

## Bit depth

`-outformat BITS TYPE` (`8 int`, `16 int`, `16 float`, `32 float`), shorthands `-out8`,
`-outhalf`. Linear data belongs in float (EXR half is the usual choice); 8-bit outputs need an
sRGB / Rec.709 / log encoding first or they band. rvio writes PNG and TIFF with alpha unless
`-outrgb`, and TIFF as 32-bit float unless told otherwise.

## Checking colour results

- Convert one frame to PNG with the intended display transform and compare pixel values with
  the source: `rvio out.#.exr -t 1010 -outsrgb -o check.png`.
- `rvls -x FILE` shows the recorded transfer and primaries (`ColorSpace/Transfer`,
  `ColorSpace/Primaries`, `DPX-0/Transfer`, movie `COLR/*` atoms).
- `rmsImageDiff A B` in the RV bin folder prints per-channel RMS differences for two images
  of the same size and channel layout.
