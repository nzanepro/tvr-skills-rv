"""Compare a baseline and a candidate (folders, single files, or a test tool's output) and
write labelled frames of every changed pair for review in RV.

    compare_dirs.py BASELINE CANDIDATE --out DIR           two folders, paired by relative path
    compare_dirs.py design.png build.png --out DIR         two files: one pair
    compare_dirs.py --adapter playwright ROOT --out DIR    a test tool's output folder

For every pair it measures the difference (on 8-bit RGBA, colour premultiplied by alpha so
invisible pixels do not count):
    mean_abs          mean absolute difference over all pixels and channels, 0-1
    max_abs           largest single-channel difference, 0-1
    changed_fraction  share of pixels whose largest channel difference is above --threshold
                      (8-bit levels, default 0: any difference counts)
    bbox              [x0, y0, x1, y1] around the changed pixels
and sorts pairs as added / removed / identical / within tolerance / changed. Identical and
within-tolerance pairs are left out of the frames unless --all is given; changed pairs come
first, most changed first, then added and removed ones.

For each pair in the review it writes frames with the same title band and label box as
sheet_panels.py: baseline, candidate, and an absolute-difference image (both directions, so
it shows what RV's one-sided difference view hides; amplified by --gain and false-coloured
over a dimmed copy of the candidate, so a one-level change is still visible). --overlay adds
a 50 % blend, --tool-diff the test tool's own diff image. All frames of a pair have one size:
mismatched images are padded (--anchor top-left, or center) with a grey checker, the padding
counts as changed, and the pair is flagged size_mismatch. --resize candidate scales the
candidate to the baseline's size instead (design export at 2x vs a 1x screenshot). SVG
inputs are rasterised first with rasterize.py (same folder) at the other side's size.

Adapters read the folders test tools write (details in references/compare-dirs.md):
    playwright  test-results/**/<name>-expected.png vs <name>-actual.png (+ -diff.png)
    jest        __image_snapshots__/<id>.png vs __received_output__/<id>-received.png, or the
                third panel of __diff_output__/<id>-diff.png
    unity       Assets/ReferenceImages/<space>/<platform>/<api>/... vs Assets/ActualImages/...
                (+ <name>.diff.png)
    unreal      image paths found in the automation report JSON (index.json) under ROOT;
                the report format is not publicly documented, see the reference
    flutter     failures/<name>_masterImage.png vs <name>_testImage.png (+ _isolatedDiff.png)

Writes in --out: the frames, frames.json (a review manifest the launcher loads with
--frames-json or --manifest: one group per pair, marks at each pair), compare_report.json
and compare_report.md. Prints one JSON line with the counts and paths.

Exit status: 0 no changes, 1 changes found (changed, added or removed pairs), 2 error.
"""
import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(os.path.abspath(__file__)).parent))
import sheet_panels as sp  # noqa: E402

REPORT_SCHEMA = "rv-review.compare"
REPORT_VERSION = 1
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif", ".svg")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
DEFAULT_GAIN = 8.0            # a 1 / 8 change in value already reaches the top of the colour ramp
HEAT_FLOOR = 0.25             # any changed pixel starts a quarter up the ramp, so it stays visible
DEFAULT_CONTEXT = 0.3         # brightness of the dimmed candidate behind the diff colours
PAD_CHECK = 16                # checker square size for padding and missing images
PAD_COLORS = ((52, 52, 52), (68, 68, 68))
TRANSPARENT_CHECK = ((204, 204, 204), (255, 255, 255))   # behind transparent pixels
HEAT_RAMP = (                  # position, colour: purple -> red -> orange -> pale yellow
    (0.00, (0, 0, 0)), (0.25, (70, 20, 160)), (0.50, (220, 40, 70)),
    (0.75, (255, 150, 0)), (1.00, (255, 255, 150)))
SIGNED_UP = (0, 220, 255)      # cyan: candidate brighter than baseline
SIGNED_DOWN = (255, 0, 200)    # magenta: candidate darker
EXIT_SAME, EXIT_CHANGED, EXIT_ERROR = 0, 1, 2


class CompareError(RuntimeError):
    """A problem the caller can fix; the message says how."""


# --- files and pairing ------------------------------------------------------------------

def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def list_images(root, exts=IMAGE_EXTS, include=(), exclude=()):
    """{relative posix path: absolute path} of the image files under root."""
    root = Path(root)
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in filenames:
            if Path(f).suffix.lower() not in exts:
                continue
            p = Path(dirpath) / f
            rel = p.relative_to(root).as_posix()
            if include and not any(fnmatch.fnmatch(rel, g) for g in include):
                continue
            if any(fnmatch.fnmatch(rel, g) for g in exclude):
                continue
            out[rel] = str(p)
    return out


def _key(rel, match):
    return str(Path(rel).with_suffix("").as_posix()) if match == "stem" else rel


