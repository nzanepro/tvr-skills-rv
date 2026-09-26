"""Review manifest: the JSON a caller (an agent, another skill, a script) hands to rv-review.

Standard library only; imported by rv_review.py and rv_session.py. Running this file checks a
manifest and prints the normalised form:

    python review_manifest.py review.json        # or: ... - < review.json

Format (schema_version 1; the full description with examples is in
references/integration.md):

    {
      "schema": "rv-review.manifest",          optional; if present it must be this
      "schema_version": 1,                     required
      "title": "shot010 lighting",             optional
      "layout": "sequence",                    sequence | wipe | difference | difference-inverted |
                                               over | replace | tile   (default sequence)
      "fps": 24,                               optional playback rate
      "stereo": "off",                         optional display stereo mode
      "marks": "auto",                         auto | groups | none | [frame, ...]
      "meta": {},                              free-form, carried through, never interpreted
      "groups": [ {"id": "side", "label": "shot010 side", "title": "...", "meta": {}} ],
      "items": [
        {"path": "renders/side_v1.png",         a still, movie or sequence spec (name.#.exr), or
                                                a list of media for one source (stereo eyes, audio)
         "label": "v1", "title": "...", "group": "side",
         "in": 1001, "out": 1100, "fps": 24,
         "view": "centre", "stereo_views": ["left", "right"],
         "annotations": [{"frame": 1, "text": "check the edge"}],
         "meta": {"version_id": 123}}
      ]
    }

A frames.json from sheet_panels.py split ({"frames": [...], "views": [...]}) is accepted too
and converted: one item per frame, one group per sheet.

Relative paths resolve against the manifest's folder (the current folder for stdin).
"""
import os
import json
import re
import sys
from pathlib import Path

SCHEMA = "rv-review.manifest"
SCHEMA_VERSION = 1                      # bump only for incompatible changes; readers refuse newer
LAYOUTS = ("sequence", "wipe", "difference", "difference-inverted", "over", "replace", "tile")
STEREO_MODES = ("off", "anaglyph", "lumanaglyph", "pair", "mirror", "hsqueezed", "vsqueezed",
                "checker", "scanline", "left", "right", "hardware")
ITEM_KEYS = {"path", "label", "title", "group", "in", "out", "fps", "view", "stereo_views",
             "annotations", "meta"}
TOP_KEYS = {"schema", "schema_version", "title", "layout", "fps", "stereo", "marks", "meta",
            "groups", "items", "frames", "views", "size", "kind", "report"}
ANNOTATION_KEYS = {"frame", "source_frame", "text", "position", "size", "color"}
SEQUENCE_RE = re.compile(r"#|@+|%0?\d*d")


