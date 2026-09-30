#!/usr/bin/env python3
"""Find the RV / OpenRV command-line tools and print where they are as JSON.

Looks for one RV bin folder and reports the tools in it: rv, rvio, rvls, rvpkg, rvpush, plus
rvio_hw (Autodesk RV), rvio_sw (Linux OpenRV), mu-interp and py-interp when present.
Standard library only; works on Windows, macOS and Linux. This file is identical in every
tvr-skills-rv command-line skill. It reads no environment variables: RV is found from the
command line, a config file, PATH and the usual install folders.

Search order (first folder that holds the wanted tool wins):
  1. --rv-bin PATH     a bin folder, an install root, an .app bundle or a tool path
  2. config file       "rv_bin" in ~/.config/tvr-skills-rv/config.json (same forms; a
                       leading ~ is the home folder), for example {"rv_bin": "/opt/rv/bin"}
  3. PATH
  4. Windows only: the App Paths registry key for rv.exe that RV's .reg files add
  5. usual install folders, newest version first:
       Windows  Program Files and Program Files (x86): OpenRV*, Autodesk/RV*, ShotGrid/RV*,
                Shotgun/RV*, ShotGrid RV*, Shotgun RV* (each with bin)
       macOS    /Applications and ~/Applications: RV*.app, OpenRV*.app (Contents/MacOS)
       Linux    /opt/rv*, /opt/RV*, /opt/OpenRV*, /opt/openrv*, /usr/local/rv*,
                /usr/local/OpenRV* (each with bin), then /usr/local/bin
  6. OpenRV built from source (the openrv-build plugin's layout): the first of the current
     folder and its parents, ~/OpenRV and, on Windows, C:/OpenRV that holds rvcmds.sh, then
     its _build/stage/app/RV.app/Contents/MacOS (macOS) or _build/stage/app/bin

An explicit --rv-bin or config rv_bin that does not hold the wanted tool is an error, not
skipped, and so is a config file that is not a JSON object.

Output (stdout, one JSON object):
  {"found": true, "bin_dir": "...", "source": "PATH", "platform": "linux",
   "tools": {"rv": "...", "rvio": "...", "rvls": "...", "rvpkg": "...", "rvpush": "...", ...},
   "versions": {"rvio": "3.1.0", "rvls": "3.1.0"}, "searched": [["PATH", "...", false], ...]}
Versions come from "rvio -version" and "rvls -version" (command-line only; rv itself is never
started because it opens a window). --path TOOL prints only that tool's path.

Exit status: 0 found; 1 not found, a wrong --rv-bin / config rv_bin or a broken config
file; 2 bad arguments.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CORE_TOOLS = ("rv", "rvio", "rvls", "rvpkg", "rvpush")
EXTRA_TOOLS = ("rvio_hw", "rvio_sw", "mu-interp", "py-interp")
VERSION_TOOLS = ("rvio", "rvls")          # both print "X.Y.Z" for -version and open no window
VERSION_TIMEOUT_S = 20.0
VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")
CONFIG_PARTS = (".config", "tvr-skills-rv", "config.json")    # under the home folder
EXPLICIT_SOURCES = ("--rv-bin", "config")
OPENRV_MARKER = "rvcmds.sh"               # at the top of an OpenRV source checkout
# Windows known folders (KNOWNFOLDERID): Program Files for this process, the 64-bit one
# and the 32-bit one. Asked from the shell, so a moved Program Files is found too.
FOLDERID_PROGRAM_FILES = ("905e63b6-c1bf-494e-b29c-65b732d3d21a",
                          "6d809377-6af0-444b-8957-a3773f02200e",
                          "7c5a40ef-a0fb-4bfc-874a-c0f2e0b9fa8e")


class RvNotFound(RuntimeError):
    """RV could not be found; the message says what was tried and what to do."""


class ConfigError(ValueError):
    """The config file exists but is not a JSON object of strings."""


def os_kind(platform=None):
    """'windows', 'macos' or 'linux' for a sys.platform value (default: this machine)."""
    p = platform or sys.platform
    if p.startswith("win"):
        return "windows"
    if p == "darwin":
        return "macos"
    return "linux"


def exe_names(tool, platform=None):
    """File names a tool can have in a bin folder, in order of preference."""
    kind = os_kind(platform)
    if kind == "windows":
        return (tool + ".exe",)
    if kind == "macos" and tool == "rv":
        return ("RV", "RV64", "rv")      # the app bundle's main binary; RV64 in older bundles
    return (tool,)


def natural_key(path):
    """Sort key that puts RV-2024.10 after RV-2024.9."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(path))]