def pair_dirs(baseline, candidate, exts=IMAGE_EXTS, include=(), exclude=(), match="exact"):
    """[{key, baseline, candidate}] for two folders; a side is None when the file is missing.

    match: 'exact' pairs identical relative paths; 'stem' ignores the extension, so
    design/home.svg pairs with build/home.png."""
    a = list_images(baseline, exts, include, exclude)
    b = list_images(candidate, exts, include, exclude)
    ka = {_key(r, match): p for r, p in a.items()}
    kb = {_key(r, match): p for r, p in b.items()}
    keys = sorted(set(ka) | set(kb), key=natural_key)
    return [{"key": k, "baseline": ka.get(k), "candidate": kb.get(k)} for k in keys]


def _walk_files(root, predicate):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            if predicate(f):
                yield Path(dirpath) / f


def adapter_playwright(root):
    """Playwright toHaveScreenshot / toMatchSnapshot failures: in test-results, every
    <name>-actual.png next to <name>-expected.png (and <name>-diff.png)."""
    root = Path(root)
    pairs = []
    for actual in _walk_files(root, lambda f: f.endswith("-actual.png")):
        stem = actual.name[:-len("-actual.png")]
        expected = actual.with_name(stem + "-expected.png")
        diff = actual.with_name(stem + "-diff.png")
        rel = actual.parent.relative_to(root).as_posix()
        pairs.append({"key": f"{rel}/{stem}" if rel != "." else stem,
                      "baseline": str(expected) if expected.is_file() else None,
                      "candidate": str(actual), "tool_diff": str(diff) if diff.is_file() else None})
    return sorted(pairs, key=lambda p: natural_key(p["key"]))


def split_composite(path, reference_size, out_dir):
    """Third panel (received) of a jest-image-snapshot composite (baseline | diff | received,
    side by side by default, stacked with diffDirection 'vertical'). Returns a written PNG
    path, or None when the composite is not three panels of the baseline's size."""
    im = Image.open(path)
    w, h = reference_size
    if im.size == (3 * w, h):
        box = (2 * w, 0, 3 * w, h)
    elif im.size == (w, 3 * h):
        box = (0, 2 * h, w, 3 * h)
    else:
        return None
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out = Path(out_dir) / (Path(path).stem + "-received-from-composite.png")
    im.crop(box).save(out)
    return str(out)


def adapter_jest(root, work_dir):
    """jest-image-snapshot: failing snapshots only (those with a received image or a diff).
    Baselines are __image_snapshots__/<id>.png; storeReceivedOnFailure writes
    __received_output__/<id>-received.png; the diff is __diff_output__/<id>-diff.png."""
    root = Path(root)
    def snap_dir_of(p):
        # outputs sit in __image_snapshots__/__received_output__ (or a sibling folder):
        # key them by their snapshot folder so equal ids in two packages stay apart
        up = p.parent.parent
        return up if up.name == "__image_snapshots__" else up / "__image_snapshots__"
    received = {(snap_dir_of(p), p.name[:-len("-received.png")]): p
                for p in _walk_files(root, lambda f: f.endswith("-received.png"))
                if p.parent.name == "__received_output__"}
    diffs = {(snap_dir_of(p), p.name[:-len("-diff.png")]): p
             for p in _walk_files(root, lambda f: f.endswith("-diff.png"))
             if p.parent.name == "__diff_output__"}
    pairs = []
    for snap_dir in sorted({p.parent for p in _walk_files(root, lambda f: f.endswith(".png"))
                            if p.parent.name == "__image_snapshots__"}):
        for base in sorted(snap_dir.glob("*.png"), key=natural_key):
            ident = base.stem
            rec, diff = received.get((snap_dir, ident)), diffs.get((snap_dir, ident))
            if not rec and not diff:
                continue                                   # passing snapshot
            cand = str(rec) if rec else None
            note = None
            if not cand and diff:
                with Image.open(base) as b:
                    cand = split_composite(diff, b.size, Path(work_dir) / "_extracted")
                if not cand:
                    note = ("could not split the diff composite; set storeReceivedOnFailure: true "
                            "to get the received image")
            rel = snap_dir.parent.relative_to(root).as_posix()
            pairs.append({"key": f"{rel}/{ident}" if rel != "." else ident, "baseline": str(base),
                          "candidate": cand, "tool_diff": str(diff) if diff else None,
                          "note": note})
    return pairs