class ManifestError(ValueError):
    """The manifest cannot be used; .problems lists every problem found, with its location."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def from_frames_json(data):
    """Convert a sheet_panels.py frames.json (frames + views) into a manifest dict."""
    frames = [str(f) for f in data.get("frames", [])]
    views = sorted(data.get("views", []), key=lambda v: int(v.get("frame", 1)))
    groups, items = [], []
    for gi, v in enumerate(views):
        start = int(v.get("frame", 1))
        end = int(views[gi + 1]["frame"]) - 1 if gi + 1 < len(views) else len(frames)
        sheet = v.get("sheet") or f"view {gi + 1}"
        gid = f"v{gi + 1}"
        groups.append({"id": gid, "label": Path(str(sheet)).stem, "meta": {"sheet": str(sheet)}})
        labels = list(v.get("labels") or [])
        for k, idx in enumerate(range(start, end + 1)):
            if 1 <= idx <= len(frames):
                lab = labels[k] if k < len(labels) else Path(frames[idx - 1]).stem
                items.append({"path": frames[idx - 1], "label": str(lab), "group": gid})
    covered = {it["path"] for it in items}
    for f in frames:                                     # frames not covered by any view
        if f not in covered:
            items.append({"path": f, "label": Path(f).stem})
    out = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "items": items,
           "marks": "groups" if groups else "auto"}
    if groups:
        out["groups"] = groups
    return out


def _check_annotations(anns, where, probs):
    if not isinstance(anns, list):
        probs.append(f"{where}: must be a list")
        return
    for k, a in enumerate(anns):
        w = f"{where}[{k}]"
        if not isinstance(a, dict):
            probs.append(f"{w}: must be an object")
            continue
        for key in sorted(set(a) - ANNOTATION_KEYS):
            probs.append(f"{w}.{key}: unknown key (allowed: {', '.join(sorted(ANNOTATION_KEYS))})")
        if not isinstance(a.get("text"), str) or not a.get("text"):
            probs.append(f"{w}.text: required, a non-empty string")
        if "frame" in a and not (_is_int(a["frame"]) and a["frame"] >= 1):
            probs.append(f"{w}.frame: an integer >= 1 (1 = the item's first frame)")
        if "source_frame" in a and not _is_int(a["source_frame"]):
            probs.append(f"{w}.source_frame: an integer")
        if "position" in a and not (isinstance(a["position"], list) and len(a["position"]) == 2
                                    and all(_is_num(x) for x in a["position"])):
            probs.append(f"{w}.position: [x, y] numbers")
        if "size" in a and not (_is_num(a["size"]) and a["size"] > 0):
            probs.append(f"{w}.size: a number > 0")
        if "color" in a and not (isinstance(a["color"], list) and len(a["color"]) in (3, 4)
                                 and all(_is_num(x) and 0 <= x <= 1 for x in a["color"])):
            probs.append(f"{w}.color: [r, g, b] or [r, g, b, a] numbers from 0 to 1")


def validate(data):
    """Problems (list of strings) with a manifest dict; empty when it is usable."""
    if not isinstance(data, dict):
        return ["manifest: must be a JSON object"]
    probs = []
    if "schema" in data and data["schema"] != SCHEMA:
        probs.append(f"schema: expected {SCHEMA!r}, got {data['schema']!r}")
    ver = data.get("schema_version")
    if ver is None:
        probs.append(f"schema_version: required (this rv-review reads {SCHEMA_VERSION})")
    elif not _is_int(ver):
        probs.append("schema_version: must be an integer")
    elif ver > SCHEMA_VERSION:
        probs.append(f"schema_version {ver} is newer than this rv-review understands "
                     f"({SCHEMA_VERSION}); update the skill")
    elif ver < 1:
        probs.append("schema_version: must be 1 or more")
    for key in sorted(set(data) - TOP_KEYS):
        probs.append(f"{key}: unknown top-level key (put free-form data in 'meta')")
    if "layout" in data and data["layout"] not in LAYOUTS:
        probs.append(f"layout: one of {', '.join(LAYOUTS)}")
    if "stereo" in data and data["stereo"] not in STEREO_MODES:
        probs.append(f"stereo: one of {', '.join(STEREO_MODES)}")
    if "fps" in data and not (_is_num(data["fps"]) and data["fps"] > 0):
        probs.append("fps: a number > 0")
    marks = data.get("marks", "auto")
    if not (marks in ("auto", "groups", "none") or
            (isinstance(marks, list) and all(_is_int(m) and m >= 1 for m in marks))):
        probs.append("marks: auto, groups, none or a list of frame numbers >= 1")
    if "meta" in data and not isinstance(data["meta"], dict):
        probs.append("meta: must be an object")
    if "title" in data and not isinstance(data["title"], str):
        probs.append("title: must be a string")
    gids = set()
    groups = data.get("groups", [])
    if not isinstance(groups, list):
        probs.append("groups: must be a list")
        groups = []
    for i, g in enumerate(groups):
        w = f"groups[{i}]"
        if not isinstance(g, dict):
            probs.append(f"{w}: must be an object")
            continue
        gid = g.get("id")
        if not isinstance(gid, str) or not gid:
            probs.append(f"{w}.id: required, a non-empty string")
        elif gid in gids:
            probs.append(f"{w}.id: duplicate id {gid!r}")
        else:
            gids.add(gid)
        for key in ("label", "title"):
            if key in g and not isinstance(g[key], str):
                probs.append(f"{w}.{key}: must be a string")
        if "meta" in g and not isinstance(g["meta"], dict):
            probs.append(f"{w}.meta: must be an object")
        for key in sorted(set(g) - {"id", "label", "title", "meta"}):
            probs.append(f"{w}.{key}: unknown key")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        probs.append("items: required, a non-empty list")
        items = []
    for i, it in enumerate(items):
        w = f"items[{i}]"
        if not isinstance(it, dict):
            probs.append(f"{w}: must be an object")
            continue
        for key in sorted(set(it) - ITEM_KEYS):
            probs.append(f"{w}.{key}: unknown key (put free-form data in 'meta')")
        p = it.get("path")
        if isinstance(p, str):
            if not p:
                probs.append(f"{w}.path: must not be empty")
        elif isinstance(p, list):
            if not p or not all(isinstance(x, str) and x for x in p):
                probs.append(f"{w}.path: a list of media must hold non-empty strings")
        else:
            probs.append(f"{w}.path: required, a string or a list of strings")
        for key in ("label", "title", "view"):
            if key in it and not isinstance(it[key], str):
                probs.append(f"{w}.{key}: must be a string")
        if "group" in it and it["group"] not in gids:
            probs.append(f"{w}.group: {it['group']!r} is not an id in 'groups'")
        for key in ("in", "out"):
            if key in it and not _is_int(it[key]):
                probs.append(f"{w}.{key}: must be an integer frame number")
        if _is_int(it.get("in")) and _is_int(it.get("out")) and it["out"] < it["in"]:
            probs.append(f"{w}: out ({it['out']}) is before in ({it['in']})")
        if "fps" in it and not (_is_num(it["fps"]) and it["fps"] > 0):
            probs.append(f"{w}.fps: a number > 0")
        sv = it.get("stereo_views")
        if sv is not None and not (isinstance(sv, list) and len(sv) == 2 and
                                   all(isinstance(x, str) and x for x in sv)):
            probs.append(f"{w}.stereo_views: two view names, e.g. [\"left\", \"right\"]")
        if "meta" in it and not isinstance(it["meta"], dict):
            probs.append(f"{w}.meta: must be an object")
        if "annotations" in it:
            _check_annotations(it["annotations"], f"{w}.annotations", probs)
    return probs


def _resolve(p, base):
    q = Path(p)
    if not q.is_absolute():
        q = Path(base) / q
    return str(q)


def normalise(data, base=None):
    """A validated manifest with defaults filled in and paths made absolute.

    Raises ManifestError listing every problem. Legacy frames.json input is converted first.
    """
    if isinstance(data, dict) and "items" not in data and "frames" in data:
        data = from_frames_json(data)
    probs = validate(data)
    if probs:
        raise ManifestError(probs)
    base = Path(base) if base else Path.cwd()
    out = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
           "title": data.get("title", ""), "layout": data.get("layout", "sequence"),
           "marks": data.get("marks", "auto"), "meta": dict(data.get("meta", {})),
           "groups": [], "items": []}
    for key in ("fps", "stereo"):
        if key in data:
            out[key] = data[key]
    for g in data.get("groups", []):
        out["groups"].append({"id": g["id"], "label": g.get("label", g["id"]),
                              "title": g.get("title", ""), "meta": dict(g.get("meta", {}))})
    for i, it in enumerate(data["items"]):
        p = it["path"]
        paths = [_resolve(x, base) for x in (p if isinstance(p, list) else [p])]
        n = dict(it)
        n["index"] = i
        n["path"] = paths if isinstance(p, list) else paths[0]
        n["label"] = it.get("label") or Path(paths[0]).name
        n["title"] = it.get("title", "")
        n["meta"] = dict(it.get("meta", {}))
        n["annotations"] = [dict(a) for a in it.get("annotations", [])]
        out["items"].append(n)
    return out


def load(path_or_dash):
    """Normalised manifest from a file path, or from stdin for '-'."""
    try:
        if str(path_or_dash) == "-":
            text, base = sys.stdin.read(), Path.cwd()
        else:
            p = Path(path_or_dash)
            if not p.is_file():
                raise ManifestError([f"manifest not found: {p}"])
            text, base = p.read_text(encoding="utf-8"), Path(os.path.abspath(p)).parent
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ManifestError([f"manifest is not valid JSON: {e}"]) from None
    return normalise(data, base)


def media_of(item):
    """The item's media files as a list (one entry unless it is a multi-media source)."""
    p = item["path"]
    return list(p) if isinstance(p, list) else [p]


