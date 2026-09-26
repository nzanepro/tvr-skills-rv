"""Turn stacked comparison sheets into equal-size labelled frames for flipping in RV.

A stacked sheet is one PNG holding several renders of the same view, one above the other:

    +--------------------------------------+
    | title band (text on any colour)      |   pixel (0, 0) must be sheet background
    |--------------------------------------|
    | background gap (full-width rows)     |
    | panel 1  [label box]                 |
    | background gap                       |
    | panel 2  [label box]                 |
    | ...                                  |
    +--------------------------------------+

The background colour is read from pixel (0, 0). Every full-width row of that colour
(within a small tolerance) separates panels; everything above the first panel, gap
included, is the title band. The panel labels come from the last tokens of the sheet's
file name: shot010_side_before_after.png has panels "before" and "after".

split  SHEET [SHEET ...] --out DIR
    For every sheet, in the order given, write one PNG per panel: the sheet's title band
    followed by that panel (with its own burnt-in label box). All frames of all sheets are
    padded, centred on their sheet's background colour, to the largest frame size so
    nothing shifts while flipping. Files are named <sheet>__<label>__<N>.png with one
    running index N across all sheets, last in the name, because RV reads the last number
    in a file name as the frame number. Writes DIR/frames.json
    {"size": [W, H], "frames": [absolute paths], "views": [{"frame", "sheet", "labels"}]}
    and prints the frame paths, one per line.

label  --title TEXT --out DIR IMAGE=LABEL [IMAGE=LABEL ...]
    For renders that were never stacked: add a title band above each image holding the
    title and, under it, a label box; nothing is drawn over the image. Band, fonts and box
    grow with the image (frame_layout) so they stay readable when RV fits the frame to its
    window. All images must be the same size. Prints the frame paths, one per line.

Exit status is 0 on success; problems (not a stacked sheet, mismatched sizes) exit
non-zero with a message on stderr.
"""
import os
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# --- split: panel detection ---------------------------------------------------
BG_TOLERANCE = 2          # max per-channel difference still counted as background (dither, noise)
MIN_PANEL_PX = 64         # a non-background run shorter than this is stray text or a rule, not a panel
MIN_PANEL_WIDTH_DIV = 8   # ... or shorter than width / 8, so thin rules on wide sheets are skipped too

# --- label: title band and label box drawn on generated frames ---------------------
# Frames the scripts generate (label, compare_dirs.py, review_set.py) carry a title band
# above the image with two rows: the title, and under it a label box naming the frame.
# Nothing is drawn over the image. Sizes are given for scale 1 and grow with the image, so
# the text stays readable when RV fits the whole frame in its window: an image larger than
# REF_VIEW in either direction scales everything up by the larger ratio (a 1440 x 900 page
# by 1.2, a 1206 x 2622 phone capture by 3.3).
REF_VIEW = (1280, 800)              # a typical RV viewer area; bigger frames are shrunk to fit it
BG = (24, 24, 24)                   # dark neutral grey: reads as "not image" and keeps text legible
TITLE_FONT_SIZE = 30                # title text, at scale 1
TITLE_TEXT_COLOR = (235, 235, 235)  # off-white, softer than pure white on the dark band
LABEL_FONT_SIZE = 26                # label text, at scale 1: the label is what changes while flipping
MIN_LABEL_FONT_SIZE = 12            # a long label shrinks down to this before it is shortened
BAND_PAD_X = 16                     # left and right margin inside the band
BAND_PAD_Y = 10                     # margin above the title and below the label box
ROW_GAP = 8                         # space between the title row and the label box
LABEL_PAD_X = 10                    # space between the label box edges and its text, left and right
LABEL_PAD_Y = 5                     # space between the label box edges and its text, top and bottom
LABEL_BOX_COLOR = (235, 235, 235)   # light box, dark text: stands out from the title on the band
LABEL_TEXT_COLOR = (0, 0, 0)
FONT_FILES = ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf",
              "Helvetica.ttc")      # first one found; else Pillow's built-in font


_FONT_CACHE = {}


def font(size):
    """A TrueType font of size px (cached), falling back to Pillow's built-in font."""
    size = max(1, int(round(size)))
    if size not in _FONT_CACHE:
        f = None
        for name in FONT_FILES:
            try:
                f = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        if f is None:
            try:
                f = ImageFont.load_default(size=size)     # scalable since Pillow 10.1
            except TypeError:
                f = ImageFont.load_default()
        _FONT_CACHE[size] = f
    return _FONT_CACHE[size]


def _fonts():
    """(title font, label font) at scale 1."""
    return font(TITLE_FONT_SIZE), font(LABEL_FONT_SIZE)


