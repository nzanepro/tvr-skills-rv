"""Write RV session files (.rv, text GTO) for a review, check them, and render them with rvio.

Standard library only, so it runs outside RV's own Python. A session reopens a review exactly
(rv review.rv) and is what rvio renders headless into a movie or images for people without RV.

write  MANIFEST -o review.rv [--layout L] [--marks auto|groups|none|N,N] [--fps F]
    Build a session from a review manifest (see review_manifest.py) or a frames.json:
      - every item becomes one source (sourceGroupNNNNNN) with its label as the UI name, its
        in / out (cut), fps, view selection (request.imageComponent) and stereo views;
      - a sequence node "review_sequence" plays the items back to back, with marks;
      - layout wipe / difference / difference-inverted / over / replace adds a stack
        "review_stack" of the first two items (a wipe opens split down the middle), tile a
        packed layout "review_layout" of all, and makes it the view (the sequence stays
        available in RV's Sessions list);
      - item "annotations" become RVPaint text on the given frames (with the frame:N.order
        entry, without which rvio does not draw them);
      - labels, titles, groups and each item's free-form "meta" are stored in a "review"
        component on each source and on the session object, so a reopened session still
        maps every frame back to its item (rv_review.py --notes reads them).
    Node names are unique, at least two characters, from [A-Za-z0-9_]; human text stays in
    ui.name and the review component.

check  SESSION.rv [--rv-bin DIR]
    Parse the file with RV's gtoinfo (found next to rv) and report syntax errors; without RV
    it runs the built-in structural check only. Exit 0 clean, 1 problems.

render SESSION.rv -o OUT [--rv-bin DIR] [-- RVIO_ARGS...]
    Run rvio on the session: OUT is a movie (review.mov / .mp4) or an image pattern
    (frames/review.#.png). Extra rvio options go after "--", e.g. -- -t 1-10 -outres 1280 720.
    Check first with 'check'; rvio reports GTO errors only as text.

Output: one JSON line ({"ok": ..., "session": ..., "problems": [...]}).
Exit status: 0 ok, 1 problems (message says what to fix), 2 bad arguments.
"""
import argparse
import glob
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.path.abspath(__file__)).parent))
import review_manifest as rm  # noqa: E402

GTO_VERSION = 4
DEFAULT_TEXT_SIZE = 0.003      # RV text size: glyphs are about 10 x size image heights tall
DEFAULT_TEXT_COLOR = (1.0, 0.85, 0.1, 1.0)   # amber: readable on light and dark renders
TEXT_MARGIN = 0.03             # inset of default-positioned text from the image corner
TEXT_LINE = 12                 # baseline offset from the top edge, in multiples of size
TEXT_USER = "review"           # user part of RVPaint component names (text:ID:FRAME:USER)
VIEW_NODES = {"sequence": "review_sequence", "stack": "review_stack", "layout": "review_layout"}
STACK_OPS = {"wipe": "over", "over": "over", "replace": "replace", "difference": "difference",
             "difference-inverted": "-difference"}
WIPE_BOX = (0.0, 0.5, 0.0, 1.0)   # top source's visible part in a wipe: [x0, x1, y0, y1], 0-1
NAME_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]+$")


# --- names and values -----------------------------------------------------------------

def sanitize_name(text, taken=()):
    """A node name RV accepts: [A-Za-z0-9_], starts with a letter or '_', two or more
    characters, not in taken. RV cuts names at other characters and one-character names
    break property creation, so human text never goes into node names."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", str(text)).strip("_")
    if not s or not re.match(r"[A-Za-z_]", s):
        s = "n_" + s if s else "node"
    if len(s) < 2:
        s += "_n"
    base, n = s, 1
    while s in taken:
        n += 1
        s = f"{base}_{n}"
    return s


def gto_string(s):
    """A double-quoted GTO text string; RV's parser reads \\\\, \\" and \\n escapes."""
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n")
    return f'"{s}"'


def _num(v, kind):
    if kind == "int":
        return str(int(v))
    f = float(v)
    return repr(f) if f != int(f) else str(int(f))


