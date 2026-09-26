# Stacked sheet layout

Read this when `sheet_panels.py split` reports "not a stacked sheet", when labels or panels
come out wrong, or when designing a script that makes new comparison sheets.

## What a stackable sheet looks like

```
+------------------------------------------+
| title band: text on any colour           |  <- pixel (0, 0) is sheet background
|                                          |
| (full-width background rows)             |
| panel 1   [label]                        |
| (full-width background rows)             |
| panel 2   [label]                        |
| ...                                      |
+------------------------------------------+
```

- **One background colour.** The top-left pixel (0, 0) defines it. Keep that corner free of
  text and title-band colour: start the band a few rows down, or draw the title text on the
  background itself.
- **Separators are whole rows.** Between panels there must be at least one row that is
  background colour across the full width (tolerance 2 per channel). A panel that contains a
  full-width row of exactly the background colour (a flat black sky on a black sheet) is cut
  in two: use a background colour that cannot occur in the renders, such as (24, 24, 24).
- **Panels are tall enough.** Runs shorter than 64 px or one eighth of the sheet width are
  ignored as stray text or rules.
- **At least two panels.** A single render is not a stacked sheet; use `label` instead.
- **Same camera and width** for every panel; heights may differ (frames are padded).
- **Label boxes inside the panel** (for example a black box with white text in its top-left
  corner), so each frame carries its own label.

## How panels become frames

1. Rows that match the background across the full width are marked as separators.
2. Every run of non-separator rows at least the minimum height is a panel; everything above the
   first panel, including the gap, is the title band.
3. Each frame is the title band followed by one panel, as tall as the tallest panel.
4. Frames from all sheets are padded to the largest width and height, centred on each sheet's
   own background colour.

## Labels and file names

- Panel labels come from the last `_`-separated tokens of the sheet's file name, one per panel:
  `shot010_side_before_after_v2.png` with three panels gives `before`, `after`, `v2`. With
  fewer tokens than panels the labels are `1`, `2`, `3`.
- So name sheets `<shot>_<view>_<label1>_<label2>...png` and keep labels free of `_`
  (use `-` inside a label: `key-light`).
- Frames are written as `<sheet>__<label>__<N>.png`; N is the running index over all sheets,
  last in the name because RV reads the last number as the frame number.
- `frames.json` holds `size`, `frames` (absolute paths in order) and `views` (for each sheet:
  its first frame, the sheet path and the labels).

## label, for renders that were never stacked

`label --title TEXT --out DIR a.png=before b.png=after` adds a (24, 24, 24) title band above
each image with two rows: the title (30 px Arial, else DejaVu Sans or Pillow's built-in font)
and under it a light label box with 26 px black text. Nothing is drawn over the image. The
band, fonts and box grow with the image once it is larger than 1280 x 800 (by the larger of
width / 1280 and height / 800), so they stay readable when RV fits the frame to its window:
a 1440 px wide page gets a 36 px title, a 1206 x 2622 phone capture a 98 px one. A title or
label too long for the width is shortened with "...". `compare_dirs.py` and `review_set.py`
draw their frames the same way. All images must be the same size. `split` draws nothing: its
frames keep the sheet's own title band and label boxes.

## Checklist for a script that makes sheets

- [ ] Background colour set once, pixel (0, 0) left as background
- [ ] At least 8 px of background between panels, across the full width
- [ ] Label box in each panel, file name ends with the labels in panel order
- [ ] Separate per-panel renders kept next to the sheet for a clean source
