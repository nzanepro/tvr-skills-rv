"""Build a review set: the same screen (or page, shot, icon) in several variants, back to back.

    review_set.py VARIANT_DIR VARIANT_DIR ... --out DIR
    review_set.py ROOT --out DIR            every sub-folder of ROOT is a variant

Each variant is a folder holding one image per screen; files pair up by relative path
(--match stem ignores the extension). For every screen the frames are its variants in the
order below, all padded to one size, labelled with the variant name in a title band that also
names the screen; a variant without that screen gets a "missing" placeholder so the order
never shifts. frames.json has one group per screen, so the launcher puts a mark at each
screen: Left / Right flips variants, Alt+Left / Alt+Right jumps between screens.

Order (--order a,b,c always wins):
    variants  folders passed one by one keep the order given. Sub-folders of ROOT are sorted
              naturally (v1, v2, v10), except that known pairs go in review order (before,
              after / baseline, candidate / old, new / expected, actual / reference, actual)
              and breakpoint names by width, when every sub-folder name belongs to the set.
    screens   natural order by name, except that web_capture.py names <page>__<breakpoint>
              (and their __t01 tiles) sort each page's breakpoints by width: a known name
              (xs, sm, md, lg, xl, 2xl, phone, mobile, tablet, laptop, desktop, wide) or a
              width in the name (w375, 1440, 768x1024).

Typical sets (the capture layouts are in references/ui-app-web.md):
    devices      caps/iphone-se/  caps/iphone-15-pro-max/  caps/pixel-7/
    appearance   caps/light/  caps/dark/  caps/high-contrast/
    text size    caps/text-100/  caps/text-130/  caps/text-200/
    locales      screenshots/en-US/  screenshots/de-DE/  screenshots/ar-SA/   (fastlane layout)
    states       caps/empty/  caps/loading/  caps/error/  caps/long-content/
    web          caps/before/  caps/after/   (web_capture.py: <page>__<breakpoint>.png)
    breakpoints  caps/after/mobile/  caps/after/tablet/  caps/after/desktop/   (--group-by breakpoint)

For two variants with difference images use compare_dirs.py instead.
Writes the frames and DIR/frames.json; prints one JSON line. Exit 0 ok, 2 error.
"""
import os
import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(os.path.abspath(__file__)).parent))
import compare_dirs as cd  # noqa: E402
import sheet_panels as sp  # noqa: E402

# Variant names with a review order of their own (compared case-insensitively): when every
# sub-folder of ROOT belongs to one sequence, they go in its order instead of alphabetically.
VARIANT_SEQUENCES = (("before", "after"), ("baseline", "candidate"), ("old", "new"),
                     ("expected", "actual"), ("reference", "actual"))

# Breakpoint names used by web_capture.py and CSS frameworks, with a typical width for sorting.
BREAKPOINT_WIDTHS = {"xs": 360, "sm": 640, "md": 768, "lg": 1024, "xl": 1280, "2xl": 1536,
                     "xxl": 1536, "phone": 375, "mobile": 375, "tablet": 768, "laptop": 1280,
                     "desktop": 1440, "wide": 1920}
_WIDTH_NAME = re.compile(r"[a-z-]*?(\d{3,4})(?:x\d{3,4})?")   # w375, 1440, 768x1024, bp-1280
_TILE = re.compile(r"t\d+")                                   # web_capture.py --tile-height


def breakpoint_width(name):
    """Width for a breakpoint name (mobile, lg, w375, 1440, 768x1024), else None."""
    n = str(name).lower()
    if n in BREAKPOINT_WIDTHS:
        return BREAKPOINT_WIDTHS[n]
    m = _WIDTH_NAME.fullmatch(n)
    return int(m.group(1)) if m else None