def gto_property(kind, name, value, width=1):
    """One property line. value: scalar, list of scalars, or list of width-sized tuples."""
    fmt = gto_string if kind == "string" else (lambda v: _num(v, kind))
    if width > 1:
        rows = value if value and isinstance(value[0], (list, tuple)) else [value]
        body = " ".join("[ " + " ".join(fmt(x) for x in r) + " ]" for r in rows)
        return f"{kind}[{width}] {name} = [ {body} ]"
    if isinstance(value, (list, tuple)):
        return f"{kind} {name} = [ " + " ".join(fmt(x) for x in value) + (" ]" if value else "]")
    return f"{kind} {name} = {fmt(value)}"


class Gto:
    """Minimal text GTO document: objects -> components -> properties, written in order."""

    def __init__(self):
        self.objects = []                 # [(name, protocol, version, {component: [lines]})]
        self._index = {}

    def obj(self, name, protocol, version=1):
        if name not in self._index:
            self._index[name] = len(self.objects)
            self.objects.append((name, protocol, version, {}))
        return self.objects[self._index[name]][3]

    def prop(self, obj_name, protocol, component, kind, name, value, width=1, version=1):
        comps = self.obj(obj_name, protocol, version)
        comps.setdefault(component, []).append(gto_property(kind, name, value, width))

    def text(self):
        out = [f"GTOa ({GTO_VERSION})", ""]
        for name, protocol, version, comps in self.objects:
            out.append(f"{name} : {protocol} ({version})")
            out.append("{")
            for comp, lines in comps.items():
                cname = comp if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", comp) else gto_string(comp)
                out.append(f"    {cname}")
                out.append("    {")
                out += [f"        {ln}" for ln in lines]
                out.append("    }")
                out.append("")
            if out[-1] == "":
                out.pop()
            out.append("}")
            out.append("")
        return "\n".join(out)


# --- media helpers ----------------------------------------------------------------------

def image_size(path):
    """(width, height) of a PNG, JPEG, GIF or BMP read from its header, else None."""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                return struct.unpack(">II", head[16:24])
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return struct.unpack("<HH", head[6:10])
            if head.startswith(b"BM"):
                w, h = struct.unpack("<ii", head[18:26])
                return w, abs(h)
            if head.startswith(b"\xff\xd8"):
                f.seek(2)
                while True:
                    marker = f.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    if marker[1] in (0xD8, 0x01) or 0xD0 <= marker[1] <= 0xD7:
                        continue
                    seg = struct.unpack(">H", f.read(2))[0]
                    if 0xC0 <= marker[1] <= 0xCF and marker[1] not in (0xC4, 0xC8, 0xCC):
                        h, w = struct.unpack(">xHH", f.read(5))
                        return w, h
                    f.seek(seg - 2, 1)
    except (OSError, struct.error):
        return None
    return None


def _spec_glob(path):
    """Glob pattern and a regex capturing the frame number for an RV sequence spec."""
    p = Path(path)
    m = re.search(r"(\d+-\d+(?:x\d+)?)?(#|@+|%0?(\d*)d)", p.name)
    if not m:
        return None, None
    pre, post = p.name[:m.start()], p.name[m.end():]
    pattern = str(Path(glob.escape(str(p.parent))) / (glob.escape(pre) + "[0-9]*" + glob.escape(post)))
    rx = re.compile(re.escape(pre) + r"(-?\d+)" + re.escape(post) + r"$")
    return pattern, rx


