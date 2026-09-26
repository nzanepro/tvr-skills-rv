"""Load media into RV / OpenRV for review, or refresh the running review window.

Sources can be any mix of stills, movies, image sequences (name.#.exr, name.1001-1100#.exr,
name.@@@@.exr, name.%04d.exr) and bracket groups such as  [ left.exr right.exr ]  (one
stereo source from two files) or  [ shot.mov -in 10 -out 50 ]  (per-source options). Put
the sources after "--" when they include brackets or per-source options.

If an RV started by this script is still running (found by its network tag), its contents
are replaced in place with "rvpush set"; otherwise a new RV is launched detached, with
networking on, so it outlives this script and the shell that ran it. The script then sets
the layout (sequence, wipe, difference, over, tile), stereo mode, multi-view selection and
360 view, stops playback on the first frame, adds timeline marks, and reads the state back
to check the load.

Defaults: sequence layout; 1 fps when every source is a still (Space = one image per
second), otherwise the media's own rate; marks at the first frame of every view in
frames.json, or at the first frame of every source when at least one source has more than
one frame; stereo off.

Finding RV (first match wins; rvpush must sit next to rv):
  1. --rv-bin DIR
  2. RV_BIN                      folder that holds rv (this skill's own variable)
  3. RVPUSH_RV_EXECUTABLE_PATH   rv executable rvpush would start, unless it is "none"
  4. RV_PATH                     rv executable (the convention RV's Nuke integration reads)
  5. RV_APP_RV                   rv executable; RV sets it for processes it starts
  6. RV_HOME                     install root: RV_HOME/bin (RV_HOME/Contents/MacOS for an .app);
                                 the Linux rv wrapper script sets it
  7. rv (rv.exe; RV on macOS) on PATH
  8. Windows only: the App Paths registry key for rv.exe that RV's .reg files add
  9. the usual install folders, newest version first:
       Windows  Program Files/OpenRV*/bin, Program Files/{Autodesk,ShotGrid,Shotgun}/RV*/bin
       macOS    /Applications and ~/Applications: RV*.app, OpenRV*.app (Contents/MacOS)
       Linux    /opt/rv*/bin, /opt/RV*/bin, /opt/OpenRV*/bin, /usr/local/rv*/bin, /usr/local/bin

Callers such as other skills pass a review manifest (--manifest FILE, or - for stdin): ordered
items with labels, groups, in / out, fps, views and a free-form "meta" object that comes back
untouched in the result and in --notes. A .rv session file given as the only source is opened
as it is. --save-session writes a .rv of what was loaded. --notes reads the reviewer's
annotations back per item; --export-annotated renders the annotated frames through rvio.

Output: exactly one JSON line on stdout, also for errors (schema "rv-review.result",
schema_version 1), for example
  {"schema": "rv-review.result", "schema_version": 1, "ok": true, "exit_code": 0,
   "action": "launched", "pid": 1234, "tag": "rv-review", "sources": 2, "marks": [1, 13],
   "items": [{"index": 0, "label": "v1", "frames": [1, 12], "sources": [...], "meta": {}}, ...],
   "state": {...}, "problems": [], "errors": [], "warnings": [], ...}
"ok" is true when the state read back from RV matches what was loaded and RV logged no
ERROR lines during the load; "problems" lists any difference, "errors" / "warnings" the
lines RV logged. Every item carries its global frame range, so a frame maps back to its item
and meta. The full schema is in references/integration.md.

Exit status: 0 loaded and verified; 1 error (RV not found, source or manifest problem, RV did
not answer); 2 bad arguments; 3 loaded but the read-back did not match or RV logged errors
(run again once, then report the problems).
"""
import argparse
import ast
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_manifest as rm  # noqa: E402  (same folder; standard library only)
import rv_session  # noqa: E402

DEFAULT_TAG = "rv-review"     # network tag that marks the review window; one window per tag
STILL_FPS = 1.0               # Space plays one still per second: slow enough to compare versions
LAUNCH_TIMEOUT_S = 60.0       # a cold RV start with many frames can take tens of seconds
POLL_INTERVAL_S = 0.5         # how often to ask a starting RV whether it answers yet
SET_SETTLE_S = 0.5            # RV finishes swapping sources before it takes more commands
RVPUSH_TIMEOUT_S = 30.0       # one rvpush call; guards against a hung RV
CREATE_BREAKAWAY_FROM_JOB = 0x01000000   # Windows process flag; not exported by subprocess

STILL_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".dpx", ".cin", ".tga",
              ".bmp", ".gif", ".hdr", ".jp2", ".j2k", ".psd", ".sgi", ".rgb", ".webp", ".iff"}
SEQUENCE_RE = re.compile(r"#|@+|%0?\d*d")          # RV sequence notation in a file name
COMPARE_MODES = ("sequence", "wipe", "difference", "difference-inverted", "over", "replace", "tile")
STEREO_MODES = ("off", "anaglyph", "lumanaglyph", "pair", "mirror", "hsqueezed", "vsqueezed",
                "checker", "scanline", "left", "right", "hardware")   # RVDisplayStereo stereo.type
SEQ_EDL = "defaultSequence_sequence.edl.frame"     # global start frame of every source, plus end
STATE_EXPR = ("(rv.commands.frame(), rv.commands.frameStart(), rv.commands.frameEnd(), "
              "rv.commands.markedFrames(), rv.commands.getIntProperty('" + SEQ_EDL + "'), "
              "len(rv.commands.nodesOfType('RVFileSource')), rv.commands.viewNode(), "
              "rv.commands.nodeType(rv.commands.viewNode()), "
              "rv.commands.getStringProperty('@RVDisplayStereo.stereo.type'), rv.commands.fps())")
SOURCES_EXPR = ("[(s, rv.commands.getStringProperty(s + '.media.movie'), "
                "[v['name'] for v in rv.commands.sourceMediaInfo(s)['viewInfos']]) "
                "for s in sorted(rv.commands.nodesOfType('RVFileSource'))]")


class RvError(RuntimeError):
    """A problem the user can fix; the message says what to try next."""


# --- finding RV -------------------------------------------------------------------------

def _os_kind(platform=None):
    p = platform or sys.platform
    if p.startswith("win"):
        return "windows"
    if p == "darwin":
        return "macos"
    return "linux"


def exe_names(platform=None):
    """(rv names, rvpush names) to look for in a bin folder, in order of preference."""
    kind = _os_kind(platform)
    if kind == "windows":
        return ("rv.exe",), ("rvpush.exe",)
    if kind == "macos":
        return ("RV", "RV64", "rv"), ("rvpush",)       # RV64: older 64-bit app bundles
    return ("rv",), ("rvpush",)


def _natural_key(path):
    # "RV-2024.10" sorts after "RV-2024.9"; used to try the newest install first
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(path))]


def install_patterns(platform=None, env=None, home=None, root="/"):
    """Glob patterns for the usual install folders, in the order they are tried."""
    env = os.environ if env is None else env
    kind = _os_kind(platform)
    root = Path(root)
    if kind == "windows":
        pats = []
        for var in ("ProgramFiles", "ProgramW6432"):
            if not env.get(var):
                continue
            pf = Path(env[var])
            for p in (pf / "OpenRV" / "bin", pf / "OpenRV*" / "bin",
                      pf / "Autodesk" / "RV*" / "bin", pf / "ShotGrid" / "RV*" / "bin",
                      pf / "Shotgun" / "RV*" / "bin"):
                if str(p) not in pats:
                    pats.append(str(p))
        return pats
    if kind == "macos":
        apps = [root / "Applications"]
        if home is not None:
            apps.append(Path(home) / "Applications")
        return [str(a / app / "Contents" / "MacOS")
                for a in apps for app in ("RV*.app", "OpenRV*.app")]
    return [str(root / p) for p in ("opt/rv*/bin", "opt/RV*/bin", "opt/OpenRV*/bin",
                                    "opt/openrv*/bin", "usr/local/rv*/bin", "usr/local/bin")]