def order_names(names):
    """Names in review order: a known sequence (before, after ...) or breakpoints by width
    when every name belongs to it, otherwise natural order (v1, v2, v10)."""
    names = sorted(names, key=cd.natural_key)
    low = [n.lower() for n in names]
    if len(set(low)) == len(low):
        for seq in VARIANT_SEQUENCES:
            if all(n in seq for n in low):
                return sorted(names, key=lambda n: seq.index(n.lower()))
    widths = [breakpoint_width(n) for n in names]
    if names and all(w is not None for w in widths):
        return [n for _, n in sorted(zip(widths, names), key=lambda t: (t[0], cd.natural_key(t[1])))]
    return names


def screen_key(screen):
    """Sort key for a screen path: natural by name, but <page>__<breakpoint>[__tNN] sorts by
    page, then breakpoint width, then tile."""
    folder, _, base = cd.strip_ext(screen).rpartition("/")
    parts = base.split("__")
    for i in range(len(parts) - 1, 0, -1):
        w = breakpoint_width(parts[i])
        if w is not None and all(_TILE.fullmatch(t) for t in parts[i + 1:]):
            return (cd.natural_key(folder), cd.natural_key("__".join(parts[:i])), w,
                    cd.natural_key(screen))
    return (cd.natural_key(folder), cd.natural_key(base), -1, cd.natural_key(screen))


def variants_from(paths, order=None):
    """[(name, folder)] from the given folders, or from the sub-folders of a single ROOT."""
    paths = [Path(p) for p in paths]
    for p in paths:
        if not p.is_dir():
            raise cd.CompareError(f"not a folder: {p}")
    if len(paths) == 1:
        subs = {d.name: d for d in paths[0].iterdir()
                if d.is_dir() and not d.name.startswith((".", "_"))}
        subs = [subs[n] for n in order_names(subs)]
        if not subs:
            raise cd.CompareError(f"{paths[0]} has no sub-folders; give the variant folders "
                                  f"themselves, or a ROOT whose sub-folders are the variants")
        paths = subs
    out = [(p.name, p) for p in paths]
    if order:
        names = [n.strip() for n in order.split(",") if n.strip()]
        known = {n: p for n, p in out}
        missing = [n for n in names if n not in known]
        if missing:
            raise cd.CompareError(f"--order names {missing} are not variants; have {list(known)}")
        out = [(n, known[n]) for n in names]
    if len({n for n, _ in out}) != len(out):
        raise cd.CompareError("two variants have the same folder name; use --labels")
    return out


def collect(variants, exts=cd.IMAGE_EXTS, include=(), exclude=(), match="exact"):
    """(screens in review order (screen_key), {screen: {variant: path}})."""
    table = {}
    for name, folder in variants:
        for rel, path in cd.list_images(folder, exts, include, exclude).items():
            table.setdefault(cd._key(rel, match), {})[name] = path
    return sorted(table, key=screen_key), table


