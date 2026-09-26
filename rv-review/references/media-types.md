# Media types: movies, sequences, multi-view, stereo, 360

Read this before loading anything other than stills or split sheets. Every example uses
`scripts/rv_review.py` (paths relative to the skill folder); the rv / rvpush equivalents are
in `rv-commands.md`.

Contents: [Movies](#movies) · [Image sequences](#image-sequences) ·
[Marks for multi-frame sources](#marks-for-multi-frame-sources) ·
[Multi-view and stereo](#multi-view-and-stereo) · [360 / lat-long](#360--lat-long-equirectangular) ·
[Sources](#sources)

## Movies

- Movies (mov, mp4, avi, mkv, mxf...) load as ordinary sources and play back to back with
  anything else in the list: `python scripts/rv_review.py shot_v1.mov shot_v2.mov`.
- **Frame rate.** RV plays the whole session at one rate. The default sequence takes the first
  source's rate; the script leaves it alone for movies and sequences and sets 1 fps only when
  every source is a still. `--fps 24` overrides it. A source's own rate is
  `rv.commands.sourceMediaInfo(src)['fps']` (0 when unknown).
- **Audio** plays when the movie has it. Mute with the property `#RVSoundTrack.audio.mute`
  (`[1]`), or start rv with `-noaudio`; per source, `-noMovieAudio` inside the brackets.
  OpenRV builds ship without non-free FFmpeg codecs (for example AAC, ProRes, HEVC), so such
  movies may play without audio or not decode; Autodesk RV builds include more codecs.
- **In / out per source** go inside brackets after `--`:
  `python scripts/rv_review.py -- [ shot_v1.mov -in 101 -out 120 ] [ shot_v2.mov -in 101 -out 120 ]`.
  The brackets must be separate arguments.
- **Comparing two versions.** Default is back to back (sequence) with a mark at each movie.
  On request:

  | Option | RV view | Use |
  |---|---|---|
  | `--compare wipe` | defaultStack, composite over, wipes on, split down the middle | first source on the left, second on the right; drag the wipe edge to move it |
  | `--compare difference` | defaultStack, composite difference | shows A minus B clamped at zero: only where the first source is brighter; where the second is brighter also reads black, and alpha is subtracted too. Run `--compare difference-inverted` (B minus A) for the other direction, or use `compare_dirs.py` for an absolute difference image |
  | `--compare over` / `replace` | defaultStack | first source on top |
  | `--compare tile` | defaultLayout (packed) | all sources side by side |

  Stack and tile modes compare the first frames of each source in parallel, so marks are off
  and the sources should cover the same frame range (use `-in` / `-out` to align them).

## Image sequences

- RV notation, quoted in the shell:

  | Spec | Meaning |
  |---|---|
  | `shot.#.exr` | every frame found, 4-digit padding |
  | `shot.1001-1100#.exr` | frames 1001-1100, padding 4 |
  | `shot.1001-1100@@@@@.exr` | `@` per digit of padding (here 5); a single `@` means none |
  | `shot.%04d.exr` | printf style |
  | `shot.1-100x10#.exr`, `shot.1,3,5#.exr` | every 10th frame; a frame list |
  | `shot.#.%V.exr` | stereo pair by view token (`%V` = left / right, `%v` = l / r) |

- `rvls DIR` lists what a folder holds with sequences collapsed
  (`shot_v1.1001-1012#.png`); `rvls -l` adds sizes, `rvls -x` shows channels and views.
  Copy the spec it prints straight into the source list.
- A sequence keeps its own frame numbers inside the source (`sourceMediaInfo(src)`
  `startFrame` / `endFrame`), but the timeline numbers frames globally from 1.
- Missing frames show as a "missing frame" card; check `rvls` output for gaps first.

## Marks for multi-frame sources

Marks go at the first global frame of every source. The script reads them back from RV rather
than assuming one frame per source:

- `rv.commands.getIntProperty('defaultSequence_sequence.edl.frame')` gives the global start
  frame of every source plus one terminator entry (end + 1); drop the last value.
  `rv.extra_commands.sequenceBoundaries()` returns the same boundaries.
- Example: `shot_v1.1001-1012#.png shot_v2.mp4 (8 frames) shot_v1.mov (12 frames)` gives
  `edl.frame = [1, 13, 21, 33]` and marks `[1, 13, 21]`.
- With per-source `-in` / `-out` the EDL reflects the trimmed lengths.
- When every source is one frame long (stills), automatic marks are skipped: a mark on every
  frame adds nothing. frames.json loads mark the first frame of every sheet instead.

## Multi-view and stereo

Stereo is the two-view case of multi-view media. An OpenEXR file (single or multi-part) can
hold any number of named views, for example `left`, `right`, `centre` or camera names.

- **List the views** of the loaded sources:
  `[(s, [v['name'] for v in rv.commands.sourceMediaInfo(s)['viewInfos']]) for s in rv.commands.nodesOfType('RVFileSource')]`.
  `sourceMediaInfo(s)['defaultView']` is the view shown by default. Outside RV, `rvls -x file.exr`
  lists them. Movies report a single view such as `track 1`.
- **Show one view:** `--views centre` sets the source property
  `request.imageComponent = ['view', 'centre']` on every multi-view source that has it, then
  reloads. On the rv command line: `rv [ file.exr -select view centre ]`.
- **Flip through all views:** `--views all` (or `--views left,centre,right` for an order and a
  subset) loads the file once per view, one frame per view, back to back. Left / Right then
  flips between views like versions on a sheet.
- **Stereo pair from any two views:** `--stereo pair --stereo-views left,centre` sets
  `request.stereoViews = ['left', 'centre']` on sources that have those views and the display
  mode to side by side. With no `request.stereoViews`, RV uses `left` / `right` when both
  exist, otherwise the first view as left and the last as right.
- **Separate files per eye:** `-- [ left.exr right.exr ]` (or two movies or sequences) is one
  stereo source; RV uses the first two media in the brackets as left and right. More than two
  eyes cannot be grouped this way: for three or more per-view files, load each as its own
  source and flip, or build a multi-view EXR.
- **Display modes** (`--stereo MODE`, property `@RVDisplayStereo.stereo.type`): `off`,
  `anaglyph`, `lumanaglyph`, `pair` (side by side), `mirror`, `hsqueezed`, `vsqueezed`,
  `checker`, `scanline`, `left`, `right`, `hardware`. `--swap-eyes` sets
  `@RVDisplayStereo.stereo.swap`.
- **Safe default on a normal monitor:** stereo off (shows the default view). For a stereo check
  use `pair` (no glasses needed) or `anaglyph` (red / cyan glasses). `checker`, `scanline` and
  `hardware` need a matching 3D display; `hardware` needs quad-buffered OpenGL.
- Stereo mode is a display setting that outlives a refresh; the script sets it (default `off`)
  on every load.

## 360 / lat-long (equirectangular)

- **RV has a lat-long viewer.** OpenRV and Autodesk RV (since RV 6) ship the
  `lat_long_viewer` package, which adds a `LatLongViewer` node: a rectilinear window into the
  sphere, like a VR headset view on a flat monitor. The package is marked experimental and
  optional, so it is not loaded by default.
- `--latlong` puts a `LatLongViewer` node on top of the current view. On a new window the
  script also starts rv with `-flags ModeManagerPreload=lat_long_viewer`, which loads the
  package so Shift+drag looks around; a window started without it shows the 360 view but
  cannot be dragged (close it and run again with `--latlong`). Users can also load it from
  Preferences > Packages and use Image > Lat-Long Viewer.
- Parameters on the node (`<node>.parameters.*`): `rotateY` (yaw, default 180 = image
  centre; 0 looks at the left / right seam), `rotateX` (pitch, default 90 = horizon),
  `focalLength` (default 7; higher zooms in), `hAperture` (default 24). Set them with
  `rv.commands.setFloatProperty('#LatLongViewer.parameters.rotateY', [0.0], True)`.
- **What to check on a 360 image**, in the 360 view or on the flat image:
  - 2:1 aspect (for example 4096 x 2048); anything else distorts the sphere.
  - The seam: the left and right edges must continue into each other (rotateY 0).
  - The poles: top and bottom rows should be uniform; pinching or smearing shows at
    rotateX near 0 and 180.
  - The horizon is level on the middle row and nothing important sits on the seam.
- Without the viewer (or for a quick flat check) load the image as a still and review it flat,
  using the checks above.
- Stereo 360 (top / bottom or left / right packed) is not split automatically; the optional
  `stereo_disassembly` package can separate packed eyes.

## Sources

- OpenRV user manual, command line and sequences:
  https://aswf-openrv.readthedocs.io/en/latest/rv-manuals/rv-user-manual/rv-user-manual-chapter-three.html
- OpenRV reference manual, node properties (RVFileSource `request.imageComponent`,
  `request.stereoViews`, RVSequence `edl.*`, RVStack `composite.type`):
  https://aswf-openrv.readthedocs.io/en/latest/rv-manuals/rv-reference-manual/rv-reference-manual-chapter-sixteen.html
- rvls: https://aswf-openrv.readthedocs.io/en/latest/rv-manuals/rv-user-manual/rv-user-manual-chapter-seventeen.html
- OpenRV source: `src/plugins/rv-packages/lat_long_viewer` (the 360 viewer),
  `src/lib/ip/IPBaseNodes/SourceIPNode.cpp` (default stereo views), `rvui.mu` in the installed
  RV (stereo modes, wipes, composite modes).
- Verified live with OpenRV on synthetic media: sequences plus movies (marks from the EDL),
  a three-view EXR (view selection, `--views all`, any-two-view stereo pair), per-eye bracket
  groups, wipe / difference / tile, and the lat-long viewer.