def _pair_in(folder, platform=None):
    """(rv, rvpush) paths if both are in folder, else None."""
    rv_names, push_names = exe_names(platform)
    folder = Path(folder)
    rv = next((folder / n for n in rv_names if (folder / n).is_file()), None)
    push = next((folder / n for n in push_names if (folder / n).is_file()), None)
    return (rv, push) if rv and push else None


def _home_bin(rv_home, platform=None):
    """Bin folder under an RV install root (an .app bundle on macOS)."""
    h = Path(rv_home)
    if h.suffix == ".app":
        return h / "Contents" / "MacOS"
    return h / "bin"


def registry_rv():
    """rv.exe from the Windows "App Paths" key that RV's .reg files register, else None."""
    try:
        import winreg
    except ImportError:
        return None
    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\rv.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key) as k:
                value = winreg.QueryValueEx(k, "")[0]
        except OSError:
            continue
        if value:
            return value.strip('"')
    return None


def candidates(rv_bin=None, env=None, platform=None, home=None, root="/", registry=None):
    """[(source, folder)] in lookup order; explicit sources come first.

    registry: callable returning the registered rv.exe path (default: registry_rv on Windows).
    """
    env = os.environ if env is None else env
    kind = _os_kind(platform)
    out = []
    if rv_bin:
        out.append(("--rv-bin", Path(rv_bin)))
    if env.get("RV_BIN"):
        out.append(("RV_BIN", Path(env["RV_BIN"])))
    for var in ("RVPUSH_RV_EXECUTABLE_PATH", "RV_PATH", "RV_APP_RV"):   # each names rv itself
        exe = env.get(var, "")
        if exe and exe.lower() != "none":
            out.append((var, Path(exe).parent))
    if env.get("RV_HOME"):
        out.append(("RV_HOME", _home_bin(env["RV_HOME"], platform)))
    for n in exe_names(platform)[0]:
        hit = shutil.which(n, path=env.get("PATH", ""))
        if hit:
            out.append(("PATH", Path(hit).parent))
            break
    if kind == "windows":
        reg = (registry or registry_rv)()
        if reg:
            out.append(("registry", Path(reg).parent))
    for pat in install_patterns(platform, env, home, root):
        for hit in sorted(glob.glob(pat), key=_natural_key, reverse=True):
            out.append(("install folder", Path(hit)))
    return out


def find_rv(rv_bin=None, env=None, platform=None, home=None, root="/", registry=None):
    """(rv, rvpush) paths. An explicit --rv-bin or RV_BIN that is wrong is an error, not skipped."""
    if home is None:
        home = Path.home()
    for source, folder in candidates(rv_bin, env, platform, home, root, registry):
        folder = folder.parent if folder.is_file() else folder
        pair = _pair_in(folder, platform)
        if pair:
            return pair
        if source in ("--rv-bin", "RV_BIN"):
            rv_names, push_names = exe_names(platform)
            raise RvError(f"{source} is {folder}, but it does not hold both {rv_names[0]} and "
                          f"{push_names[0]}. Point it at the RV bin folder (<install>/bin, or "
                          f"RV.app/Contents/MacOS on macOS).")
    raise RvError("RV not found: tried --rv-bin, RV_BIN, RVPUSH_RV_EXECUTABLE_PATH, RV_PATH, "
                  "RV_APP_RV, RV_HOME, PATH, the Windows registry and the usual install folders. "
                  "Install RV or OpenRV, or pass --rv-bin <folder with rv and rvpush>, or set "
                  "RV_BIN to that folder.")


# --- sources ----------------------------------------------------------------------------

def is_sequence_spec(token):
    """True for RV sequence notation: name.#.exr, name.1-10#.exr, name.@@@@.exr, name.%04d.exr."""
    return bool(SEQUENCE_RE.search(Path(token).name))


def is_still(token):
    """A single image file (not a movie, not a sequence)."""
    return Path(token).suffix.lower() in STILL_EXTS and not is_sequence_spec(token)


def group_sources(tokens):
    """Split source tokens into RV sources: a bracket group  [ a b -in 1 ]  is one source."""
    groups, cur, depth = [], None, 0
    for t in tokens:
        if t == "[":
            if depth:
                raise RvError("nested '[' in the source list; close the group with ']' first.")
            depth, cur = 1, ["["]
        elif t == "]":
            if not depth:
                raise RvError("']' without a matching '[' in the source list.")
            cur.append("]")
            groups.append(cur)
            depth, cur = 0, None
        elif depth:
            cur.append(t)
        else:
            if t.startswith("-"):
                raise RvError(f"per-source option {t!r} must sit inside a [ ... ] group, after "
                              f"'--', for example: -- [ shot.mov -in 10 -out 50 ]")
            groups.append([t])
    if depth:
        raise RvError("'[' without a matching ']' in the source list.")
    return groups


OPTION_VALUES = {"-noMovieAudio": 0, "-select": 2}   # per-source options; others take one value


def _group_files(group):
    """File tokens of one group (skips brackets and per-source options and their values)."""
    files, skip = [], 0
    for t in group:
        if skip:
            skip -= 1
        elif t in ("[", "]"):
            continue
        elif t.startswith("-"):
            skip = OPTION_VALUES.get(t, 1)
        else:
            files.append(t)
    return files


def resolve_token(token, cwd=None):
    """Absolute path for a file or sequence spec; errors say what to check."""
    p = Path(token)
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    if is_sequence_spec(token):
        if not p.parent.is_dir():
            raise RvError(f"folder of sequence {token} not found. Check the path; list what a "
                          f"folder holds with 'rvls <folder>'.")
        return str(p.parent.resolve() / p.name)
    if not p.is_file():
        raise RvError(f"source not found: {token}. Check the path (relative paths resolve from "
                      f"the current folder); for an image sequence use RV notation such as "
                      f"name.#.exr or name.1001-1100#.exr.")
    return str(p.resolve())


def resolve_sources(tokens, cwd=None):
    """Tokens with every file made absolute; brackets and options unchanged."""
    out = []
    for g in group_sources(tokens):
        files = set(_group_files(g))
        for t in g:
            out.append(resolve_token(t, cwd) if t in files else t)
    return out


def load_frames_json(path):
    """(frames, marks) from a frames.json written by sheet_panels.py split."""
    path = Path(path)
    if not path.is_file():
        raise RvError(f"frames.json not found: {path}. Run 'sheet_panels.py split SHEET ... "
                      f"--out DIR' first and pass DIR/frames.json.")
    data = json.loads(path.read_text())
    return [str(f) for f in data["frames"]], [int(v["frame"]) for v in data["views"]]


def parse_marks(text):
    """'auto' / 'none' / '1, 4,7' -> 'auto' / [] / [1, 4, 7]."""
    text = (text or "").strip().lower()
    if text in ("", "auto"):
        return "auto"
    if text == "none":
        return []
    try:
        return [int(t) for t in text.split(",") if t.strip()]
    except ValueError:
        raise RvError(f"--marks takes auto, none or comma-separated frame numbers, got {text!r}") from None


