"""Turn stacked contact sheets into equal-size labelled frames for flipping in RV.

split  <sheet.png> [<sheet.png> ...] --out DIR
    For every sheet (in the order given): detect its panels (full-width rows of the sheet
    background colour separate them) and write one PNG per panel = the sheet's title band +
    that panel (the panel keeps its own burnt-in label box).  All frames of all sheets are
    padded (centred, sheet background colour) to the largest frame size so they line up.
    Files are numbered globally, index last (<sheet>__<label>__<N>.png) so RV's frame number
    in the window title equals the sequence frame.  Writes DIR/frames.json (paths, view start
    frames) and prints the frame paths in order, one per line.

label  --title "TEXT" --out DIR  IMG=LABEL [IMG=LABEL ...]
    For unstacked renders: burn the same title band and label box the sheet scripts draw
    (arial 30 title on a 50 px (24,24,24) band, arial 20 white label in a black box).
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BG = (24, 24, 24)


def _fonts():
    try:
        return ImageFont.truetype("arial.ttf", 30), ImageFont.truetype("arial.ttf", 20)
    except OSError:
        f = ImageFont.load_default()
        return f, f


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
    uniform = (np.abs(a - bg).max(axis=2) <= 2).all(axis=1)          # full-width background rows
    min_panel = max(64, im.width // 8)
    panels = [(s, e) for s, e in _runs(~uniform) if e - s >= min_panel]
    if len(panels) < 2:
        sys.exit(f"{sheet}: found {len(panels)} panels, not a stacked sheet")
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
    out = Path(out).resolve()
    sheets = [Path(x).resolve() for x in sheets]
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


def label(title_text, items, out):
    out = Path(out).resolve()
    font, small = _fonts()
    out.mkdir(parents=True, exist_ok=True)
    paths, size = [], None
    for i, (img, text) in enumerate(items, 1):
        im = Image.open(img).convert("RGB")
        if size and im.size != size:
            sys.exit(f"{img}: size {im.size} differs from {size}; frames would not line up")
        size = im.size
        frame = Image.new("RGB", (im.width, im.height + 50), BG)
        d = ImageDraw.Draw(frame)
        d.text((16, 10), title_text, fill=(235, 235, 235), font=font)
        frame.paste(im, (0, 50))
        tw = d.textbbox((20, 64), text, font=small)[2]
        d.rectangle([10, 60, tw + 10, 100], fill=(0, 0, 0))
        d.text((20, 64), text, fill=(255, 255, 255), font=small)
        p = out / f"{Path(img).stem}__{_safe(text)}__{i}.png"
        frame.save(p)
        paths.append(p)
    return paths


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split")
    s.add_argument("sheets", nargs="+")
    s.add_argument("--out", required=True)
    lab = sub.add_parser("label")
    lab.add_argument("--title", required=True)
    lab.add_argument("--out", required=True)
    lab.add_argument("items", nargs="+", help="image=label")
    a = ap.parse_args()
    if a.cmd == "split":
        paths = split(a.sheets, Path(a.out))
    else:
        paths = label(a.title, [tuple(x.rsplit("=", 1)) for x in a.items], Path(a.out))
    for p in paths:
        print(p)


if __name__ == "__main__":
    main()