def is_sequence_spec(path):
    return bool(SEQUENCE_RE.search(Path(path).name))


def rv_tokens(item):
    """rv / rvpush arguments for one item: a plain path, or a [ ... ] group with options."""
    media = media_of(item)
    opts = []
    if "in" in item:
        opts += ["-in", str(item["in"])]
    if "out" in item:
        opts += ["-out", str(item["out"])]
    if "fps" in item:
        opts += ["-fps", str(item["fps"])]
    if item.get("view"):
        opts += ["-select", "view", item["view"]]
    if len(media) == 1 and not opts:
        return media
    return ["[", *media, *opts, "]"]


def group_starts(items, item_frames):
    """First global frame of every group, in order of first appearance.

    items: normalised items; item_frames: [(first, last)] per item (same order)."""
    seen, starts = set(), []
    for it, (first, _) in zip(items, item_frames):
        g = it.get("group")
        if g is not None and g not in seen:
            seen.add(g)
            starts.append(first)
    return starts


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        prog="review_manifest.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", help="manifest or frames.json file, or - for stdin")
    a = ap.parse_args(argv)
    try:
        m = load(a.manifest)
    except ManifestError as e:
        print(json.dumps({"ok": False, "problems": e.problems}, indent=1))
        return 1
    print(json.dumps({"ok": True, "manifest": m}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