def auto_marks(edl_frames):
    """Marks at the first global frame of every source, from the sequence EDL (its last entry
    is the end). None when every source is one frame long: a mark on every frame is noise."""
    starts = list(edl_frames[:-1]) if len(edl_frames) > 1 else []
    if len(starts) < 2:
        return []
    lengths = [b - a for a, b in zip(edl_frames[:-1], edl_frames[1:])]
    return starts if any(n > 1 for n in lengths) else []


def expand_views(groups, source_views, wanted):
    """One source per view for multi-view sources.

    groups: token groups as loaded; source_views: view names RV reported per source (same
    order); wanted: 'all' or a list of view names. Returns (tokens, view per source), with
    None for sources left as they are (single-view media, or none of the wanted views).
    """
    tokens, assign = [], []
    for g, views in zip(groups, source_views):
        pick = list(views) if wanted == "all" else [v for v in wanted if v in views]
        if len(views) > 1 and pick:
            for v in pick:
                tokens += g
                assign.append(v)
        else:
            tokens += g
            assign.append(None)
    return tokens, assign


# --- building RV commands ---------------------------------------------------------------

def post_commands(compare="sequence", marks=(), fps=None, stereo=None, stereo_views=None,
                  swap_eyes=False, view_assign=None, latlong=False, info_strip=False):
    """Python run inside RV (rvpush py-exec) after loading.

    py-exec runs with separate globals and locals, so comprehensions cannot see local
    variables or imports: always spell out rv.commands / rv.runtime and inline the lists.
    """
    c = []
    if compare == "tile":
        c.append("rv.commands.setViewNode('defaultLayout')")
    elif compare == "sequence":
        c.append("rv.commands.setViewNode('defaultSequence')")
    else:
        comp = {"wipe": "over", "difference-inverted": "-difference"}.get(compare, compare)
        c.append(f"rv.commands.setStringProperty('defaultStack_stack.composite.type', ['{comp}'], True)")
        c.append("rv.commands.setViewNode('defaultStack')")
    want_wipe = "!=" if compare == "wipe" else "=="
    c.append("rv.runtime.eval('if (rvui.wipeShown() " + want_wipe +
             " commands.CheckedMenuState) rvui.toggleWipe();', ['rvui', 'commands'])")
    reload = False
    if view_assign and any(view_assign):
        c.append("[rv.commands.setStringProperty(s + '.request.imageComponent', ['view', v], True) "
                 "for s, v in zip(sorted(rv.commands.nodesOfType('RVFileSource')), "
                 f"{list(view_assign)!r}) if v]")
        reload = True
    if stereo_views:
        a, b = stereo_views
        c.append("[rv.commands.setStringProperty(s + '.request.stereoViews', "
                 f"[{a!r}, {b!r}], True) for s in rv.commands.nodesOfType('RVFileSource') "
                 f"if {a!r} in [v['name'] for v in rv.commands.sourceMediaInfo(s)['viewInfos']]]")
        reload = True
    if reload:
        c.append("rv.commands.reload()")
    # always set the stereo mode: a refreshed window keeps the previous load's mode otherwise
    c.append(f"rv.commands.setStringProperty('@RVDisplayStereo.stereo.type', ['{stereo or 'off'}'], True)")
    c.append(f"rv.commands.setIntProperty('@RVDisplayStereo.stereo.swap', [{int(bool(swap_eyes))}], True)")
    if latlong:
        # LatLongViewer node (OpenRV's lat_long_viewer package) on top of the view: a
        # rectilinear window into the sphere; Shift+drag looks around when the package is loaded
        c.append("n = rv.commands.newNode('LatLongViewer', 'latlong'); "
                 "rv.commands.setNodeInputs(n, [rv.commands.viewNode()]); rv.commands.setViewNode(n)")
    c.append("rv.commands.stop()")
    if fps:
        c.append(f"rv.commands.setFPS({float(fps)})")
    c.append("rv.commands.setFrame(rv.commands.frameStart())")
    if info_strip:
        c.append("rv.runtime.eval('if (rvui.infoStripShown() == 0) rvui.toggleInfoStrip();', ['rvui'])")
    if marks:
        # Alt+Left / Alt+Right jump between marks; Ctrl+Left / Ctrl+Right loop one source
        c.append("[rv.commands.markFrame(f, True) for f in [" + ",".join(str(int(m)) for m in marks) + "]]")
    return "; ".join(c)


def launch_args(rv, tokens, tag, latlong=False):
    # -flags takes every argument up to the next option, so it goes before -network
    extra = ["-flags", "ModeManagerPreload=lat_long_viewer"] if latlong else []  # Shift+drag look
    return [str(rv), *extra, "-network", "-networkTag", tag, *tokens]


def rvpush_args(rvpush, tag, *args):
    return [str(rvpush), "-tag", tag, *args]


def child_env(env=None):
    """Environment for rvpush: never let it start an RV of its own, which would be tied to
    this process and could die with it."""
    e = dict(os.environ if env is None else env)
    e["RVPUSH_RV_EXECUTABLE_PATH"] = "none"
    return e


STATE_KEYS = ("frame", "frameStart", "frameEnd", "marks", "sourceStarts", "sources",
              "viewNode", "viewNodeType", "stereo", "fps")


def parse_state(text):
    """rvpush py-eval-return output of STATE_EXPR -> dict, or None if it is not that."""
    try:
        vals = ast.literal_eval(text.strip())
        if not isinstance(vals, tuple) or len(vals) != len(STATE_KEYS):
            return None
        st = dict(zip(STATE_KEYS, vals))
        st["marks"] = sorted(int(m) for m in st["marks"])
        st["sourceStarts"] = [int(f) for f in st["sourceStarts"]][:-1]
        st["stereo"] = st["stereo"][0] if st["stereo"] else "off"
        st["frames"] = int(st["frameEnd"]) - int(st["frameStart"]) + 1
        return st
    except (ValueError, SyntaxError, TypeError, KeyError, IndexError):
        return None


def check_state(state, expected):
    """Differences between the read-back state and what was asked for (empty list = ok)."""
    if state is None:
        return ["RV did not return its state"]
    probs = []
    if "sources" in expected and state["sources"] != expected["sources"]:
        probs.append(f"{state['sources']} sources loaded, expected {expected['sources']}")
    if "frames" in expected and state["frames"] != expected["frames"]:
        probs.append(f"{state['frames']} frames loaded, expected {expected['frames']}")
    if "marks" in expected and state["marks"] != sorted(set(expected["marks"])):
        probs.append(f"marks {state['marks']}, expected {sorted(set(expected['marks']))}")
    if "viewNodeType" in expected and state["viewNodeType"] != expected["viewNodeType"]:
        probs.append(f"view {state['viewNodeType']}, expected {expected['viewNodeType']}")
    if "stereo" in expected and state["stereo"] != expected["stereo"]:
        probs.append(f"stereo {state['stereo']}, expected {expected['stereo']}")
    return probs


VIEW_NODE_TYPES = {"sequence": "RVSequenceGroup", "tile": "RVLayoutGroup"}


# --- items: what the caller asked for, mapped onto RV sources and frames -------------------