def frame_scale(size):
    """How much the band, fonts and label box grow for an image of size (w, h): 1 up to
    REF_VIEW, then the larger of w / REF_VIEW[0] and h / REF_VIEW[1]."""
    w, h = size
    return max(1.0, w / REF_VIEW[0], h / REF_VIEW[1])


def frame_layout(size):
    """Band height, fonts and positions for the frame of an image of size (w, h)."""
    s = frame_scale(size)
    title_px = round(TITLE_FONT_SIZE * s)
    label_px = round(LABEL_FONT_SIZE * s)
    pad_x, pad_y, gap = round(BAND_PAD_X * s), round(BAND_PAD_Y * s), round(ROW_GAP * s)
    title_h = round(title_px * 1.2)                   # line height: room for descenders
    box_h = round(label_px * 1.2) + 2 * round(LABEL_PAD_Y * s)
    label_top = pad_y + title_h + gap
    return {"scale": s, "band": label_top + box_h + pad_y,
            "title_font": font(title_px), "label_font": font(label_px),
            "title_pos": (pad_x, pad_y), "title_room": max(1, size[0] - 2 * pad_x),
            "label_left": pad_x, "label_top": label_top, "label_box_h": box_h,
            "label_pad": (round(LABEL_PAD_X * s), round(LABEL_PAD_Y * s)),
            "label_px": label_px}


def band_height(size):
    """Height of the title band above an image of size (w, h)."""
    return frame_layout(size)["band"]


def fit_text(text, width, fnt, keep="end"):
    """text shortened with '...' so it fits width pixels at fnt; keep='end' drops characters
    from the start (paths: the file name stays), keep='start' from the end."""
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    if d.textlength(text, font=fnt) <= width:
        return text
    while text and d.textlength("..." + text if keep == "end" else text + "...", font=fnt) > width:
        text = text[1:] if keep == "end" else text[:-1]
    return "..." + text if keep == "end" else text + "..."


def _runs(mask):
    runs, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def _labels_from_name(stem, n):
    toks = stem.split("_")
    return toks[-n:] if len(toks) > n else [str(i + 1) for i in range(n)]


def _safe(s):
    return re.sub(r"[^A-Za-z0-9-]+", "-", s).strip("-")