def build(variants, out, title="", labels=None, exts=cd.IMAGE_EXTS, include=(), exclude=(),
          match="exact", anchor="top-left", svg_opts=None, skip_incomplete=False):
    out = Path(os.path.abspath(out))
    out.mkdir(parents=True, exist_ok=True)
    screens, table = collect(variants, exts, include, exclude, match)
    if not screens:
        raise cd.CompareError("no images found in the variant folders (check --ext / --include)")
    names = [n for n, _ in variants]
    labels = labels or names
    frames, views, groups, items = [], [], [], []
    n = 0
    for si, screen in enumerate(screens, 1):
        row = table[screen]
        if skip_incomplete and len(row) < len(names):
            continue
        arrays = {}
        for v in names:
            p = row.get(v)
            if p and Path(p).suffix.lower() == ".svg":
                import rasterize as rz
                target = out / "_raster" / v / (cd.safe_name(screen) + ".png")
                target.parent.mkdir(parents=True, exist_ok=True)
                p = rz.rasterize(p, target, scale=(svg_opts or {}).get("scale", 1.0),
                                 backend=(svg_opts or {}).get("backend"))["out"]
            arrays[v] = cd.load_rgba(p) if p else None
        present = [a for a in arrays.values() if a is not None]
        size = (max(a.shape[1] for a in present), max(a.shape[0] for a in present))
        gid = f"s{si}"
        views.append({"frame": n + 1, "sheet": screen, "labels": list(labels)})
        groups.append({"id": gid, "label": screen,
                       "meta": {"screen": screen, "variants": {v: row.get(v) for v in names}}})
        head = f"{title}  " if title else ""
        for v, lab in zip(names, labels):
            a = arrays[v]
            if a is None:
                im = cd.placeholder(size, f"missing in {v}")
            else:
                padded, valid = cd.pad_to(a, size, anchor)
                im = Image.fromarray(cd.flatten(padded, valid))
            n += 1
            lay = sp.frame_layout(im.size)
            text = cd.fit_text(f"{head}{screen}", lay["title_room"], lay["title_font"])
            frame = sp.labelled_frame(im, text, lab, lay)
            path = out / f"{cd.safe_name(cd.strip_ext(screen))}__{cd.safe_name(lab)}__{n}.png"
            frame.save(path)
            frames.append(str(path))
            items.append({"path": str(path), "label": lab, "group": gid,
                          "meta": {"screen": screen, "variant": v, "source": row.get(v)}})
    manifest = {"schema": "rv-review.manifest", "schema_version": 1, "kind": "review_set",
                "title": title or "review set", "layout": "sequence", "marks": "groups",
                "groups": groups, "items": items, "frames": frames, "views": views}
    (out / "frames.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    missing = {s: [v for v in names if v not in table[s]] for s in screens
               if len(table[s]) < len(names)}
    return {"ok": True, "variants": names, "screens": len(views), "frames": len(frames),
            "frames_json": str(out / "frames.json"), "missing": missing}


def build_parser():
    ap = argparse.ArgumentParser(prog="review_set.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="examples:\n"
                                        "  python review_set.py caps --out review/devices\n"
                                        "  python review_set.py caps --out review/versions        # caps/before, caps/after: before first\n"
                                        "  python review_set.py caps/light caps/dark --out review/appearance\n"
                                        "  python review_set.py screenshots --order en-US,de-DE,ar-SA --out review/locales\n"
                                        "then: python rv_review.py --frames-json review/devices/frames.json")
    ap.add_argument("folders", nargs="+", metavar="DIR", help="variant folders, or one ROOT")
    ap.add_argument("--out", required=True, metavar="DIR", help="folder for frames and frames.json")
    ap.add_argument("--order", metavar="A,B,...",
                    help="variant order by folder name (wins over the automatic order)")
    ap.add_argument("--labels", metavar="A,B,...", help="frame labels, one per variant")
    ap.add_argument("--title", metavar="TEXT", help="prefix for every frame title")
    ap.add_argument("--ext", metavar="png,jpg,...", help="file extensions to use")
    ap.add_argument("--include", action="append", default=[], metavar="GLOB")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    ap.add_argument("--match", choices=("exact", "stem"), default="exact")
    ap.add_argument("--anchor", choices=("top-left", "center"), default="top-left")
    ap.add_argument("--skip-incomplete", action="store_true",
                    help="leave out screens missing from any variant")
    ap.add_argument("--svg-scale", type=float, default=1.0, help="scale for SVG inputs")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        variants = variants_from(a.folders, a.order)
        labels = [x.strip() for x in a.labels.split(",")] if a.labels else None
        if labels and len(labels) != len(variants):
            raise cd.CompareError(f"--labels gives {len(labels)} names for {len(variants)} variants")
        exts = tuple("." + e.strip(".").lower() for e in a.ext.split(",")) if a.ext else cd.IMAGE_EXTS
        res = build(variants, a.out, a.title or "", labels, exts, a.include, a.exclude, a.match,
                    a.anchor, {"scale": a.svg_scale}, a.skip_incomplete)
    except (cd.CompareError, OSError) as e:
        print(f"review_set: {e}", file=sys.stderr)
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
