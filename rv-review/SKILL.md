---
name: rv-review
description: Open comparison images (render sheets, before/after contact sheets, look-dev or texture versions) in RV / OpenRV for review, as one labelled flip-book sequence. Use whenever work produces images that should be compared or approved, or when asked to "open in RV", "review in RV" or "show the sheets".
---

# Review images in RV

Loads comparison images into one RV session as a labelled flip-book, so versions of the same view
can be flipped in place.

- **Panels, not sheets.** A contact sheet stacks several renders of the same view (for example
  before / after / v2). Each panel becomes its own frame, so stepping frames flips the versions on
  the same camera. Do not tile them, and do not load whole sheets as frames.
- **Labels on every frame.** Each frame keeps the sheet's title band and that panel's own label box.
- **Everything in one session, back to back.** View 1 panels, then view 2 panels, and so on, with
  a timeline mark on the first frame of every view.
- **Same size.** All frames are padded to the largest size, centred on the sheet background, so
  nothing shifts while flipping.

## Requirements

- RV or OpenRV. The launcher finds `rv.exe` from `-RvBin`, the `RV_BIN` environment variable,
  `PATH`, or the usual install folders under Program Files.
- Python 3 with numpy and Pillow for the frame helper.
- Windows PowerShell for the launcher. On other platforms, run the same `rv` and `rvpush`
  commands shown under Gotchas.

The scripts live in this skill's `scripts/` folder; `<skill>` below means this skill's directory.

## Steps

1. **Collect the sheets** in the order they should be seen (the order passed is the order in RV).
   Do not build the list with a glob or a sort: alphabetical order puts `v10` before `v9`.

2. **Split them into labelled frames**, written next to the sheets so the review can be reopened:

   ```bash
   python <skill>/scripts/sheet_panels.py split sheet_a.png sheet_b.png sheet_c.png --out <sheet folder>/rv_frames
   ```

   Panels are found by the full-width background rows between them. Frames are written as
   `<sheet>__<label>__<N>.png` with the index last, because RV reads the last number in a file
   name as the frame number. `rv_frames/frames.json` lists the frames (absolute paths) and the
   first frame of each view. Panel labels come from the last tokens of the sheet's file name
   (`sheet_side_before_after.png` gives `before`, `after`).

   For renders that were never stacked, burn a title band and label box on instead (all images
   must be the same size):

   ```bash
   python <skill>/scripts/sheet_panels.py label --title "Shot 010: key light" --out <dir> a.png="before" b.png="after"
   ```

3. **Open or refresh RV:**

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "<skill>\scripts\rv_review.ps1" -FramesJson "<sheet folder>\rv_frames\frames.json"
   ```

   If an RV started by this script is still open (network tag `claude`, change with `-Tag`), its
   contents are replaced in place with `rvpush set`. Otherwise a new RV is launched detached. Either
   way the script switches to the sequence view, stops playback, sets 1 fps, goes to the first
   frame and adds the view marks. Plain image paths also work instead of `-FramesJson` (add
   `-Marks "1,4,7"` for marks).

4. **Report** which views are loaded, in what order, and the frame each view starts on, with the
   keys:

   | Key | Action |
   |---|---|
   | Left / Right | previous / next frame (flip versions) |
   | Alt+Left / Alt+Right | previous / next view (jump between marks) |
   | Ctrl+Left / Ctrl+Right | loop just one view's frames |
   | Space | play at one image per second |

## When making new comparison sheets

Keep the separate panel renders beside every stacked sheet (for example a `renders/` folder with
one PNG per version and view, same camera and size), so there is always a clean source for RV.

## Gotchas

- **rvpush needs the tag.** RV must be started with `-network -networkTag <tag>`, and every push
  uses `-tag <tag>`. Set `RVPUSH_RV_EXECUTABLE_PATH=none` so rvpush never launches RV itself: an RV
  started by rvpush is tied to the calling shell and can die when that shell exits. Launch RV
  detached instead (`Start-Process` on Windows).
- **rvpush exits 0 even when the Python fails.** In `py-exec`, spell out `rv.commands.` in full
  (local imports are not visible inside comprehensions there) and read the state back to check:
  `rvpush -tag <tag> py-eval-return "(rv.commands.frame(), rv.commands.frameEnd(), rv.commands.markedFrames())"`.
- **`rvpush set` versus `merge`.** `set` replaces the session (frame 1, marks cleared); `merge`
  appends.
- **Each `rv` launch opens a new window.** Reuse a window only through `rvpush`.
- **`-InfoStrip`** (the F7 overlay) makes RV save the strip as on in its preferences when it exits,
  so it is off by default; the labels are burnt into the frames anyway.
- **Quoting on Windows.** With `Start-Process`, wrap each path in literal double quotes; spaces,
  `&` and parentheses work. Inside `py-exec` strings use single quotes only (PowerShell 5.1).
- **Other layouts**, only if asked: `rv -wipe a b`, `rv -diff a b`, `rv -tile a b c`.

Docs: [command line](https://aswf-openrv.readthedocs.io/en/latest/rv-manuals/rv-user-manual/rv-user-manual-chapter-three.html),
[rvpush](https://aswf-openrv.readthedocs.io/en/latest/rv-manuals/rv-user-manual/rv-user-manual-chapter-eighteen.html),
[OpenRV](https://github.com/AcademySoftwareFoundation/OpenRV).