def panels_of(sheet):
    """[(label, PIL frame)] for one stacked sheet, plus its background colour."""
    im = Image.open(sheet).convert("RGB")
    a = np.asarray(im).astype(np.int16)
    bg = a[0, 0]
    uniform = (np.abs(a - bg).max(axis=2) <= BG_TOLERANCE).all(axis=1)   # full-width background rows
    min_panel = max(MIN_PANEL_PX, im.width // MIN_PANEL_WIDTH_DIV)
    panels = [(s, e) for s, e in _runs(~uniform) if e - s >= min_panel]
    if len(panels) < 2:
        sys.exit(f"{sheet}: found {len(panels)} panels, not a stacked sheet "
                 "(pixel (0, 0) must be background and panels must be separated by "
                 "full-width background rows; use 'label' for single renders)")
    h = max(e - s for s, e in panels)
    bgc = tuple(int(c) for c in bg)
    title = im.crop((0, 0, im.width, panels[0][0]))                  # title band incl. gap
    labels = _labels_from_name(Path(sheet).stem, len(panels))
    frames = []
    for (s, _), lab in zip(panels, labels):
        f = Image.new("RGB", (im.width, title.height + h), bgc)
        f.paste(title, (0, 0))
        f.paste(im.crop((0, s, im.width, min(s + h, im.height))), (0, title.height))
        frames.append((lab, f))
    return frames, bgc


def split(sheets, out):
    out = Path(os.path.abspath(out))
    sheets = [Path(os.path.abspath(x)) for x in sheets]
    views = [(Path(s), *panels_of(s)) for s in sheets]
    W = max(f.width for _, fr, _ in views for _, f in fr)
    H = max(f.height for _, fr, _ in views for _, f in fr)
    out.mkdir(parents=True, exist_ok=True)
    paths, starts, n = [], [], 0
    for sheet, frames, bgc in views:
        starts.append({"frame": n + 1, "sheet": str(sheet), "labels": [lab for lab, _ in frames]})
        for lab, f in frames:
            n += 1
            if f.size != (W, H):                                      # pad, centred
                p = Image.new("RGB", (W, H), bgc)
                p.paste(f, ((W - f.width) // 2, (H - f.height) // 2))
                f = p
            path = out / f"{sheet.stem}__{_safe(lab)}__{n}.png"
            f.save(path)
            paths.append(path)
    (out / "frames.json").write_text(json.dumps(
        {"size": [W, H], "frames": [str(p) for p in paths], "views": starts}, indent=1))
    return paths


def labelled_frame(im, title_text, text, layout=None):
    """One RGB frame: a title band above im holding title_text and, under it, a label box
    with text; nothing is drawn over the image. Sizes follow frame_layout(im.size). A title
    or label too long for the width is shortened with '...'. Shared by 'label' and the other
    frame writers (compare_dirs.py, review_set.py) so every frame in a review looks the same."""
    im = im.convert("RGB")
    lay = layout if isinstance(layout, dict) else frame_layout(im.size)
    band = lay["band"]
    frame = Image.new("RGB", (im.width, im.height + band), BG)
    frame.paste(im, (0, band))
    d = ImageDraw.Draw(frame)
    d.text(lay["title_pos"], fit_text(title_text, lay["title_room"], lay["title_font"]),
           fill=TITLE_TEXT_COLOR, font=lay["title_font"])
    px, py = lay["label_pad"]
    room = lay["title_room"] - 2 * px
    size, fnt = lay["label_px"], lay["label_font"]
    while size > MIN_LABEL_FONT_SIZE and d.textlength(text, font=fnt) > room:
        size -= 1
        fnt = font(size)
    text = fit_text(text, room, fnt, keep="start")
    left, top, box_h = lay["label_left"], lay["label_top"], lay["label_box_h"]
    tw = d.textlength(text, font=fnt)
    d.rectangle([left, top, left + tw + 2 * px, top + box_h - 1], fill=LABEL_BOX_COLOR)
    asc, desc = fnt.getmetrics() if hasattr(fnt, "getmetrics") else (size, 0)
    d.text((left + px, top + (box_h - asc - desc) / 2), text, fill=LABEL_TEXT_COLOR, font=fnt)
    return frame


def label(title_text, items, out):
    out = Path(os.path.abspath(out))
    out.mkdir(parents=True, exist_ok=True)
    paths, size = [], None
    for i, (img, text) in enumerate(items, 1):
        im = Image.open(img).convert("RGB")
        if size and im.size != size:
            sys.exit(f"{img}: size {im.size} differs from {size}; frames would not line up "
                     "(re-render or resize so every image has the same size)")
        size = im.size
        frame = labelled_frame(im, title_text, text)
        p = out / f"{Path(img).stem}__{_safe(text)}__{i}.png"
        frame.save(p)
        paths.append(p)
    return paths


def _item(value):
    if "=" not in value:
        raise argparse.ArgumentTypeError(f"expected IMAGE=LABEL, got {value!r}")
    return tuple(value.rsplit("=", 1))


def build_parser():
    ap = argparse.ArgumentParser(
        prog="sheet_panels.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="{split,label}")
    s = sub.add_parser(
        "split", help="split stacked sheets into labelled frames and write frames.json",
        description="Split stacked comparison sheets into one labelled, equal-size frame per "
                    "panel, in the order given, and write DIR/frames.json (frame paths, "
                    "frame size and the first frame of each view).",
        epilog="example:\n  python sheet_panels.py split shot010_side_before_after.png "
               "shot010_top_before_after.png --out review/rv_frames\n\n"
               "Pass the sheets in viewing order; do not glob or sort (v10 sorts before v9).\n"
               "Panel labels are the last _-separated tokens of each sheet's file name.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("sheets", nargs="+", metavar="SHEET",
                   help="stacked sheet image (PNG), in viewing order")
    s.add_argument("--out", required=True, metavar="DIR",
                   help="folder for the frames and frames.json (created if missing)")
    lab = sub.add_parser(
        "label", help="add a title band and label box to unstacked renders",
        description="Add a title band and a label box to renders that were never stacked, so "
                    "they flip like split sheet frames. All images must be the same size.",
        epilog='example:\n  python sheet_panels.py label --title "shot010: key light" '
               '--out review/rv_frames before.png=before after.png=after',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    lab.add_argument("--title", required=True, metavar="TEXT",
                     help="text for the title band on every frame")
    lab.add_argument("--out", required=True, metavar="DIR",
                     help="folder for the labelled frames (created if missing)")
    lab.add_argument("items", nargs="+", type=_item, metavar="IMAGE=LABEL",
                     help="image path and the label to burn on it, in viewing order")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.cmd == "split":
        paths = split(a.sheets, Path(a.out))
    else:
        paths = label(a.title, a.items, Path(a.out))
    for p in paths:
        print(p)


if __name__ == "__main__":
    main()