def _item_from_group(group, index):
    """A manifest-style item for one command-line source group (file or [ ... ] group)."""
    files = _group_files(group)
    item = {"index": index, "path": files if len(files) > 1 else files[0],
            "label": Path(files[0]).name, "title": "", "meta": {}}
    it = iter(group)
    for t in it:
        if t in ("-in", "-out"):
            try:
                item[t[1:]] = int(next(it))
            except (StopIteration, ValueError):
                pass
        elif t == "-fps":
            try:
                item["fps"] = float(next(it))
            except (StopIteration, ValueError):
                pass
        elif t == "-select":
            kind, name = next(it, ""), next(it, "")
            if kind == "view" and name:
                item["view"] = name
    return item


def items_from_tokens(tokens):
    return [_item_from_group(g, i) for i, g in enumerate(group_sources(tokens))]


def manifest_tokens(manifest):
    """rv / rvpush source arguments for a normalised manifest, one source per item."""
    out = []
    for it in manifest["items"]:
        out += rm.rv_tokens(it)
    return out


def item_ranges(state, source_items, n_items, layout="sequence"):
    """[(first, last)] global frames per item from the read-back state.

    source_items: item index of every loaded source, in load order. In a sequence each source
    covers its EDL span; stacks and tiles show every source over the whole range."""
    if not state:
        return [None] * n_items
    start, end = int(state["frameStart"]), int(state["frameEnd"])
    starts = list(state.get("sourceStarts") or [])
    spans = []
    for k in range(len(source_items)):
        if layout != "sequence" or k >= len(starts):
            spans.append((start, end))
        else:
            spans.append((starts[k], (starts[k + 1] - 1) if k + 1 < len(starts) else end))
    ranges = [None] * n_items
    for k, i in enumerate(source_items):
        if i is None or i >= n_items or k >= len(spans):
            continue
        a, b = spans[k]
        ranges[i] = (a, b) if ranges[i] is None else (min(ranges[i][0], a), max(ranges[i][1], b))
    return ranges


def item_at_frame(items, frame):
    """The first item whose frame range holds a global frame, else None."""
    for it in items:
        fr = it.get("frames")
        if fr and fr[0] <= frame <= fr[1]:
            return it
    return None


def _public_item(it, sources=(), frames=None):
    out = {"index": it["index"], "label": it["label"], "title": it.get("title", ""),
           "group": it.get("group"), "path": it["path"], "sources": list(sources),
           "frames": list(frames) if frames else None, "meta": it.get("meta", {})}
    for k in ("in", "out", "fps", "view"):
        if k in it:
            out[k] = it[k]
    return out


def group_ranges(groups, items):
    out = []
    for g in groups:
        fr = [it["frames"] for it in items if it.get("group") == g["id"] and it.get("frames")]
        out.append({"id": g["id"], "label": g.get("label", g["id"]), "title": g.get("title", ""),
                    "frames": [min(a for a, _ in fr), max(b for _, b in fr)] if fr else None,
                    "meta": g.get("meta", {})})
    return out


def review_props_commands(source_names, source_items, items, session_node=None, manifest=None,
                          chunk=6000):
    """py-exec strings that store each item's label, title, group, view and meta on its RV
    source (component 'review'), and the session title / meta / groups on the session node,
    so --notes, --state and a saved session map frames back to items. Split into chunks to
    stay under command-line length limits."""
    props = []
    for src, i in zip(source_names, source_items):
        if i is None:
            continue
        it = items[i]
        props.append((f"{src}.review.item", "int", it["index"]))
        for key in ("label", "title", "group", "view"):
            props.append((f"{src}.review.{key}", "string", str(it.get(key) or "")))
        props.append((f"{src}.review.meta", "string",
                      json.dumps(it.get("meta", {}), sort_keys=True, separators=(",", ":"))))
    if session_node and manifest is not None:
        base = f"{session_node}.review."
        props.append((base + "schema_version", "int", rm.SCHEMA_VERSION))
        for key in ("title", "layout"):
            props.append((base + key, "string", str(manifest.get(key) or "")))
        for key in ("meta", "groups"):
            props.append((base + key, "string", json.dumps(manifest.get(key) or ({} if key == "meta" else []),
                                                           sort_keys=True, separators=(",", ":"))))
    cmds, cur = [], []

    def flush():
        if not cur:
            return
        s = [(p, v) for p, k, v in cur if k == "string"]
        n = [(p, v) for p, k, v in cur if k == "int"]
        parts = []
        if s:
            parts.append("[(rv.commands.newProperty(p, rv.commands.StringType, 1) if not "
                         "rv.commands.propertyExists(p) else None, "
                         "rv.commands.setStringProperty(p, [v], True)) for p, v in " + ascii(s) + "]")
        if n:
            parts.append("[(rv.commands.newProperty(p, rv.commands.IntType, 1) if not "
                         "rv.commands.propertyExists(p) else None, "
                         "rv.commands.setIntProperty(p, [v], True)) for p, v in " + ascii(n) + "]")
        cmds.append("; ".join(parts))
        cur.clear()

    size = 0
    for p in props:
        cur.append(p)
        size += len(repr(p))
        if size > chunk:
            flush()
            size = 0
    flush()
    return cmds


def annotation_commands(source_names, source_items, items):
    """py-exec strings that draw each item's text annotations into its source's RVPaint node
    in a live RV (the same properties rv_session.py writes into a .rv)."""
    cmds = []
    for src, i in zip(source_names, source_items):
        if i is None or not items[i].get("annotations"):
            continue
        node = re.sub(r"_source$", "_paint", src)
        rows = {"string": [], "float": [], "int": []}
        for comp, kind, name, values, width in rv_session.paint_properties(items[i]):
            rows[kind].append((f"{node}.{comp}.{name}", width, list(values)))
        parts = []
        for kind, typ, setter in (("string", "StringType", "setStringProperty"),
                                  ("float", "FloatType", "setFloatProperty"),
                                  ("int", "IntType", "setIntProperty")):
            if rows[kind]:
                parts.append(f"[(rv.commands.newProperty(p, rv.commands.{typ}, w) if not "
                             f"rv.commands.propertyExists(p) else None, "
                             f"rv.commands.{setter}(p, v, True)) for p, w, v in "
                             + ascii(rows[kind]) + "]")
        cmds.append("; ".join(parts))
    return cmds


# --- RV's log: errors and warnings logged during a load ---------------------------------

LOG_LINE_RE = re.compile(
    r"^(?:\[[^\]]*\]\s*\[[^\]]*\]\s*\[(?P<lvl1>[a-z]+)\]\s*(?P<msg1>.*)"     # [time] [OpenRV] [error] msg
    r"|(?P<lvl2>ERROR|WARNING|CRITICAL|WARN)\s*:\s*(?P<msg2>.*))$")          # ERROR: msg
LOG_IGNORE = ()   # message substrings that are known to be harmless; none so far


def parse_log(text):
    """(errors, warnings) from RV log text; INFO / DEBUG lines are dropped, duplicates merged."""
    errors, warnings = [], []
    for line in (text or "").splitlines():
        m = LOG_LINE_RE.match(line.strip())
        if not m:
            continue
        lvl = (m.group("lvl1") or m.group("lvl2") or "").lower()
        msg = (m.group("msg1") if m.group("lvl1") else m.group("msg2")).strip()
        if not msg or any(s in msg for s in LOG_IGNORE):
            continue
        if lvl in ("error", "critical", "err"):
            if msg not in errors:
                errors.append(msg)
        elif lvl in ("warning", "warn"):
            if msg not in warnings:
                warnings.append(msg)
    return errors, warnings