def adapter_unity(root):
    """Unity Graphics Test Framework: Assets/ActualImages/<space>/<platform>/<api>/<test>.png
    (and <test>.diff.png) against the same path under Assets/ReferenceImages; a reference with
    the same file name elsewhere under ReferenceImages is used when the exact path is missing."""
    root = Path(root)
    assets = root / "Assets" if (root / "Assets").is_dir() else root
    actual_root, ref_root = assets / "ActualImages", assets / "ReferenceImages"
    if not actual_root.is_dir():
        raise CompareError(f"{actual_root} not found: run the graphics tests first, or pass the "
                           f"Unity project folder (the one holding Assets/)")
    refs = list(_walk_files(ref_root, lambda f: f.lower().endswith(".png"))) if ref_root.is_dir() else []
    pairs = []
    for actual in _walk_files(actual_root, lambda f: f.lower().endswith(".png")
                              and not f.lower().endswith((".diff.png", ".expected.png"))):
        rel = actual.relative_to(actual_root)
        ref = ref_root / rel
        if not ref.is_file():
            same = [r for r in refs if r.name == actual.name]
            # prefer the reference whose folder path shares the most trailing parts
            ref = max(same, key=lambda r: sum(1 for x, y in zip(reversed(r.parent.parts),
                                                                 reversed(actual.parent.parts)) if x == y),
                      default=None)
        expected_copy = actual.with_name(actual.stem + ".expected.png")
        if (not ref or not Path(ref).is_file()) and expected_copy.is_file():
            ref = expected_copy                # some versions copy the reference next to it
        diff = actual.with_name(actual.stem + ".diff.png")
        pairs.append({"key": rel.with_suffix("").as_posix(),
                      "baseline": str(ref) if ref and Path(ref).is_file() else None,
                      "candidate": str(actual), "tool_diff": str(diff) if diff.is_file() else None})
    return sorted(pairs, key=lambda p: natural_key(p["key"]))


UNREAL_BASE_KEYS = ("approved", "groundtruth", "ground_truth", "expected", "reference")
UNREAL_CAND_KEYS = ("unapproved", "incoming", "actual", "incomingfile")
UNREAL_DIFF_KEYS = ("difference", "delta", "diff")
UNREAL_NAME_KEYS = ("name", "testDisplayName", "fullTestPath", "screenshotName", "shotName")


def _find_image_triples(obj, found, context_name=None):
    if isinstance(obj, dict):
        name = next((obj[k] for k in UNREAL_NAME_KEYS if isinstance(obj.get(k), str)), context_name)
        flat = {}
        for k, v in obj.items():
            if isinstance(v, str):
                flat[k.lower().replace("_", "")] = v
            elif isinstance(v, dict):
                for k2, v2 in v.items():
                    if isinstance(v2, str):
                        flat.setdefault(k2.lower().replace("_", ""), v2)

        def pick(keys):
            for k in keys:
                v = flat.get(k.replace("_", ""))
                if v and Path(v).suffix.lower() in IMAGE_EXTS:
                    return v
            return None
        b, c = pick(UNREAL_BASE_KEYS), pick(UNREAL_CAND_KEYS)
        if b or c:
            found.append({"name": name, "baseline": b, "candidate": c, "diff": pick(UNREAL_DIFF_KEYS)})
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _find_image_triples(v, found, name)
    elif isinstance(obj, list):
        for v in obj:
            _find_image_triples(v, found, context_name)


