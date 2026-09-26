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

Output: one JSON line on stdout, for example
  {"action": "launched", "pid": 1234, "tag": "rv-review", "sources": 2, "marks": [1, 13],
   "state": {...}, "problems": [], "ok": true}
"ok" is true when the state read back from RV matches what was loaded; "problems" lists
any difference. --state prints only the "state" object of the running review window.

Exit status: 0 loaded and verified; 1 error (RV not found, source missing, RV did not
answer); 2 bad arguments; 3 loaded but the read-back did not match (run again once, then
report the problems).
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
COMPARE_MODES = ("sequence", "wipe", "difference", "over", "replace", "tile")
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


def _group_files(group):
    """File tokens of one group (skips brackets and per-source options and their values)."""
    files, skip = [], False
    for t in group:
        if skip:
            skip = False
        elif t in ("[", "]"):
            continue
        elif t.startswith("-"):
            skip = t not in ("-noMovieAudio",)       # options below take one value
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
        comp = "over" if compare == "wipe" else compare
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


# --- talking to RV ----------------------------------------------------------------------

def _rvpush(rvpush, tag, *args):
    try:
        r = subprocess.run(rvpush_args(rvpush, tag, *args), env=child_env(),
                           capture_output=True, text=True, timeout=RVPUSH_TIMEOUT_S)
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


def _launch_detached(args):
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True)
    if os.name != "nt":
        return subprocess.Popen(args, start_new_session=True, **kw)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # leave the caller's job object too, or a tool runner that closes its job on exit
        # takes RV down with it
        return subprocess.Popen(args, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw)
    except OSError:                          # the job forbids breakaway: detach as far as allowed
        return subprocess.Popen(args, creationflags=flags, **kw)


def read_state(rvpush, tag):
    code, out = _rvpush(rvpush, tag, "py-eval-return", STATE_EXPR)
    return parse_state(out) if code == 0 else None


def _load(rv, rvpush, tag, tokens, latlong=False):
    """Replace the contents of the tagged RV, or launch one. Returns (action, pid)."""
    code, _ = _rvpush(rvpush, tag, "set", *tokens)
    if code == 0:
        time.sleep(SET_SETTLE_S)
        return "replaced", None
    p = _launch_detached(launch_args(rv, tokens, tag, latlong))
    start = time.monotonic()
    while True:
        c, out = _rvpush(rvpush, tag, "py-eval-return", "len(rv.commands.sources())")
        if c == 0 and out.isdigit() and int(out) > 0:     # answering and sources added
            return "launched", p.pid
        if p.poll() is not None or time.monotonic() - start > LAUNCH_TIMEOUT_S:
            raise RvError(
                f"RV pid {p.pid} started but did not answer rvpush within {int(LAUNCH_TIMEOUT_S)} s "
                f"(exited: {p.poll() is not None}). Check that RV networking is allowed (a "
                f"firewall prompt may be waiting), that the sources open in RV by hand, and that "
                f"no other RV uses tag '{tag}'; then run again.")
        time.sleep(POLL_INTERVAL_S)


def review(tokens, rv, rvpush, tag=DEFAULT_TAG, marks="auto", compare="sequence", fps=None,
           views=None, stereo=None, stereo_views=None, swap_eyes=False, latlong=False,
           info_strip=False):
    groups = group_sources(tokens)
    action, pid = _load(rv, rvpush, tag, tokens, latlong)
    view_assign = None
    if views:
        infos = _eval(rvpush, tag, SOURCES_EXPR) or []
        source_views = [v for _, _, v in infos]
        if views != "all" and len(views) == 1:           # show one view, do not expand
            view_assign = [views[0] if views[0] in v else None for v in source_views]
        else:
            new_tokens, view_assign = expand_views(groups, source_views, views)
            if len(view_assign) != len(groups):
                _rvpush(rvpush, tag, "set", *new_tokens)
                time.sleep(SET_SETTLE_S)
                groups = group_sources(new_tokens)
    if marks == "auto":
        marks = []
        if compare == "sequence":
            edl = _eval(rvpush, tag, f"rv.commands.getIntProperty('{SEQ_EDL}')") or []
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
        add_latlong = latlong and (state is None or state["viewNodeType"] != "LatLongViewer")
        _rvpush(rvpush, tag, "py-exec", post_commands(
            compare, marks, fps, stereo, stereo_views, swap_eyes, view_assign, add_latlong,
            info_strip))
        state = read_state(rvpush, tag)
        problems = check_state(state, expected)
        if not problems:
            break
        time.sleep(SET_SETTLE_S)
    return {"action": action, "pid": pid, "tag": tag, "sources": len(groups), "marks": list(marks),
            "views": view_assign, "state": state, "problems": problems, "ok": not problems}


# --- command line -----------------------------------------------------------------------

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
    ap = argparse.ArgumentParser(
        prog="rv_review.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  python rv_review.py --frames-json review/rv_frames/frames.json\n"
               "  python rv_review.py before.png after.png v2.png\n"
               "  python rv_review.py shot_v1.mov shot_v2.mov --compare wipe\n"
               "  python rv_review.py 'plates/shot.1001-1100#.exr' 'renders/shot.#.exr'\n"
               "  python rv_review.py views.exr --views all\n"
               "  python rv_review.py --stereo pair -- [ left.exr right.exr ]\n"
               "  python rv_review.py pano_latlong.exr --latlong\n"
               "  python rv_review.py --state")
    ap.add_argument("sources", nargs="*", metavar="SOURCE",
                    help="stills, movies, sequence specs or [ ... ] groups, in viewing order")
    ap.add_argument("--frames-json", metavar="FILE",
                    help="frames.json from 'sheet_panels.py split': its frames in order, with a "
                         "mark at the first frame of every view")
    ap.add_argument("--marks", default="auto", metavar="auto|none|N,N",
                    help="timeline marks (default auto: see above)")
    ap.add_argument("--compare", choices=COMPARE_MODES, default="sequence",
                    help="layout: sources back to back (default), wipe / difference / over / "
                         "replace of the first two, or tile")
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
    ap.add_argument("--tag", default=DEFAULT_TAG,
                    help=f"RV network tag of the review window (default: {DEFAULT_TAG})")
    ap.add_argument("--rv-bin", metavar="DIR",
                    help="folder holding rv and rvpush; skips the lookup described above")
    ap.add_argument("--info-strip", action="store_true",
                    help="turn on RV's info strip (F7); RV then saves it as on in its preferences")
    ap.add_argument("--state", action="store_true",
                    help="load nothing; print the review window's state as JSON")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        rv, rvpush = find_rv(a.rv_bin)
        if a.state:
            state = read_state(rvpush, a.tag)
            if state is None:
                raise RvError(f"no RV with tag '{a.tag}' answered. Load sources with this script "
                              f"first, or pass the --tag the review window was started with.")
            print(json.dumps(state))
            return 0
        tokens, marks = list(a.sources), parse_marks(a.marks)
        if a.frames_json:
            frames, view_marks = load_frames_json(a.frames_json)
            tokens = frames + tokens
            if marks == "auto":
                marks = view_marks
        if not tokens:
            raise RvError("no sources given. Pass stills, movies or sequences in viewing order, "
                          "or --frames-json DIR/frames.json; see --help.")
        result = review(resolve_sources(tokens), rv, rvpush, a.tag, marks, a.compare, a.fps,
                        a.views, a.stereo, a.stereo_views, a.swap_eyes, a.latlong, a.info_strip)
    except RvError as e:
        print(f"rv_review: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