# --- config file --------------------------------------------------------------------------

def config_path(home=None):
    """~/.config/tvr-skills-rv/config.json (home: the home folder; default Path.home())."""
    return (Path.home() if home is None else Path(home)).joinpath(*CONFIG_PARTS)


def load_config(home=None):
    """Settings from the config file: {} when there is none; ConfigError when it is broken."""
    path = config_path(home)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{path} is not valid JSON ({exc}); fix it or delete it") from None
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise ConfigError(f'{path} must hold one JSON object of strings, such as '
                          f'{{"rv_bin": "/opt/rv/bin"}}; fix it or delete it')
    return data


def config_value(config, key, home=None):
    """config[key] as a path string, with a leading ~ made the home folder; None if unset."""
    value = (config or {}).get(key, "").strip()
    if not value:
        return None
    if value == "~" or value.startswith(("~/", "~\\")):
        return str((Path.home() if home is None else Path(home)) / value[2:])
    return value


# --- Windows folders ----------------------------------------------------------------------

def known_folder(guid):
    """A Windows known folder from SHGetKnownFolderPath, or None (always None elsewhere)."""
    try:
        import ctypes
        import uuid
        from ctypes import wintypes
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
    except (ImportError, AttributeError, OSError):
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    u = uuid.UUID(guid)
    g = GUID(u.fields[0], u.fields[1], u.fields[2],
             (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
    out = ctypes.c_wchar_p()
    try:
        if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
            return None
        value = out.value
        ole32.CoTaskMemFree(out)
    except (OSError, AttributeError, ValueError):
        return None
    return Path(value) if value else None


def program_files_dirs(root="/"):
    """Program Files folders to search on Windows: the known folders, then C:/Program Files
    and C:/Program Files (x86). Under a test root only the root's own folders are used."""
    dirs = []
    if str(root) == "/":
        for guid in FOLDERID_PROGRAM_FILES:
            p = known_folder(guid)
            if p and p not in dirs:
                dirs.append(p)
        base = Path("C:/")
    else:
        base = Path(root)
    for name in ("Program Files", "Program Files (x86)"):
        if base / name not in dirs:
            dirs.append(base / name)
    return dirs


def install_patterns(platform=None, home=None, root="/", program_files=None):
    """Glob patterns for the usual install bin folders, in the order they are tried.
    program_files: callable returning the Windows Program Files folders (default:
    program_files_dirs(root))."""
    kind = os_kind(platform)
    if kind == "windows":
        pats = []
        bases = program_files() if program_files is not None else program_files_dirs(root)
        for base in bases:
            for sub in ("OpenRV*", "Autodesk/RV*", "ShotGrid/RV*", "Shotgun/RV*",
                        "ShotGrid RV*", "Shotgun RV*"):
                pat = str(Path(base) / sub / "bin")
                if pat not in pats:
                    pats.append(pat)
        return pats
    root = Path(root)
    if kind == "macos":
        apps = [root / "Applications"]
        if home is not None:
            apps.append(Path(home) / "Applications")
        return [str(a / app / "Contents" / "MacOS") for a in apps
                for app in ("RV*.app", "OpenRV*.app")]
    return [str(root / p) for p in ("opt/rv*/bin", "opt/RV*/bin", "opt/OpenRV*/bin",
                                    "opt/openrv*/bin", "usr/local/rv*/bin",
                                    "usr/local/OpenRV*/bin", "usr/local/bin")]


def registry_rv():
    """rv.exe registered under the Windows App Paths key, or None."""
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


# --- OpenRV built from source ---------------------------------------------------------------

def openrv_build_roots(platform=None, home=None, root="/", cwd=None):
    """Folders that may be an OpenRV source checkout: the current folder and its parents,
    ~/OpenRV and, on Windows, C:/OpenRV (the openrv-build plugin's default places)."""
    cwd = Path.cwd() if cwd is None else Path(cwd)
    out = [cwd] + list(cwd.parents)
    if home is not None:
        out.append(Path(home) / "OpenRV")
    if os_kind(platform) == "windows":
        out.append((Path("C:/") if str(root) == "/" else Path(root)) / "OpenRV")
    return out


def openrv_build_bin(checkout, platform=None):
    """The staged bin folder of an OpenRV build in checkout."""
    app = Path(checkout) / "_build" / "stage" / "app"
    if os_kind(platform) == "macos":
        return app / "RV.app" / "Contents" / "MacOS"
    return app / "bin"


def openrv_build_dirs(platform=None, home=None, root="/", cwd=None):
    """Bin folder of the first OpenRV checkout found (one that holds rvcmds.sh), or []."""
    for folder in openrv_build_roots(platform, home, root, cwd):
        try:
            if (folder / OPENRV_MARKER).is_file():
                return [openrv_build_bin(folder, platform)]
        except OSError:
            continue
    return []


# --- the search -----------------------------------------------------------------------------

def bin_dirs_for(path, platform=None):
    """Folders to look in for a user-given path: a tool, a bin folder, an install root or .app."""
    p = Path(path)
    if p.is_file():
        return [p.parent]
    out = [p]
    if p.suffix == ".app" or (p / "Contents" / "MacOS").is_dir():
        out.append(p / "Contents" / "MacOS")
    out.append(p / "bin")
    return out


def iter_candidates(rv_bin=None, config=None, platform=None, home=None, root="/",
                    registry=None, which=None, cwd=None, program_files=None):
    """(source, [folders]) in lookup order, lazily: the config file is read only when
    --rv-bin did not already answer. config: settings dict (default: the config file)."""
    kind = os_kind(platform)
    which = which or shutil.which
    if rv_bin:
        yield "--rv-bin", bin_dirs_for(rv_bin, platform)
    if config is None:
        config = load_config(home)
    configured = config_value(config, "rv_bin", home)
    if configured:
        yield "config", bin_dirs_for(configured, platform)
    seen = set()
    for tool in ("rvio", "rv", "rvls", "rvpkg", "rvpush"):
        for name in exe_names(tool, platform):
            hit = which(name)
            if hit and str(Path(hit).parent) not in seen:
                seen.add(str(Path(hit).parent))
                yield "PATH", [Path(hit).parent]
    if kind == "windows":
        reg = (registry or registry_rv)()
        if reg:
            yield "registry", [Path(reg).parent]
    for pat in install_patterns(platform, home, root, program_files):
        for hit in sorted(glob.glob(pat), key=natural_key, reverse=True):
            yield "install folder", [Path(hit)]
    build = openrv_build_dirs(platform, home, root, cwd)
    if build:
        yield "OpenRV build", build


def candidates(rv_bin=None, config=None, platform=None, home=None, root="/", registry=None,
               which=None, cwd=None, program_files=None):
    """[(source, [folders])] in lookup order."""
    return list(iter_candidates(rv_bin, config, platform, home, root, registry, which, cwd,
                                program_files))


def tool_in(folder, tool, platform=None):
    """Path of tool inside folder, or None."""
    folder = Path(folder)
    for name in exe_names(tool, platform):
        p = folder / name
        if p.is_file() and (os_kind(platform) == "windows" or os_kind() == "windows"
                            or os.access(str(p), os.X_OK)):
            return p
    return None


def tools_in(folder, platform=None):
    """{tool: path or None} for every known tool in folder."""
    return {t: (str(tool_in(folder, t, platform)) if tool_in(folder, t, platform) else None)
            for t in CORE_TOOLS + EXTRA_TOOLS}


def find_rv(want="rvio", rv_bin=None, config=None, platform=None, home=None, root="/",
            registry=None, which=None, cwd=None, program_files=None):
    """(bin_dir, source, searched) for the first folder that holds `want`.

    searched lists (source, folder, matched) for every folder looked at.
    Raises RvNotFound when nothing matches, an explicit --rv-bin / config rv_bin is wrong or
    the config file is broken.
    """
    if home is None:
        home = Path.home()
    searched = []
    try:
        for source, folders in iter_candidates(rv_bin, config, platform, home, root, registry,
                                               which, cwd, program_files):
            for folder in folders:
                hit = tool_in(folder, want, platform)
                searched.append((source, str(folder), bool(hit)))
                if hit:
                    return Path(folder), source, searched
            if source in EXPLICIT_SOURCES:
                where = "--rv-bin" if source == "--rv-bin" else f"rv_bin in {config_path(home)}"
                raise RvNotFound(
                    f"{where} points at {folders[0]}, but no {exe_names(want, platform)[0]} "
                    f"was found there or in its bin folder. Point it at the RV bin folder "
                    f"(<install>/bin, or RV.app/Contents/MacOS on macOS).")
    except ConfigError as exc:
        raise RvNotFound(f"config file problem: {exc}") from None
    raise RvNotFound(
        f"{want} not found. Tried --rv-bin, rv_bin in {config_path(home)}, PATH, the Windows "
        f"registry, the usual install folders and an OpenRV build (~/OpenRV or a folder "
        f"above this one holding {OPENRV_MARKER}). Install RV or OpenRV, or pass --rv-bin "
        f"<RV bin folder>, or put it in that config file as \"rv_bin\".")


def no_window_flags():
    """subprocess creationflags that stop a console window flashing up on Windows."""
    return 0x08000000 if os.name == "nt" else 0     # CREATE_NO_WINDOW


def tool_version(path, timeout=VERSION_TIMEOUT_S):
    """'X.Y.Z' from `<tool> -version`, or None. Only call this for command-line tools."""
    try:
        r = subprocess.run([str(path), "-version"], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, errors="replace",
                           timeout=timeout, creationflags=no_window_flags())
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = VERSION_RE.search((r.stdout or "") + (r.stderr or ""))
    return m.group(0) if m else None


def locate(want="rvio", rv_bin=None, versions=True, **where):
    """Full report as a dict (see the module docstring). where: find_rv's keyword arguments
    (config, platform, home, root, registry, which, cwd, program_files)."""
    report = {"found": False, "want": want, "platform": os_kind(where.get("platform")),
              "bin_dir": None, "source": None, "tools": {}, "versions": {}, "searched": []}
    try:
        folder, source, searched = find_rv(want, rv_bin, **where)
    except RvNotFound as exc:
        report["error"] = str(exc)
        return report
    report.update(found=True, bin_dir=str(folder), source=source,
                  tools=tools_in(folder, where.get("platform")), searched=searched)
    if versions:
        for t in VERSION_TOOLS:
            if report["tools"].get(t):
                report["versions"][t] = tool_version(report["tools"][t])
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Find the RV / OpenRV command-line tools (rv, rvio, rvls, rvpkg, rvpush) "
                    "and print their paths and versions as JSON.",
        epilog="Search order: --rv-bin, rv_bin in ~/.config/tvr-skills-rv/config.json, PATH, "
               "the Windows registry, the usual install folders, then an OpenRV build "
               "(~/OpenRV or a folder above the current one holding rvcmds.sh). "
               "Exit 0 found, 1 not found.")
    ap.add_argument("--rv-bin", help="RV bin folder, install root, .app bundle or tool path")
    ap.add_argument("--want", default="rvio", choices=CORE_TOOLS + EXTRA_TOOLS,
                    help="tool the chosen folder must hold (default: rvio)")
    ap.add_argument("--path", metavar="TOOL", choices=CORE_TOOLS + EXTRA_TOOLS,
                    help="print only this tool's path (implies --want TOOL and no versions)")
    ap.add_argument("--no-version", action="store_true",
                    help="skip running rvio/rvls -version")
    args = ap.parse_args(argv)

    want = args.path or args.want
    report = locate(want, args.rv_bin, versions=not (args.no_version or args.path))
    if args.path:
        if report["found"] and report["tools"].get(args.path):
            print(report["tools"][args.path])
            return 0
        print(report.get("error", f"{args.path} not found"), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    if not report["found"]:
        print(report["error"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