def adapter_unreal(root):
    """Unreal automation screenshot comparisons, read from the report JSON (index.json from
    -ReportExportPath) rather than guessed folders: any object in it that names an approved /
    ground-truth image and an unapproved / incoming one (plus difference / delta). Paths are
    tried relative to the JSON's folder, then ROOT."""
    root = Path(root)
    reports = [p for p in _walk_files(root, lambda f: f.lower() == "index.json")]
    if not reports:
        reports = [p for p in _walk_files(root, lambda f: f.lower().endswith(".json"))
                   if p.stat().st_size < 50_000_000]
    pairs, seen = [], set()
    for rep in reports:
        try:
            data = json.loads(rep.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        found = []
        _find_image_triples(data, found)

        def res(p):
            if not p:
                return None
            for base in (rep.parent, root):
                q = (base / p) if not Path(p).is_absolute() else Path(p)
                if q.is_file():
                    return str(q)
            return None
        for f in found:
            b, c, d = res(f["baseline"]), res(f["candidate"]), res(f["diff"])
            if (b, c) in seen or not (b or c):
                continue
            seen.add((b, c))
            key = f["name"] or Path(c or b).stem
            pairs.append({"key": f"{key}" if key not in {p['key'] for p in pairs} else f"{key}_{len(pairs)}",
                          "baseline": b, "candidate": c, "tool_diff": d,
                          "note": None if (b and c) else "report names an image that is not on disk"})
    if not pairs:
        raise CompareError(f"no screenshot comparisons found in report JSON under {root}. Run the "
                           f"tests with -ReportExportPath=<folder> and pass that folder; see "
                           f"references/compare-dirs.md")
    return pairs


def adapter_flutter(root):
    """Flutter golden tests (matchesGoldenFile): failures/<name>_masterImage.png vs
    <name>_testImage.png, with <name>_isolatedDiff.png as the tool diff."""
    root = Path(root)
    pairs = []
    for master in _walk_files(root, lambda f: f.endswith("_masterImage.png")):
        if master.parent.name != "failures":
            continue
        stem = master.name[:-len("_masterImage.png")]
        test = master.with_name(stem + "_testImage.png")
        diff = master.with_name(stem + "_isolatedDiff.png")
        try:
            rel = master.parent.parent.relative_to(root).as_posix()
        except ValueError:                  # ROOT is the failures folder itself
            rel = "."
        pairs.append({"key": f"{rel}/{stem}" if rel != "." else stem, "baseline": str(master),
                      "candidate": str(test) if test.is_file() else None,
                      "tool_diff": str(diff) if diff.is_file() else None})
    return sorted(pairs, key=lambda p: natural_key(p["key"]))


ADAPTERS = ("dirs", "playwright", "jest", "unity", "unreal", "flutter")


# --- images -----------------------------------------------------------------------------

def load_rgba(path):
    """8-bit RGBA array; 16-bit and float greyscale are scaled down, not clipped."""
    with Image.open(path) as im:
        im.load()
        if im.mode in ("I;16", "I;16B", "I;16L", "I"):
            a = np.asarray(im).astype(np.float64)
            # I;16 is 16-bit by definition; plain I (32-bit) is only known to be 16-bit data
            # when values go above 8 bits
            div = 257.0 if im.mode.startswith("I;16") or a.max() > 255 else 1.0
            a = np.clip(a / div, 0, 255).astype(np.uint8)
            return np.dstack([a, a, a, np.full_like(a, 255)])
        if im.mode == "F":
            a = np.clip(np.asarray(im) * 255.0, 0, 255).astype(np.uint8)
            return np.dstack([a, a, a, np.full_like(a, 255)])
        return np.asarray(im.convert("RGBA")).copy()


def checker(h, w, colors=PAD_COLORS, size=PAD_CHECK):
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((yy // size + xx // size) % 2).astype(bool)
    out = np.empty((h, w, 3), np.uint8)
    out[~m] = colors[0]
    out[m] = colors[1]
    return out


def pad_to(a, size, anchor="top-left"):
    """(padded RGBA array, valid mask) at size (w, h); padding is transparent and invalid."""
    w, h = size
    ah, aw = a.shape[:2]
    out = np.zeros((h, w, 4), np.uint8)
    valid = np.zeros((h, w), bool)
    x0, y0 = ((w - aw) // 2, (h - ah) // 2) if anchor == "center" else (0, 0)
    out[y0:y0 + ah, x0:x0 + aw] = a
    valid[y0:y0 + ah, x0:x0 + aw] = True
    return out, valid


def premultiplied(a):
    f = a.astype(np.float32)
    f[..., :3] *= f[..., 3:4] / 255.0
    return f


def metrics(a, b, valid_a=None, valid_b=None, threshold=0):
    """Difference metrics between two RGBA arrays of one size; pixels valid on only one side
    (padding) count as fully changed."""
    h, w = a.shape[:2]
    va = np.ones((h, w), bool) if valid_a is None else valid_a
    vb = np.ones((h, w), bool) if valid_b is None else valid_b
    d = np.abs(premultiplied(a) - premultiplied(b))
    mag = d.max(axis=2)
    one_side = va ^ vb
    mag[one_side] = 255.0
    d[one_side] = 255.0
    changed = mag > threshold
    n = changed.sum()
    ys, xs = np.nonzero(changed)
    return {"mean_abs": round(float(d.mean() / 255.0), 6),
            "max_abs": round(float(mag.max() / 255.0), 6) if mag.size else 0.0,
            "changed_pixels": int(n), "changed_fraction": round(float(n / mag.size), 6) if mag.size else 0.0,
            "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if n else None}, mag


def flatten(a, valid=None):
    """RGB for display: transparency over a light checker, padding as a grey checker."""
    h, w = a.shape[:2]
    alpha = a[..., 3:4].astype(np.float32) / 255.0
    rgb = a[..., :3].astype(np.float32) * alpha + checker(h, w, TRANSPARENT_CHECK) * (1 - alpha)
    rgb = rgb.astype(np.uint8)
    if valid is not None:
        pad = checker(h, w)
        rgb[~valid] = pad[~valid]
    return rgb


def ramp(v):
    """Colour for values 0-1 on the heat ramp, as uint8 RGB."""
    xs = [p for p, _ in HEAT_RAMP]
    return np.stack([np.interp(v, xs, [c[i] for _, c in HEAT_RAMP]) for i in range(3)],
                    axis=-1).astype(np.uint8)


def diff_image(a, b, mag, threshold=0, gain=DEFAULT_GAIN, context=DEFAULT_CONTEXT, mode="abs",
               valid_b=None):
    """False-colour difference over a dimmed candidate. abs: heat ramp by size of change;
    signed: cyan where the candidate is brighter, magenta where darker."""
    base = flatten(b, valid_b).astype(np.float32)
    lum = base @ np.array([0.299, 0.587, 0.114], np.float32)
    out = np.repeat((lum * context)[..., None], 3, axis=2)
    changed = mag > threshold
    v = np.clip(HEAT_FLOOR + (1 - HEAT_FLOOR) * (mag / 255.0) * gain, 0, 1)
    if mode == "signed":
        la = premultiplied(a)[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32)
        lb = premultiplied(b)[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32)
        up = (lb >= la)[..., None]
        col = np.where(up, np.array(SIGNED_UP, np.float32), np.array(SIGNED_DOWN, np.float32))
        col = col * v[..., None]
    else:
        col = ramp(v).astype(np.float32)
    out[changed] = col[changed]
    return out.clip(0, 255).astype(np.uint8)


def placeholder(size, text):
    """A grey checker frame for a missing side, with a short note in the middle."""
    w, h = size
    im = Image.fromarray(checker(h, w))
    d = ImageDraw.Draw(im)
    _, small = sp._fonts()
    tw = d.textlength(text, font=small)
    d.text(((w - tw) / 2, h / 2 - 10), text, fill=(220, 220, 220), font=small)
    return im


def fit_text(text, width, font):
    """text shortened from the left with '...' so it fits width pixels at font."""
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength("..." + text, font=font) > width:
        text = text[1:]
    return "..." + text


def title_text(name, info, width, font):
    """Title band text: the name shortened from the left first, so the status stays readable."""
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    room = width - 2 * sp.TITLE_TEXT_POS[0]
    if d.textlength(name + info, font=font) <= room:
        return name + info
    if d.textlength("..." + name[-6:] + info, font=font) <= room:
        return fit_text(name, room - d.textlength(info, font=font), font) + info
    return fit_text(name + info, room, font)


def strip_ext(key):
    return re.sub(r"\.(png|jpe?g|webp|tiff?|bmp|gif|svg)$", "", key, flags=re.I)


def safe_name(text, limit=80):
    s = re.sub(r"[^A-Za-z0-9-]+", "-", text).strip("-")
    return (s[-limit:] if len(s) > limit else s) or "pair"


def rasterize_pair(pair, out_dir, svg_opts):
    """Rasterise SVG sides of a pair (same size as the other side when that is a bitmap)."""
    import rasterize as rz
    paths = {}
    other = {"baseline": pair.get("candidate"), "candidate": pair.get("baseline")}
    for side in ("baseline", "candidate"):
        p = pair.get(side)
        if not p or Path(p).suffix.lower() != ".svg":
            paths[side] = p
            continue
        size = None
        o = other[side]
        if o and Path(o).suffix.lower() != ".svg":
            with Image.open(o) as im:
                size = im.size
        elif o:
            s1, s2 = rz.svg_size(p), rz.svg_size(o)
            if s1 and s2:
                size = (max(s1[0], s2[0]), max(s1[1], s2[1]))
                size = (round(size[0] * svg_opts.get("scale", 1.0)), round(size[1] * svg_opts.get("scale", 1.0)))
        target = Path(out_dir) / "_raster" / side / (safe_name(pair["key"]) + ".png")
        target.parent.mkdir(parents=True, exist_ok=True)
        res = rz.rasterize(p, target, size=size, scale=svg_opts.get("scale", 1.0),
                           background=svg_opts.get("background", "transparent"),
                           backend=svg_opts.get("backend"))
        paths[side] = str(res["out"])
        pair.setdefault("rasterized", {})[side] = res["backend"]
    return paths


# --- the comparison ---------------------------------------------------------------------

def compare_pair(pair, threshold=0, min_changed=0.0, resize=None, anchor="top-left",
                 svg_opts=None, out_dir=None):
    """Metrics and status for one pair; keeps the loaded arrays for frame writing."""
    r = {"key": pair["key"], "baseline": pair.get("baseline"), "candidate": pair.get("candidate"),
         "tool_diff": pair.get("tool_diff"), "note": pair.get("note")}
    if not r["baseline"] or not r["candidate"]:
        r["status"] = "added" if r["candidate"] else "removed"
        present = r["candidate"] or r["baseline"]
        if present and Path(present).suffix.lower() == ".svg":
            present = rasterize_pair(pair, out_dir, svg_opts or {})["candidate" if r["candidate"] else "baseline"]
        r["_present"] = load_rgba(present) if present else None
        return r
    paths = {"baseline": r["baseline"], "candidate": r["candidate"]}
    if any(Path(p).suffix.lower() == ".svg" for p in paths.values()):
        paths = rasterize_pair(pair, out_dir, svg_opts or {})
        r["rasterized"] = pair.get("rasterized")
    try:
        same_bytes = Path(paths["baseline"]).read_bytes() == Path(paths["candidate"]).read_bytes()
    except OSError as e:
        raise CompareError(f"cannot read {e.filename}: {e.strerror}") from None
    a, b = load_rgba(paths["baseline"]), load_rgba(paths["candidate"])
    r["size_baseline"] = [a.shape[1], a.shape[0]]
    r["size_candidate"] = [b.shape[1], b.shape[0]]
    if resize == "candidate" and a.shape[:2] != b.shape[:2]:
        b = np.asarray(Image.fromarray(b).resize((a.shape[1], a.shape[0]), Image.LANCZOS))
        r["resized"] = "candidate"
    elif resize == "baseline" and a.shape[:2] != b.shape[:2]:
        a = np.asarray(Image.fromarray(a).resize((b.shape[1], b.shape[0]), Image.LANCZOS))
        r["resized"] = "baseline"
    r["size_mismatch"] = a.shape[:2] != b.shape[:2]
    size = (max(a.shape[1], b.shape[1]), max(a.shape[0], b.shape[0]))
    a, va = pad_to(a, size, anchor)
    b, vb = pad_to(b, size, anchor)
    m, mag = metrics(a, b, va, vb, threshold)
    r.update(m)
    if same_bytes or (m["changed_pixels"] == 0 and m["max_abs"] == 0 and not r["size_mismatch"]):
        r["status"] = "identical"
    elif m["changed_fraction"] <= min_changed and not r["size_mismatch"]:
        r["status"] = "within_tolerance"
    else:
        r["status"] = "changed"
    r["_arrays"] = (a, b, va, vb, mag)
    return r


STATUS_ORDER = {"changed": 0, "added": 1, "removed": 2, "within_tolerance": 3, "identical": 4}


def sort_results(results, order="changed"):
    if order == "name":
        return sorted(results, key=lambda r: natural_key(r["key"]))
    # mean_abs weighs how far and how much pixels moved, so a shifted button ranks above a
    # one-level colour change that touches more pixels
    return sorted(results, key=lambda r: (STATUS_ORDER[r["status"]], -r.get("mean_abs", 0),
                                          -r.get("changed_fraction", 0), natural_key(r["key"])))


def write_frames(results, out, labels=("baseline", "candidate"), title="", gain=DEFAULT_GAIN,
                 context=DEFAULT_CONTEXT, mode="abs", threshold=0, overlay=False, tool_diff=False,
                 with_diff=True, include=("changed", "added", "removed")):
    """Labelled frames per pair and the manifest dict (frames.json)."""
    out = Path(os.path.abspath(out))
    out.mkdir(parents=True, exist_ok=True)
    fonts = sp._fonts()
    frames, views, groups, items = [], [], [], []
    size = [0, 0]
    n = 0
    for gi, r in enumerate([r for r in results if r["status"] in include], 1):
        stat = r["status"]
        if stat == "changed":
            info = f"{r['changed_fraction'] * 100:.2f}% px, max {r['max_abs']:.2f}"
            if r.get("size_mismatch"):
                info += ", size differs"
        else:
            info = stat.replace("_", " ")
        # the title names the pair; the numbers go in the diff frame's label box, which
        # stays readable on narrow (phone-width) frames
        title_info = "" if stat == "changed" and not r.get("size_mismatch") else             ("size differs" if stat == "changed" else info)
        pair_frames = []
        if "_arrays" in r:
            a, b, va, vb, mag = r["_arrays"]
            pair_frames.append((labels[0], Image.fromarray(flatten(a, va)), "baseline"))
            pair_frames.append((labels[1], Image.fromarray(flatten(b, vb)), "candidate"))
            if with_diff:
                pair_frames.append((f"diff x{gain:g}" + (" signed" if mode == "signed" else "")
                                    + f"  {r['changed_fraction'] * 100:.2f}% px, max {r['max_abs']:.2f}",
                                    Image.fromarray(diff_image(a, b, mag, threshold, gain, context, mode, vb)),
                                    "diff"))
            if overlay:
                blend = (flatten(a, va).astype(np.uint16) + flatten(b, vb)) // 2
                pair_frames.append(("overlay 50%", Image.fromarray(blend.astype(np.uint8)), "overlay"))
            if tool_diff and r.get("tool_diff"):
                td = Image.open(r["tool_diff"]).convert("RGB")
                canvas = Image.fromarray(checker(a.shape[0], a.shape[1]))
                canvas.paste(td.crop((0, 0, min(td.width, a.shape[1]), min(td.height, a.shape[0]))), (0, 0))
                pair_frames.append(("tool diff", canvas, "tool_diff"))
        elif r.get("_present") is not None:
            p = r["_present"]
            img = Image.fromarray(flatten(p))
            miss = placeholder(img.size, "no baseline" if stat == "added" else "no candidate")
            pair_frames = [(labels[0], miss if stat == "added" else img, "baseline"),
                           (labels[1], img if stat == "added" else miss, "candidate")]
        if not pair_frames:
            continue
        head = f"{title}  " if title else ""
        views.append({"frame": n + 1, "sheet": r["key"], "labels": [lab for lab, _, _ in pair_frames]})
        gid = f"p{gi}"
        meta = {k: v for k, v in r.items() if not k.startswith("_")}
        groups.append({"id": gid, "label": r["key"], "title": info, "meta": meta})
        r["frames"] = {}
        for lab, im, role in pair_frames:
            n += 1
            text = title_text(f"{head}{r['key']}", f"  ({title_info})" if title_info else "",
                              im.width, fonts[0])
            frame = sp.labelled_frame(im, text, lab, fonts)
            path = out / f"{safe_name(strip_ext(r['key']))}__{safe_name(role)}__{n}.png"
            frame.save(path)
            frames.append(str(path))
            r["frames"][role] = str(path)
            items.append({"path": str(path), "label": lab, "group": gid,
                          "meta": {"role": role, "key": r["key"], "source": r.get(role)}})
            size = [max(size[0], frame.width), max(size[1], frame.height)]
        r["first_frame"] = views[-1]["frame"]
    manifest = {"schema": "rv-review.manifest", "schema_version": 1, "kind": "compare",
                "title": title or "compare", "layout": "sequence", "marks": "groups",
                "groups": groups, "items": items,
                "frames": frames, "views": views, "size": size}
    return manifest


def report_markdown(report):
    c = report["counts"]
    lines = [f"# Compare report", "",
             f"- baseline: `{report['baseline']}`", f"- candidate: `{report['candidate']}`",
             f"- adapter: {report['adapter']}, threshold: {report['threshold']} levels",
             "- " + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in c.items()), "",
             "| # | pair | status | changed px | mean | max | size | first frame |",
             "|---|---|---|---|---|---|---|---|"]
    for i, p in enumerate(report["pairs"], 1):
        size = "mismatch" if p.get("size_mismatch") else ""
        cf = f"{p['changed_fraction'] * 100:.3f}%" if "changed_fraction" in p else ""
        mean = f"{p['mean_abs']:.4f}" if "mean_abs" in p else ""
        mx = f"{p['max_abs']:.3f}" if "max_abs" in p else ""
        lines.append(f"| {i} | `{p['key']}` | {p['status']} | {cf} | {mean} | {mx} | {size} | "
                     f"{p.get('first_frame', '')} |")
    return "\n".join(lines) + "\n"


def run(args):
    out = Path(os.path.abspath(args.out))
    svg_opts = {"scale": args.svg_scale, "background": args.svg_background, "backend": args.svg_backend}
    exts = tuple("." + e.strip(".").lower() for e in args.ext.split(",")) if args.ext else IMAGE_EXTS
    if args.adapter == "dirs":
        if len(args.paths) != 2:
            raise CompareError("give a BASELINE and a CANDIDATE (two folders or two files), or "
                               "--adapter NAME with the test output folder")
        base, cand = (Path(p) for p in args.paths)
        for p in (base, cand):
            if not p.exists():
                raise CompareError(f"not found: {p}")
        if base.is_file() and cand.is_file():
            pairs = [{"key": base.stem if base.stem == cand.stem else f"{base.stem} vs {cand.stem}",
                      "baseline": str(Path(os.path.abspath(base))), "candidate": str(Path(os.path.abspath(cand)))}]
        elif base.is_dir() and cand.is_dir():
            pairs = pair_dirs(base, cand, exts, args.include, args.exclude, args.match)
        else:
            raise CompareError("BASELINE and CANDIDATE must both be folders or both be files")
        base_s, cand_s = str(Path(os.path.abspath(base))), str(Path(os.path.abspath(cand)))
    else:
        if len(args.paths) != 1:
            raise CompareError(f"--adapter {args.adapter} takes one folder: the project or the "
                               f"test output folder")
        root = Path(args.paths[0])
        if not root.is_dir():
            raise CompareError(f"not a folder: {root}")
        fn = {"playwright": adapter_playwright, "unity": adapter_unity, "unreal": adapter_unreal,
              "flutter": adapter_flutter}.get(args.adapter)
        pairs = fn(root) if fn else adapter_jest(root, out)
        if args.include:
            pairs = [p for p in pairs if any(fnmatch.fnmatch(p["key"], g) for g in args.include)]
        if args.exclude:
            pairs = [p for p in pairs if not any(fnmatch.fnmatch(p["key"], g) for g in args.exclude)]
        base_s = cand_s = str(Path(os.path.abspath(root)))
    if not pairs:
        raise CompareError("no images found to compare; check the folders and --ext / --include")
    labels = tuple(x.strip() for x in args.labels.split(",")) if args.labels else ("baseline", "candidate")
    if len(labels) != 2:
        raise CompareError("--labels takes two names, e.g. design,build")
    results = [compare_pair(p, args.threshold, args.min_changed, args.resize, args.anchor, svg_opts, out)
               for p in pairs]
    results = sort_results(results, args.order)
    include = ("changed", "added", "removed", "within_tolerance", "identical") if args.all else \
        ("changed", "added", "removed")
    if args.skip_added_removed:
        include = tuple(s for s in include if s not in ("added", "removed"))
    review = [r for r in results if r["status"] in include][:args.max_pairs or None]
    manifest = write_frames(review, out, labels, args.title or "", args.gain, args.context,
                            args.diff_mode, args.threshold, args.overlay, args.tool_diff,
                            not args.no_diff, include)
    counts = {s: sum(1 for r in results if r["status"] == s) for s in STATUS_ORDER}
    report = {"schema": REPORT_SCHEMA, "schema_version": REPORT_VERSION, "adapter": args.adapter,
              "baseline": base_s, "candidate": cand_s, "labels": list(labels),
              "threshold": args.threshold, "min_changed": args.min_changed, "counts": counts,
              "frames_json": str(out / "frames.json"),
              "pairs": [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]}
    (out / "frames.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (out / "compare_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (out / "compare_report.md").write_text(report_markdown(report), encoding="utf-8")
    changes = counts["changed"] + counts["added"] + counts["removed"]
    summary = {"ok": True, "exit_code": EXIT_CHANGED if changes else EXIT_SAME, "counts": counts,
               "frames": len(manifest["frames"]), "pairs_in_review": len(manifest["views"]),
               "frames_json": str(out / "frames.json") if manifest["frames"] else None,
               "report": str(out / "compare_report.json"),
               "most_changed": [r["key"] for r in results if r["status"] == "changed"][:5]}
    return summary


def build_parser():
    ap = argparse.ArgumentParser(prog="compare_dirs.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="examples:\n"
                                        "  python compare_dirs.py shots/baseline shots/candidate --out review/compare\n"
                                        "  python compare_dirs.py design/home.png build/home.png --labels design,build --overlay --out review/home\n"
                                        "  python compare_dirs.py --adapter playwright . --out review/visual\n"
                                        "  python compare_dirs.py icons/v1 icons/v2 --ext svg --svg-scale 4 --out review/icons\n"
                                        "then: python rv_review.py --frames-json review/compare/frames.json")
    ap.add_argument("paths", nargs="+", metavar="PATH",
                    help="BASELINE CANDIDATE (folders or files), or one ROOT with --adapter")
    ap.add_argument("--out", required=True, metavar="DIR", help="folder for frames and reports")
    ap.add_argument("--adapter", choices=ADAPTERS, default="dirs",
                    help="how to find pairs (default dirs: two folders by relative path)")
    ap.add_argument("--ext", metavar="png,jpg,...", help="file extensions to pair "
                    f"(default {','.join(e[1:] for e in IMAGE_EXTS)})")
    ap.add_argument("--include", action="append", default=[], metavar="GLOB",
                    help="only relative paths (or adapter keys) matching this glob; repeatable")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="skip relative paths (or adapter keys) matching this glob; repeatable")
    ap.add_argument("--match", choices=("exact", "stem"), default="exact",
                    help="pair by relative path (exact) or ignoring the extension (stem)")
    ap.add_argument("--threshold", type=float, default=0, metavar="LEVELS",
                    help="per-pixel tolerance in 8-bit levels before a pixel counts as changed "
                         "(default 0; 2-4 absorbs dithering, more for JPEG)")
    ap.add_argument("--min-changed", type=float, default=0.0, metavar="FRACTION",
                    help="pairs with at most this share of changed pixels are 'within tolerance' "
                         "and left out (default 0)")
    ap.add_argument("--all", action="store_true", help="also write frames for identical pairs")
    ap.add_argument("--skip-added-removed", action="store_true",
                    help="leave pairs with a missing side out of the frames")
    ap.add_argument("--order", choices=("changed", "name"), default="changed",
                    help="frames most changed first (default) or by name")
    ap.add_argument("--max-pairs", type=int, metavar="N", help="write frames for the first N pairs only")
    ap.add_argument("--labels", metavar="A,B", help="frame labels (default baseline,candidate)")
    ap.add_argument("--title", metavar="TEXT", help="prefix for every frame title")
    ap.add_argument("--gain", type=float, default=DEFAULT_GAIN, help=f"diff amplification (default {DEFAULT_GAIN:g})")
    ap.add_argument("--context", type=float, default=DEFAULT_CONTEXT,
                    help="brightness of the dimmed candidate behind the diff, 0-1 (default 0.3)")
    ap.add_argument("--diff-mode", choices=("abs", "signed"), default="abs",
                    help="abs: heat colours by size of change; signed: cyan brighter, magenta darker")
    ap.add_argument("--no-diff", action="store_true", help="no difference frame")
    ap.add_argument("--overlay", action="store_true", help="add a 50%% blend frame per pair")
    ap.add_argument("--tool-diff", action="store_true", help="add the test tool's own diff image")
    ap.add_argument("--resize", choices=("none", "candidate", "baseline"), default="none",
                    help="scale one side to the other's size when they differ (design exports)")
    ap.add_argument("--anchor", choices=("top-left", "center"), default="top-left",
                    help="where smaller images sit when padded (default top-left, right for UI)")
    ap.add_argument("--svg-scale", type=float, default=1.0, help="scale for SVG inputs (default 1)")
    ap.add_argument("--svg-background", default="transparent",
                    help="SVG background: transparent (checker in frames) or a colour like #ffffff")
    ap.add_argument("--svg-backend", help="force a rasterize.py backend (default: first found)")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = run(args)
    except (CompareError, OSError) as e:
        msg = str(e)
        print(f"compare_dirs: {msg}", file=sys.stderr)
        print(json.dumps({"ok": False, "exit_code": EXIT_ERROR, "error": msg}))
        return EXIT_ERROR
    except Exception as e:                    # rasteriser missing, unreadable image...: exit 2,
        msg = f"{e.__class__.__name__}: {e}"  # never 1, which means "changes found"
        print(f"compare_dirs: {msg}", file=sys.stderr)
        print(json.dumps({"ok": False, "exit_code": EXIT_ERROR, "error": msg}))
        return EXIT_ERROR
    print(json.dumps(summary))
    return summary["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