def own_log_path(tag):
    """Where this script sends the stdout / stderr of an RV it launches."""
    import tempfile
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", tag) or "rv"
    return Path(tempfile.gettempdir()) / f"rv-review-{safe}.log"


def app_log_path(env=None, platform=None, home=None):
    """OpenRV's own log file (OpenRV src/lib/base/TwkUtil/FileLogger.cpp): Windows
    %APPDATA%/ASWF/OpenRV/OpenRV.log, macOS ~/Library/Logs/ASWF/OpenRV.log, Linux
    ~/.local/share/ASWF/OpenRV/OpenRV.log. Every OpenRV window appends to it."""
    env = os.environ if env is None else env
    home = Path(home) if home else Path.home()
    kind = _os_kind(platform)
    if kind == "windows":
        base = env.get("APPDATA")
        return Path(base) / "ASWF" / "OpenRV" / "OpenRV.log" if base else None
    if kind == "macos":
        return home / "Library" / "Logs" / "ASWF" / "OpenRV.log"
    xdg = env.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / "ASWF" / "OpenRV" / "OpenRV.log"


class LogWatch:
    """Remembers the size of RV's log files, then returns what was written after that."""

    def __init__(self, tag):
        own = own_log_path(tag)
        # the file this script captures belongs to one RV window; the shared app log is the
        # fallback for windows started some other way (other RV windows write to it too)
        self.paths = [own] if own.is_file() else [p for p in [app_log_path()] if p]
        self.sizes = {p: (p.stat().st_size if p.is_file() else 0) for p in self.paths}

    def use(self, path):
        """Watch path from its current end (after launching RV with its output there)."""
        self.paths = [Path(path)]
        self.sizes = {Path(path): Path(path).stat().st_size if Path(path).is_file() else 0}

    def new_text(self):
        out = []
        for p in self.paths:
            try:
                with open(p, "rb") as f:
                    size = p.stat().st_size
                    f.seek(self.sizes.get(p, 0) if size >= self.sizes.get(p, 0) else 0)
                    out.append(f.read().decode("utf-8", "replace"))
            except OSError:
                continue
        return "\n".join(out)

    def sources(self):
        return [str(p) for p in self.paths]


# --- talking to RV ----------------------------------------------------------------------