def media_frames(path):
    """(first, last) source frames of a still, sequence spec or movie, as far as known
    without RV; last is None for movies. A still's frame is the last number in its name."""
    name = Path(path).name
    if rm.is_sequence_spec(path):
        m = re.search(r"(-?\d+)-(-?\d+)(?:x\d+)?(?:#|@+|%0?\d*d)", name)
        if m:
            return int(m.group(1)), int(m.group(2))
        pattern, rx = _spec_glob(path)
        frames = sorted(int(rx.match(Path(f).name).group(1)) for f in glob.glob(pattern)
                        if rx.match(Path(f).name)) if pattern else []
        return (frames[0], frames[-1]) if frames else (1, None)
    suffix = Path(path).suffix.lower()
    if suffix in {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mxf", ".webm", ".mpg", ".mpeg"}:
        return 1, None
    nums = re.findall(r"\d+", Path(path).stem)
    f = int(nums[-1]) if nums else 1
    return f, f


def item_frames(item):
    """(first, last) source frames of an item after in / out; last None when unknown."""
    first, last = media_frames(rm.media_of(item)[0])
    if "in" in item:
        first = item["in"]
    if "out" in item:
        last = item["out"]
    return first, last


def item_length(item):
    first, last = item_frames(item)
    return None if last is None else max(1, last - first + 1)


def item_aspect(item):
    media = rm.media_of(item)[0]
    if rm.is_sequence_spec(media):
        pattern, _ = _spec_glob(media)
        hits = sorted(glob.glob(pattern)) if pattern else []
        media = hits[0] if hits else media
    size = image_size(media)
    return (size[0] / size[1]) if size and size[1] else 16 / 9


def offline_item_ranges(items):
    """[(first, last)] global frames per item for a sequence, or None if a length is unknown
    (a movie without in / out)."""
    ranges, start = [], 1
    for it in items:
        n = item_length(it)
        if n is None:
            return None
        ranges.append((start, start + n - 1))
        start += n
    return ranges


def resolve_marks(manifest, ranges, marks=None):
    """Marks for the session: explicit list, 'groups', 'auto' or 'none'."""
    marks = manifest.get("marks", "auto") if marks is None else marks
    if isinstance(marks, list):
        return sorted(set(int(m) for m in marks))
    if marks == "none" or ranges is None:
        return []
    if marks == "groups" or (marks == "auto" and manifest.get("groups")):
        return rm.group_starts(manifest["items"], ranges)
    starts = [a for a, _ in ranges]
    long_items = any(b > a for a, b in ranges)
    return starts if len(starts) > 1 and long_items else []


# --- building the session ---------------------------------------------------------------

def _meta_json(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build(manifest, marks=None, fps=None, layout=None):
    """Gto document for a normalised manifest (review_manifest.normalise / load)."""
    items = manifest["items"]
    layout = layout or manifest.get("layout", "sequence")
    if layout not in rm.LAYOUTS:
        raise ValueError(f"layout must be one of {', '.join(rm.LAYOUTS)}")
    fps = fps if fps is not None else manifest.get("fps")
    ranges = offline_item_ranges(items)
    mark_list = resolve_marks(manifest, ranges, marks)
    g = Gto()
    taken = set()
    if layout == "sequence":
        view = VIEW_NODES["sequence"]
    elif layout == "tile":
        view = VIEW_NODES["layout"]
    else:
        view = VIEW_NODES["stack"]
    # session object first: RV reads it to know what to show
    g.prop("rv", "RVSession", "session", "string", "viewNode", view, version=4)
    if fps:
        g.prop("rv", "RVSession", "session", "float", "fps", float(fps), version=4)
    g.prop("rv", "RVSession", "session", "int", "currentFrame", 1, version=4)
    g.prop("rv", "RVSession", "review", "int", "schema_version", rm.SCHEMA_VERSION, version=4)
    g.prop("rv", "RVSession", "review", "string", "title", manifest.get("title", ""), version=4)
    g.prop("rv", "RVSession", "review", "string", "meta", _meta_json(manifest.get("meta", {})),
           version=4)
    g.prop("rv", "RVSession", "review", "string", "groups",
           _meta_json(manifest.get("groups", [])), version=4)
    g.prop("rv", "RVSession", "review", "string", "layout", layout, version=4)

    names = [f"sourceGroup{i:06d}" for i in range(len(items))]
    taken.update(names)
    lhs, rhs = [], []
    seq = VIEW_NODES["sequence"]
    for n in names:
        lhs.append(n)
        rhs.append(seq)
    if layout == "tile":
        for n in names:
            lhs.append(n)
            rhs.append(VIEW_NODES["layout"])
    elif layout != "sequence":
        for n in names[:2]:
            lhs.append(n)
            rhs.append(VIEW_NODES["stack"])
    g.prop("connections", "connection", "evaluation", "string", "lhs", lhs, version=1)
    g.prop("connections", "connection", "evaluation", "string", "rhs", rhs, version=1)

    title = manifest.get("title") or "review"
    g.prop(seq, "RVSequenceGroup", "ui", "string", "name", title)
    g.prop(seq, "RVSequenceGroup", "session", "int", "marks", mark_list)
    if fps:
        g.prop(seq, "RVSequenceGroup", "session", "float", "fps", float(fps))
    g.prop(seq, "RVSequenceGroup", "session", "int", "frame", 1)
    if layout == "tile":
        lay = VIEW_NODES["layout"]
        g.prop(lay, "RVLayoutGroup", "ui", "string", "name", f"{title} (tile)")
        g.prop(lay, "RVLayoutGroup", "layout", "string", "mode", "packed")
    elif layout != "sequence":
        st = VIEW_NODES["stack"]
        g.prop(st, "RVStackGroup", "ui", "string", "name", f"{title} ({layout})")
        g.prop(st, "RVStackGroup", "ui", "int", "wipes", 1 if layout == "wipe" else 0)
        g.prop(f"{st}_stack", "RVStack", "composite", "string", "type", STACK_OPS[layout])
        if layout == "wipe":
            # the wipe edge: the top item shows on the left half, the second on the right (the
            # stack's per-input transform, which RV's wipes mode edits when the edge is dragged)
            g.prop(f"{st}_t_{names[0]}", "RVTransform2D", "stencil", "float", "visibleBox",
                   list(WIPE_BOX))
    if manifest.get("stereo") and manifest["stereo"] != "off":
        # the node RV itself saves the display stereo mode on
        g.prop("defaultOutputGroup_stereo", "RVDisplayStereo", "stereo", "string", "type",
               manifest["stereo"])

    for it, n in zip(items, names):
        src = f"{n}_source"
        g.prop(n, "RVSourceGroup", "ui", "string", "name", it["label"])
        media = rm.media_of(it)
        g.prop(src, "RVFileSource", "media", "string", "movie",
               [m.replace("\\", "/") for m in media] if len(media) > 1
               else media[0].replace("\\", "/"))
        if "fps" in it:
            g.prop(src, "RVFileSource", "group", "float", "fps", float(it["fps"]))
        if "in" in it or "out" in it:
            first, last = media_frames(media[0])
            g.prop(src, "RVFileSource", "cut", "int", "in", it.get("in", first))
            if "out" in it or last is not None:
                g.prop(src, "RVFileSource", "cut", "int", "out", it.get("out", last))
        if it.get("view"):
            g.prop(src, "RVFileSource", "request", "string", "imageComponent", ["view", it["view"]])
        if it.get("stereo_views"):
            g.prop(src, "RVFileSource", "request", "string", "stereoViews", list(it["stereo_views"]))
        g.prop(src, "RVFileSource", "review", "int", "item", it["index"])
        g.prop(src, "RVFileSource", "review", "string", "label", it["label"])
        g.prop(src, "RVFileSource", "review", "string", "title", it.get("title", ""))
        g.prop(src, "RVFileSource", "review", "string", "group", it.get("group", "") or "")
        g.prop(src, "RVFileSource", "review", "string", "meta", _meta_json(it.get("meta", {})))
        if it.get("view"):
            g.prop(src, "RVFileSource", "review", "string", "view", it["view"])
        _paint(g, n, it)
    return g


def paint_properties(item):
    """RVPaint properties for an item's text annotations, as
    [(component, kind, name, values, width)] in writing order: every text component (text
    first), the paint counters, then one frame:N.order per frame. Shared by the session
    writer and rv_review.py, which creates the same properties in a live RV."""
    anns = item.get("annotations") or []
    if not anns:
        return []
    first, _ = item_frames(item)
    aspect = item_aspect(item)
    out, by_frame = [], {}
    for k, a in enumerate(anns, 1):
        frame = a["source_frame"] if "source_frame" in a else first + a.get("frame", 1) - 1
        comp = f"text:{k}:{frame}:{TEXT_USER}"
        size = float(a.get("size", DEFAULT_TEXT_SIZE))
        x, y = a.get("position", (-aspect / 2 + TEXT_MARGIN, 0.5 - TEXT_MARGIN - size * TEXT_LINE))
        color = [float(c) for c in a.get("color", DEFAULT_TEXT_COLOR)]
        color += [1.0] * (4 - len(color))
        # text first: RV's own writer fails when position / size come before the text
        out += [(comp, "string", "text", [a["text"]], 1),
                (comp, "float", "position", [round(float(x), 4), round(float(y), 4)], 2),
                (comp, "float", "color", color, 4),
                (comp, "float", "size", [size], 1),
                (comp, "float", "scale", [1.0], 1),
                (comp, "float", "spacing", [0.8], 1),
                (comp, "float", "rotation", [0.0], 1),
                (comp, "string", "font", [""], 1)]
        by_frame.setdefault(frame, []).append(comp)
    out += [("paint", "int", "nextId", [len(anns) + 1], 1), ("paint", "int", "show", [1], 1)]
    for frame, comps in sorted(by_frame.items()):
        # without frame:N.order the text exists but rvio (and RV) never draw it
        out.append((f"frame:{frame}", "string", "order", comps, 1))
    return out


def _paint(g, group_name, item):
    node = f"{group_name}_paint"
    for comp, kind, name, values, width in paint_properties(item):
        if width > 1:
            value = [tuple(values[i:i + width]) for i in range(0, len(values), width)]
        elif name == "order":
            value = list(values)
        else:
            value = values[0]
        g.prop(node, "RVPaint", comp, kind, name, value, width=width, version=3)


def write(manifest, path, marks=None, fps=None, layout=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(manifest, marks, fps, layout).text(), encoding="utf-8")
    return path


# --- reading back and checking ----------------------------------------------------------

OBJ_RE = re.compile(r'^\s*("?[^\s:"]+"?)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((\d+)\)', re.M)


def summarise(path):
    """What a text .rv holds, for checking a load: source count, view node, marks, and the
    review component values written by this script. Binary GTO returns None."""
    data = Path(path).read_bytes()
    if not data.lstrip().startswith(b"GTOa"):
        return None
    text = data.decode("utf-8", "replace")
    objs = OBJ_RE.findall(text)
    out = {"sources": sum(1 for _, p, _ in objs if p == "RVFileSource"),
           "objects": [(n.strip('"'), p) for n, p, _ in objs]}
    m = re.search(r'string\s+viewNode\s*=\s*"([^"]*)"', text)
    out["viewNode"] = m.group(1) if m else None
    out["marks"] = []
    if out["viewNode"]:
        blk = re.search(r"^" + re.escape(out["viewNode"]) + r"\s*:\s*\w+\s*\(\d+\)\s*\{(.*?)^\}",
                        text, re.M | re.S)
        if blk:
            mk = re.search(r"int\s+marks\s*=\s*\[([^\]]*)\]", blk.group(1))
            if mk:
                out["marks"] = [int(x) for x in mk.group(1).split()]
    return out


def structural_problems(text):
    """Cheap checks that catch the usual hand-written session mistakes without RV."""
    probs = []
    if not text.startswith("GTOa"):
        probs.append("first line must be 'GTOa (N)' for a text GTO file")
    bare = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)      # string values cannot break syntax
    if re.search(r"\b(?:int|float|string)\[\]\s", bare):
        probs.append("'type[] name' is not GTO syntax; write 'type name = [ ... ]'")
    depth = 0
    for ch in bare:
        depth += ch == "{"
        depth -= ch == "}"
        if depth < 0:
            break
    if depth != 0:
        probs.append("unbalanced braces")
    for name, proto, _ in OBJ_RE.findall(text):
        n = name.strip('"')
        if proto not in ("connection",) and not NAME_OK.match(n):
            probs.append(f"object name {n!r} is not [A-Za-z_][A-Za-z0-9_]+ (two characters or more)")
    return probs


def rv_tool(name, rv_bin=None):
    """Path of an RV command-line tool (gtoinfo, rvio) next to rv, or None."""
    try:
        import rv_review
        rv, _ = rv_review.find_rv(rv_bin)
    except Exception:          # RV missing or rv_review unavailable: callers fall back
        return None
    exe = rv.parent / (name + (".exe" if os.name == "nt" else ""))
    return exe if exe.is_file() else None


def gtoinfo_problems(path, gtoinfo):
    """Syntax errors gtoinfo reports (it exits 0 even on errors, so read its text)."""
    r = subprocess.run([str(gtoinfo), str(path)], capture_output=True, text=True, timeout=60)
    text = (r.stdout + r.stderr)
    return [ln.strip() for ln in text.splitlines()
            if ln.strip().startswith(("ERROR", "Error"))]


def check(path, rv_bin=None):
    """(problems, checker) for a session file; checker is 'gtoinfo' or 'structural'."""
    path = Path(path)
    if not path.is_file():
        return [f"session not found: {path}"], None
    probs = structural_problems(path.read_text(encoding="utf-8", errors="replace"))
    gi = rv_tool("gtoinfo", rv_bin)
    if gi:
        return probs + gtoinfo_problems(path, gi), "gtoinfo"
    return probs, "structural"


def render_args(rvio, session, out, extra=()):
    return [str(rvio), str(session), "-o", str(out), *extra]


# --- command line -----------------------------------------------------------------------

def _marks_arg(text):
    t = text.strip().lower()
    if t in ("auto", "groups", "none"):
        return t
    try:
        return [int(x) for x in t.split(",") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("auto, groups, none or frame numbers like 1,4,7") from None


def build_parser():
    ap = argparse.ArgumentParser(prog="rv_session.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="{write,check,render}")
    w = sub.add_parser("write", help="write a .rv session from a manifest or frames.json",
                       description="Write a .rv session from a review manifest or frames.json.",
                       epilog="example:\n  python rv_session.py write review.json -o review.rv "
                              "--layout wipe",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    w.add_argument("manifest", help="manifest or frames.json, or - for stdin")
    w.add_argument("-o", "--out", required=True, metavar="FILE.rv", help="session file to write")
    w.add_argument("--layout", choices=rm.LAYOUTS, help="override the manifest's layout")
    w.add_argument("--marks", type=_marks_arg, help="override the manifest's marks")
    w.add_argument("--fps", type=float, help="playback rate stored in the session")
    c = sub.add_parser("check", help="check a .rv file with gtoinfo (or structurally)",
                       description="Check a .rv session file with RV's gtoinfo when RV is "
                                   "installed, otherwise with a structural check.")
    c.add_argument("session", help=".rv file")
    c.add_argument("--rv-bin", metavar="DIR", help="folder holding rv (and gtoinfo)")
    r = sub.add_parser("render", help="render a .rv session with rvio",
                       description="Render a session with rvio into a movie or images.",
                       epilog="examples:\n  python rv_session.py render review.rv -o review.mov\n"
                              "  python rv_session.py render review.rv -o frames/review.#.png "
                              "-- -t 1-5",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    r.add_argument("session", help=".rv file")
    r.add_argument("-o", "--out", required=True, help="movie file or image pattern with #")
    r.add_argument("--rv-bin", metavar="DIR", help="folder holding rv and rvio")
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in argv:                      # everything after -- goes to rvio unchanged
        cut = argv.index("--")
        argv, extra = argv[:cut], argv[cut + 1:]
    a = build_parser().parse_args(argv)
    result = {"ok": False, "problems": []}
    try:
        if a.cmd == "write":
            m = rm.load(a.manifest)
            p = write(m, a.out, a.marks, a.fps, a.layout)
            probs = structural_problems(p.read_text(encoding="utf-8"))
            result.update(session=str(Path(os.path.abspath(p))), items=len(m["items"]), problems=probs,
                          ok=not probs)
        elif a.cmd == "check":
            probs, checker = check(a.session, a.rv_bin)
            result.update(session=str(Path(os.path.abspath(a.session))), checker=checker,
                          problems=probs, ok=not probs)
        else:
            probs, _ = check(a.session, a.rv_bin)
            if probs:
                result.update(problems=probs)
            else:
                rvio = rv_tool("rvio", a.rv_bin)
                if not rvio:
                    raise RuntimeError("rvio not found next to rv; pass --rv-bin <RV bin folder>")
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                cmd = render_args(rvio, Path(os.path.abspath(a.session)), a.out, extra)
                r = subprocess.run(cmd, capture_output=True, text=True)
                errs = [ln for ln in (r.stdout + r.stderr).splitlines()
                        if ln.startswith(("ERROR", "Error"))]
                result.update(command=cmd, exit=r.returncode, problems=errs,
                              ok=r.returncode == 0 and not errs, out=str(Path(os.path.abspath(a.out))))
    except rm.ManifestError as e:
        result["problems"] = e.problems
    except (OSError, ValueError, RuntimeError) as e:
        result["problems"] = [str(e)]
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