def _rvpush(rvpush, tag, *args):
    try:
        # rvpush prints what RV returns as UTF-8 on every platform
        r = subprocess.run(rvpush_args(rvpush, tag, *args), env=child_env(),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=RVPUSH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 1, "rvpush timed out"
    return r.returncode, (r.stdout + r.stderr).strip()


def _eval(rvpush, tag, expr):
    code, out = _rvpush(rvpush, tag, "py-eval-return", expr)
    if code != 0:
        return None
    try:
        return ast.literal_eval(out)
    except (ValueError, SyntaxError):
        return None


def _launch_detached(args, log_path=None):
    out = subprocess.DEVNULL
    if log_path:
        try:
            out = open(log_path, "ab")           # RV's stderr: ERROR / WARNING lines for checks
            out.write(f"\n=== rv-review launch {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode())
            out.flush()
        except OSError:
            out = subprocess.DEVNULL
    kw = dict(stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True)
    try:
        if os.name != "nt":
            return subprocess.Popen(args, start_new_session=True, **kw)
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            # leave the caller's job object too, or a tool runner that closes its job on exit
            # takes RV down with it
            return subprocess.Popen(args, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw)
        except OSError:                      # the job forbids breakaway: detach as far as allowed
            return subprocess.Popen(args, creationflags=flags, **kw)
    finally:
        if out is not subprocess.DEVNULL:
            out.close()                      # the child keeps its own handle


def read_state(rvpush, tag):
    code, out = _rvpush(rvpush, tag, "py-eval-return", STATE_EXPR)
    return parse_state(out) if code == 0 else None


def _load(rv, rvpush, tag, tokens, latlong=False, watch=None, expected=1):
    """Replace the contents of the tagged RV, or launch one. Returns (action, pid).

    After a launch, waits until RV reports `expected` sources (it adds them one by one)."""
    code, _ = _rvpush(rvpush, tag, "set", *tokens)
    if code == 0:
        time.sleep(SET_SETTLE_S)
        return "replaced", None
    log = own_log_path(tag)
    if watch is not None:
        watch.use(log)
    p = _launch_detached(launch_args(rv, tokens, tag, latlong), log)
    start = time.monotonic()
    while True:
        c, out = _rvpush(rvpush, tag, "py-eval-return", "len(rv.commands.sources())")
        if c == 0 and out.isdigit() and int(out) > 0:     # answering and sources added
            if int(out) >= expected or time.monotonic() - start > LAUNCH_TIMEOUT_S / 2:
                return "launched", p.pid
        if p.poll() is not None or time.monotonic() - start > LAUNCH_TIMEOUT_S:
            raise RvError(
                f"RV pid {p.pid} started but did not answer rvpush within {int(LAUNCH_TIMEOUT_S)} s "
                f"(exited: {p.poll() is not None}). Check that RV networking is allowed (a "
                f"firewall prompt may be waiting), that the sources open in RV by hand, and that "
                f"no other RV uses tag '{tag}'; then run again. RV's output: {log}")
        time.sleep(POLL_INTERVAL_S)


SOURCE_NAMES_EXPR = ("(sorted(rv.commands.nodesOfType('RVFileSource')), "
                     "rv.commands.nodesOfType('RVSession'))")
REVIEW_KEYS = ("label", "title", "group", "view", "meta")
SOURCE_REVIEW_EXPR = (
    "[(s, rv.commands.getStringProperty(s + '.media.movie'), "
    "[(rv.commands.getStringProperty(s + '.review.' + k) if rv.commands.propertyExists("
    "s + '.review.' + k) else []) for k in ['label', 'title', 'group', 'view', 'meta']], "
    "(rv.commands.getIntProperty(s + '.review.item') if rv.commands.propertyExists("
    "s + '.review.item') else [])) for s in sorted(rv.commands.nodesOfType('RVFileSource'))]")
SESSION_REVIEW_EXPR = (
    "[[(rv.commands.getStringProperty(n + '.review.' + k) if rv.commands.propertyExists("
    "n + '.review.' + k) else []) for k in ['title', 'layout', 'meta', 'groups']] "
    "for n in rv.commands.nodesOfType('RVSession')]")
ANNOTATED_EXPR = ("[(f, rv.commands.sourcesAtFrame(f), rv.extra_commands.sourceFrame(f)) "
                  "for f in sorted(set(rv.extra_commands.findAnnotatedFrames()))]")
PAINT_EXPR = ("[(n, [(p, rv.commands.getStringProperty(p)) for p in rv.commands.properties(n) "
              "if p.endswith('.text') or p.endswith('.order')]) "
              "for n in rv.commands.nodesOfType('RVPaint')]")


def _first(v, default=""):
    return v[0] if isinstance(v, (list, tuple)) and v else default


def _json_or(text, default):
    try:
        v = json.loads(text)
        return v if isinstance(v, type(default)) else default
    except (TypeError, ValueError):
        return default


def items_from_rv(source_info, state=None, layout="sequence"):
    """Items rebuilt from the review component on RV's sources (set by this script or by a
    session written with rv_session.py); sources without it become one item each."""
    items, by_index, source_items = [], {}, []
    for k, (src, media, vals, item_no) in enumerate(source_info):
        label, title, group, view, meta = [_first(v) for v in vals]
        idx = _first(item_no, None)
        key = idx if idx is not None else f"src{k}"
        if key not in by_index:
            files = list(media) if isinstance(media, (list, tuple)) else [media]
            by_index[key] = len(items)
            it = {"index": len(items), "path": files if len(files) > 1 else (files[0] if files else ""),
                  "label": label or (Path(files[0]).name if files else src), "title": title,
                  "meta": _json_or(meta, {}), "_sources": []}
            if group:
                it["group"] = group
            if view:
                it["view"] = view
            items.append(it)
        items[by_index[key]]["_sources"].append(src)
        source_items.append(by_index[key])
    ranges = item_ranges(state, source_items, len(items), layout)
    for it, fr in zip(items, ranges):
        it["frames"] = list(fr) if fr else None
    return items, source_items


def _paint_target(node):
    """(source node, frame kind) for an RVPaint node: its source for per-source paint
    ('source' frames), or None for view-level paint on a stack or layout ('global' frames)."""
    m = re.match(r"^(sourceGroup\d+)_paint$", node) or re.match(r"^.+_p_(sourceGroup\d+)$", node)
    return (m.group(1) + "_source", "source") if m else (None, "global")


def build_notes(items, annotated, paint):
    """Per-item notes from findAnnotatedFrames, sourcesAtFrame / sourceFrame and the RVPaint
    properties (text:ID:FRAME:USER.text, frame:N.order)."""
    src_item = {s: it for it in items for s in it.get("_sources", [])}
    notes = {it["index"]: [] for it in items}

    def note_for(it, frame=None, source_frame=None):
        for n in notes[it["index"]]:
            if (frame is not None and n["frame"] == frame) or \
               (frame is None and source_frame is not None and n["source_frame"] == source_frame):
                return n
        first = (it.get("frames") or [None])[0]
        n = {"frame": frame, "item_frame": (frame - first + 1) if frame and first else None,
             "source_frame": source_frame, "texts": [], "strokes": 0, "image": None}
        notes[it["index"]].append(n)
        return n

    for f, srcs, sf in annotated or []:
        if len(srcs or []) != 1:        # stack / tile: the paint data says which source
            continue
        it = src_item.get(srcs[0])
        if it is not None:
            note_for(it, f, sf)
    for node, props in paint or []:
        values = {p: v for p, v in props}
        src, kind = _paint_target(node)
        for p, order in props:
            m = re.match(r"^.*\.frame:(-?\d+)\.order$", p)
            if not m or not order:
                continue
            fnum = int(m.group(1))
            texts = [_first(values.get(f"{node}.{c}.text")) for c in order if c.startswith("text:")]
            texts = [t for t in texts if t]
            strokes = sum(1 for c in order if c.startswith("pen:"))
            if kind == "source":
                it = src_item.get(src)
                if it is None:
                    continue
                n = next((x for x in notes[it["index"]] if x["source_frame"] == fnum), None) \
                    or note_for(it, None, fnum)
                targets = [n]
            else:
                it = item_at_frame(items, fnum)
                targets = [note_for(it, fnum, None)] if it else []
            for n in targets:
                n["texts"] += [t for t in texts if t not in n["texts"]]
                n["strokes"] += strokes
    for v in notes.values():
        v.sort(key=lambda n: (n["frame"] is None, n["frame"] or 0, n["source_frame"] or 0))
    return notes


def export_annotated(rv, rvpush, tag, frames, folder):
    """Render the annotated frames through rvio from a copy of the live session, the way
    RV's File > Export > Annotated Frames does. Returns (images by frame, command, problems)."""
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    session = folder / "annotated_session.rv"
    _rvpush(rvpush, tag, "py-eval-return",
            f"rv.commands.saveSession({str(session).replace(chr(92), '/')!r}, True)")
    if not session.is_file():
        return {}, None, [f"RV did not save {session}; check that the folder is writable"]
    rvio = Path(rv).parent / ("rvio.exe" if os.name == "nt" else "rvio")
    if not rvio.is_file():
        return {}, None, [f"rvio not found next to {rv}; the annotated session is at {session}"]
    cmd = [str(rvio), str(session), "-o", str(folder / "annotated.#.png"),
           "-t", ",".join(str(f) for f in frames)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    errors, _ = parse_log(r.stdout + r.stderr)
    images = {}
    for f in frames:
        hits = sorted(folder.glob(f"annotated.*{f}.png"))
        hit = next((h for h in hits if re.search(rf"\.0*{f}\.png$", h.name)), None)
        if hit:
            images[f] = str(hit)
    if r.returncode != 0 and not errors:
        errors = [f"rvio exited {r.returncode}"]
    return images, cmd, errors


def read_notes(rv, rvpush, tag, export_dir=None):
    state = read_state(rvpush, tag)
    if state is None:
        raise RvError(f"no RV with tag '{tag}' answered. Load the review with this script "
                      f"first, or pass the --tag the review window was started with.")
    info = _eval(rvpush, tag, SOURCE_REVIEW_EXPR) or []
    sess = _eval(rvpush, tag, SESSION_REVIEW_EXPR) or []
    title, layout, meta, groups = [_first(v) for v in (sess[0] if sess else [[]] * 4)]
    layout = layout or ("sequence" if state["viewNodeType"] == "RVSequenceGroup" else "stack")
    items, _ = items_from_rv(info, state, "sequence" if layout == "sequence" else layout)
    annotated = _eval(rvpush, tag, ANNOTATED_EXPR) or []
    paint = _eval(rvpush, tag, PAINT_EXPR) or []
    notes = build_notes(items, annotated, paint)
    frames = sorted({n["frame"] for v in notes.values() for n in v if n["frame"]})
    export, problems = None, []
    if export_dir and frames:
        images, cmd, problems = export_annotated(rv, rvpush, tag, frames, export_dir)
        for v in notes.values():
            for n in v:
                n["image"] = images.get(n["frame"])
        export = {"folder": str(Path(export_dir).resolve()), "command": cmd,
                  "session": str(Path(export_dir).resolve() / "annotated_session.rv")}
    out_items = []
    for it in items:
        d = _public_item(it, it.get("_sources", []), it.get("frames"))
        d["notes"] = notes[it["index"]]
        out_items.append(d)
    return {"action": "notes", "tag": tag, "title": title, "meta": _json_or(meta, {}),
            "groups": group_ranges(_json_or(groups, []), items),
            "items": out_items, "annotated_frames": frames, "marks": state["marks"],
            "export": export, "state": state, "problems": problems}


def _wait_edl(rvpush, tag, n_sources, timeout=10.0):
    """The sequence EDL once it holds every source (n_sources starts plus the end entry)."""
    start, edl = time.monotonic(), []
    while True:
        edl = _eval(rvpush, tag, f"rv.commands.getIntProperty('{SEQ_EDL}')") or []
        if len(edl) >= n_sources + 1 or time.monotonic() - start > timeout:
            return list(edl)
        time.sleep(POLL_INTERVAL_S)


def _session_post_commands():
    """After opening a .rv: keep its view, marks and layout; only stop on the first frame."""
    return "rv.commands.stop(); rv.commands.setFrame(rv.commands.frameStart())"


def review(tokens, rv, rvpush, tag=DEFAULT_TAG, marks="auto", compare="sequence", fps=None,
           views=None, stereo=None, stereo_views=None, swap_eyes=False, latlong=False,
           info_strip=False, manifest=None, save_session=None):
    """Load, set up, read back and verify. manifest: a normalised review manifest whose items
    match tokens one to one (None builds items from the tokens)."""
    groups = group_sources(tokens)
    session_file = next((f for g in groups for f in _group_files(g)
                         if f.lower().endswith(".rv")), None)
    if session_file and len(groups) > 1:
        raise RvError("a .rv session must be the only source; open it on its own.")
    if manifest is None:
        manifest = {"schema_version": rm.SCHEMA_VERSION, "title": "", "meta": {}, "groups": [],
                    "items": items_from_tokens(tokens), "layout": compare, "marks": "auto"}
    items = manifest["items"]
    watch = LogWatch(tag)
    n_expected = (rv_session.summarise(session_file) or {}).get("sources", 1) if session_file \
        else len(groups)
    action, pid = _load(rv, rvpush, tag, tokens, latlong, watch, max(1, n_expected))
    view_assign = None
    source_items = list(range(len(groups)))
    if views and not session_file:
        infos = _eval(rvpush, tag, SOURCES_EXPR) or []
        source_views = [v for _, _, v in infos]
        if views != "all" and len(views) == 1:           # show one view, do not expand
            view_assign = [views[0] if views[0] in v else None for v in source_views]
        else:
            new_tokens, view_assign = expand_views(groups, source_views, views)
            source_items = []
            for gi, v in enumerate(source_views):
                pick = list(v) if views == "all" else [x for x in views if x in v]
                source_items += [gi] * (len(pick) if len(v) > 1 and pick else 1)
            if len(view_assign) != len(groups):
                _rvpush(rvpush, tag, "set", *new_tokens)
                time.sleep(SET_SETTLE_S)
                groups = group_sources(new_tokens)
    expected = {}
    if session_file:
        summary = rv_session.summarise(session_file) or {}
        if "sources" in summary:
            expected["sources"] = summary["sources"]
        if summary.get("marks"):
            expected["marks"] = summary["marks"]
        marks = summary.get("marks", [])
    else:
        if marks == "auto":
            marks = []
            if compare == "sequence":
                edl = _wait_edl(rvpush, tag, len(groups))
                if manifest.get("groups") and manifest.get("marks", "auto") in ("auto", "groups") \
                        and len(groups) == len(items):
                    starts = list(edl)[:-1]
                    if len(starts) < len(items):         # EDL not complete: stills are 1 frame
                        starts = [a for a, _ in (rv_session.offline_item_ranges(items) or [])]
                    marks = rm.group_starts(items, [(s, s) for s in starts[:len(items)]]) \
                        if len(starts) >= len(items) else auto_marks(list(edl))
                elif manifest.get("marks") != "none":
                    marks = auto_marks(list(edl))
        if fps is None and all(is_still(f) for g in groups for f in _group_files(g)):
            fps = STILL_FPS
        expected = {"sources": len(groups), "marks": marks}
        if latlong:
            expected["viewNodeType"] = "LatLongViewer"
        elif compare in VIEW_NODE_TYPES:
            expected["viewNodeType"] = VIEW_NODE_TYPES[compare]
        expected["stereo"] = stereo or "off"
    state, problems = None, []
    for attempt in range(2):                 # post commands, read back, one retry on mismatch
        if session_file:
            _rvpush(rvpush, tag, "py-exec", _session_post_commands())
        else:
            add_latlong = latlong and (state is None or state["viewNodeType"] != "LatLongViewer")
            _rvpush(rvpush, tag, "py-exec", post_commands(
                compare, marks, fps, stereo, stereo_views, swap_eyes, view_assign, add_latlong,
                info_strip))
        state = read_state(rvpush, tag)
        problems = check_state(state, expected)
        if not problems:
            break
        time.sleep(SET_SETTLE_S)
    if session_file:
        info = _eval(rvpush, tag, SOURCE_REVIEW_EXPR) or []
        sess = _eval(rvpush, tag, SESSION_REVIEW_EXPR) or []
        title, layout, meta, groups_json = [_first(v) for v in (sess[0] if sess else [[]] * 4)]
        seq = (state or {}).get("viewNodeType") == "RVSequenceGroup"
        rv_items, _ = items_from_rv(info, state, "sequence" if seq else "stack")
        manifest = {"title": title, "meta": _json_or(meta, {}), "groups": _json_or(groups_json, [])}
        out_items = [_public_item(it, it["_sources"], it["frames"]) for it in rv_items]
    else:
        names = _eval(rvpush, tag, SOURCE_NAMES_EXPR) or ([], [])
        src_names, sess_nodes = names[0], names[1]
        for cmd in review_props_commands(src_names, source_items, items,
                                         sess_nodes[0] if sess_nodes else None, manifest) +                 annotation_commands(src_names, source_items, items):
            _rvpush(rvpush, tag, "py-exec", cmd)
        layout = "sequence" if compare == "sequence" and not latlong else compare
        ranges = item_ranges(state, source_items, len(items), layout)
        by_item = {}
        for s, i in zip(src_names, source_items):
            by_item.setdefault(i, []).append(s)
        out_items = [_public_item(it, by_item.get(it["index"], []), fr)
                     for it, fr in zip(items, ranges)]
    time.sleep(SET_SETTLE_S)                 # RV flushes its log lines a moment later
    errors, warnings = parse_log(watch.new_text())
    if errors:
        problems = problems + [f"RV logged {len(errors)} error(s) during the load; see 'errors'"]
    session_out = None
    if save_session and not session_file:
        sess_items = []
        for i, it in enumerate(items):
            srcs = [k for k, x in enumerate(source_items) if x == i]
            views_here = [view_assign[k] for k in srcs] if view_assign else [None] * len(srcs)
            for v in views_here or [None]:
                d = {k: val for k, val in it.items() if not k.startswith("_")}
                if v:
                    d["view"] = v
                d["index"] = len(sess_items)
                sess_items.append(d)
        sm = dict(manifest, items=sess_items, layout=compare if compare in rm.LAYOUTS else "sequence")
        if stereo:
            sm["stereo"] = stereo
        path = rv_session.write(sm, save_session, marks=list(marks), fps=fps)
        session_out = str(Path(path).resolve())
        sp = rv_session.structural_problems(Path(path).read_text(encoding="utf-8"))
        problems += [f"saved session: {p}" for p in sp]
    elif session_file:
        session_out = str(Path(session_file).resolve())
    n_sources = (state or {}).get("sources", len(groups)) if session_file else len(groups)
    return {"action": action, "pid": pid, "tag": tag, "sources": n_sources, "marks": list(marks),
            "views": view_assign, "title": manifest.get("title", ""),
            "meta": manifest.get("meta", {}),
            "items": out_items, "groups": group_ranges(manifest.get("groups", []),
                                                         [dict(o) for o in out_items]),
            "session": session_out, "state": state, "problems": problems,
            "errors": errors, "warnings": warnings, "log": watch.sources(), "ok": not problems}


# --- command line -----------------------------------------------------------------------

RESULT_SCHEMA = "rv-review.result"
RESULT_VERSION = 1
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_MISMATCH = 0, 1, 2, 3


def envelope(body=None, exit_code=EXIT_OK, error=None):
    """Every JSON line this script prints: schema, version, ok, exit_code, then the body."""
    out = {"schema": RESULT_SCHEMA, "schema_version": RESULT_VERSION,
           "ok": exit_code == EXIT_OK, "exit_code": exit_code}
    out.update(body or {})
    # the envelope's own keys always win over anything in the body
    out.update({"schema": RESULT_SCHEMA, "schema_version": RESULT_VERSION,
                "ok": exit_code == EXIT_OK, "exit_code": exit_code})
    if error:
        out["error"] = error
    return out


class JsonArgumentParser(argparse.ArgumentParser):
    """Bad arguments print a JSON error line on stdout (exit 2) as well as the usage."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(json.dumps(envelope({"action": "error"}, EXIT_USAGE,
                                  f"{self.prog}: {message}")))
        sys.exit(EXIT_USAGE)


def _pair(text):
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"expected two view names like left,right, got {text!r}")
    return parts


def _views(text):
    if text.strip().lower() == "all":
        return "all"
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError("expected all or view names like left,right,centre")
    return parts


def build_parser():
    ap = JsonArgumentParser(
        prog="rv_review.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  python rv_review.py --frames-json review/rv_frames/frames.json\n"
               "  python rv_review.py --manifest review.json --save-session review.rv\n"
               "  python rv_review.py review.rv\n"
               "  python rv_review.py before.png after.png v2.png\n"
               "  python rv_review.py shot_v1.mov shot_v2.mov --compare wipe\n"
               "  python rv_review.py 'plates/shot.1001-1100#.exr' 'renders/shot.#.exr'\n"
               "  python rv_review.py views.exr --views all\n"
               "  python rv_review.py --stereo pair -- [ left.exr right.exr ]\n"
               "  python rv_review.py pano_latlong.exr --latlong\n"
               "  python rv_review.py --notes --export-annotated review/notes\n"
               "  python rv_review.py --state")
    ap.add_argument("sources", nargs="*", metavar="SOURCE",
                    help="stills, movies, sequence specs, [ ... ] groups or one .rv session, "
                         "in viewing order")
    ap.add_argument("--frames-json", metavar="FILE",
                    help="frames.json from 'sheet_panels.py split' (or compare_dirs.py / "
                         "review_set.py): its frames in order, with a mark at every view")
    ap.add_argument("--manifest", metavar="FILE|-",
                    help="review manifest JSON (see references/integration.md), or - for stdin; "
                         "labels and meta come back in the result and in --notes")
    ap.add_argument("--marks", default="auto", metavar="auto|none|N,N",
                    help="timeline marks (default auto: see above)")
    ap.add_argument("--compare", choices=COMPARE_MODES, default=None,
                    help="layout: sources back to back (default), wipe / difference / "
                         "difference-inverted / over / replace of the first two, or tile")
    ap.add_argument("--fps", type=float, help="playback rate (default: 1 for stills, else the media's)")
    ap.add_argument("--views", type=_views, metavar="all|NAME[,NAME...]",
                    help="multi-view files: one name shows that view; several names or 'all' "
                         "load one frame per view, in that order")
    ap.add_argument("--stereo", choices=STEREO_MODES,
                    help="stereo display; 'pair' (side by side) or 'anaglyph' work on any monitor")
    ap.add_argument("--stereo-views", type=_pair, metavar="LEFT,RIGHT",
                    help="which two views of a multi-view file form the stereo pair")
    ap.add_argument("--swap-eyes", action="store_true", help="swap the left and right eye")
    ap.add_argument("--latlong", action="store_true",
                    help="view lat-long (equirectangular) images through RV's 360 viewer; "
                         "Shift+drag to look around")
    ap.add_argument("--save-session", metavar="FILE.rv",
                    help="after loading, write an RV session of the review (labels and meta "
                         "included) that reopens with 'rv FILE.rv' or renders with rvio")
    ap.add_argument("--notes", action="store_true",
                    help="load nothing; print the reviewer's annotations per item (frames, "
                         "text, stroke counts) with each item's label and meta")
    ap.add_argument("--export-annotated", metavar="DIR",
                    help="with --notes: also render every annotated frame to DIR through rvio")
    ap.add_argument("--tag", default=DEFAULT_TAG,
                    help=f"RV network tag of the review window (default: {DEFAULT_TAG})")
    ap.add_argument("--rv-bin", metavar="DIR",
                    help="folder holding rv and rvpush; skips the lookup described above")
    ap.add_argument("--info-strip", action="store_true",
                    help="turn on RV's info strip (F7); RV then saves it as on in its preferences")
    ap.add_argument("--state", action="store_true",
                    help="load nothing; print the review window's state and item mapping")
    return ap


def _state_body(rvpush, tag):
    state = read_state(rvpush, tag)
    if state is None:
        raise RvError(f"no RV with tag '{tag}' answered. Load sources with this script "
                      f"first, or pass the --tag the review window was started with.")
    info = _eval(rvpush, tag, SOURCE_REVIEW_EXPR) or []
    seq = state["viewNodeType"] == "RVSequenceGroup"
    items, _ = items_from_rv(info, state, "sequence" if seq else "stack")
    return {"action": "state", "tag": tag, "state": state,
            "items": [_public_item(it, it["_sources"], it["frames"]) for it in items]}


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        if a.export_annotated and not a.notes:
            raise RvError("--export-annotated goes with --notes.")
        rv, rvpush = find_rv(a.rv_bin)
        if a.state:
            print(json.dumps(envelope(_state_body(rvpush, a.tag))))
            return EXIT_OK
        if a.notes:
            body = read_notes(rv, rvpush, a.tag, a.export_annotated)
            code = EXIT_MISMATCH if body["problems"] else EXIT_OK
            print(json.dumps(envelope(body, code)))
            return code
        tokens, marks = list(a.sources), parse_marks(a.marks)
        manifest = None
        if a.manifest or a.frames_json:
            if a.manifest and a.frames_json:
                raise RvError("pass --manifest or --frames-json, not both.")
            if a.frames_json and not Path(a.frames_json).is_file():
                load_frames_json(a.frames_json)            # raises the helpful message
            try:
                manifest = rm.load(a.manifest or a.frames_json)
            except rm.ManifestError as e:
                raise RvError("manifest problems: " + "; ".join(e.problems)) from None
            if tokens:
                raise RvError("sources on the command line cannot be mixed with a manifest; "
                              "add them to the manifest's items.")
            tokens = manifest_tokens(manifest)
            if marks == "auto":
                mm = manifest.get("marks", "auto")
                marks = mm if isinstance(mm, list) else ([] if mm == "none" else "auto")
        if not tokens:
            raise RvError("no sources given. Pass stills, movies, sequences or a .rv session in "
                          "viewing order, or --manifest / --frames-json; see --help.")
        compare = a.compare or (manifest or {}).get("layout", "sequence")
        stereo = a.stereo or ((manifest or {}).get("stereo") if (manifest or {}).get("stereo") != "off" else None)
        fps = a.fps if a.fps is not None else (manifest or {}).get("fps")
        result = review(resolve_sources(tokens), rv, rvpush, a.tag, marks, compare, fps,
                        a.views, stereo, a.stereo_views, a.swap_eyes, a.latlong, a.info_strip,
                        manifest, a.save_session)
    except RvError as e:
        print(f"rv_review: {e}", file=sys.stderr)
        print(json.dumps(envelope({"action": "error"}, EXIT_ERROR, str(e))))
        return EXIT_ERROR
    code = EXIT_OK if result["ok"] else EXIT_MISMATCH
    print(json.dumps(envelope(result, code)))
    return code


if __name__ == "__main__":
    sys.exit(main())
