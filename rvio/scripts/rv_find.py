#!/usr/bin/env python3
"""Find the RV / OpenRV command-line tools and print where they are as JSON.

Looks for one RV bin folder and reports the tools in it: rv, rvio, rvls, rvpkg, rvpush, plus
rvio_hw (Autodesk RV), rvio_sw (Linux OpenRV), mu-interp and py-interp when present.
Standard library only; works on Windows, macOS and Linux. This file is identical in every
tvr-skills-rv command-line skill.

Search order (first folder that holds the wanted tool wins):
  1. --rv-bin PATH               a bin folder, an install root, an .app bundle or a tool path
  2. RV_BIN                      same forms as --rv-bin
  3. RVPUSH_RV_EXECUTABLE_PATH   the rv executable rvpush starts (ignored when "none")
  4. RV_PATH                     rv executable (the convention RV's Nuke integration reads)
  5. RV_APP_RV                   rv executable; RV sets it for processes it starts
  6. RV_HOME                     install root: RV_HOME/bin, or RV_HOME/Contents/MacOS for an .app
  7. PATH
  8. Windows only: the App Paths registry key for rv.exe that RV's .reg files add
  9. usual install folders, newest version first:
       Windows  Program Files: OpenRV*, Autodesk/RV*, ShotGrid/RV*, Shotgun/RV*, ShotGrid RV*,
                Shotgun RV* (each with bin)
       macOS    /Applications and ~/Applications: RV*.app, OpenRV*.app (Contents/MacOS)
       Linux    /opt/rv*, /opt/RV*, /opt/OpenRV*, /opt/openrv*, /usr/local/rv*,
                /usr/local/OpenRV* (each with bin), then /usr/local/bin

An explicit --rv-bin or RV_BIN that does not hold the wanted tool is an error, not skipped.

Output (stdout, one JSON object):
  {"found": true, "bin_dir": "...", "source": "PATH", "platform": "linux",
   "tools": {"rv": "...", "rvio": "...", "rvls": "...", "rvpkg": "...", "rvpush": "...", ...},
   "versions": {"rvio": "3.1.0", "rvls": "3.1.0"}, "searched": [["PATH", "...", false], ...]}
Versions come from "rvio -version" and "rvls -version" (command-line only; rv itself is never
started because it opens a window). --path TOOL prints only that tool's path.

Exit status: 0 found; 1 not found or a wrong --rv-bin / RV_BIN; 2 bad arguments.
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


class RvNotFound(RuntimeError):
    """RV could not be found; the message says what was tried and what to do."""


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


def install_patterns(platform=None, env=None, home=None, root="/"):
    """Glob patterns for the usual install bin folders, in the order they are tried."""
    env = os.environ if env is None else env
    kind = os_kind(platform)
    if kind == "windows":
        pats = []
        for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = env.get(var)
            if not base:
                continue
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


def home_bin(rv_home):
    """Bin folder under an install root; RV_HOME is the .app bundle on macOS."""
    h = Path(rv_home)
    if h.suffix == ".app":
        return h / "Contents" / "MacOS"
    return h / "bin"


def candidates(rv_bin=None, env=None, platform=None, home=None, root="/", registry=None):
    """[(source, [folders])] in lookup order."""
    env = os.environ if env is None else env
    kind = os_kind(platform)
    out = []
    if rv_bin:
        out.append(("--rv-bin", bin_dirs_for(rv_bin, platform)))
    if env.get("RV_BIN"):
        out.append(("RV_BIN", bin_dirs_for(env["RV_BIN"], platform)))
    for var in ("RVPUSH_RV_EXECUTABLE_PATH", "RV_PATH", "RV_APP_RV"):   # each names rv itself
        exe = env.get(var, "").strip().strip('"')
        if exe and exe.lower() != "none":
            out.append((var, [Path(exe).parent]))
    if env.get("RV_HOME"):
        out.append(("RV_HOME", [home_bin(env["RV_HOME"])]))
    path_var = env.get("PATH", "")
    seen = set()
    for tool in ("rvio", "rv", "rvls", "rvpkg", "rvpush"):
        for name in exe_names(tool, platform):
            hit = shutil.which(name, path=path_var)
            if hit and str(Path(hit).parent) not in seen:
                seen.add(str(Path(hit).parent))
                out.append(("PATH", [Path(hit).parent]))
    if kind == "windows":
        reg = (registry or registry_rv)()
        if reg:
            out.append(("registry", [Path(reg).parent]))
    for pat in install_patterns(platform, env, home, root):
        for hit in sorted(glob.glob(pat), key=natural_key, reverse=True):
            out.append(("install folder", [Path(hit)]))
    return out


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


def find_rv(want="rvio", rv_bin=None, env=None, platform=None, home=None, root="/",
            registry=None):
    """(bin_dir, source, searched) for the first folder that holds `want`.

    searched lists (source, folder, matched) for every folder looked at.
    Raises RvNotFound when nothing matches or an explicit --rv-bin / RV_BIN is wrong.
    """
    if home is None:
        home = Path.home()
    searched = []
    for source, folders in candidates(rv_bin, env, platform, home, root, registry):
        for folder in folders:
            hit = tool_in(folder, want, platform)
            searched.append((source, str(folder), bool(hit)))
            if hit:
                return Path(folder), source, searched
        if source in ("--rv-bin", "RV_BIN"):
            raise RvNotFound(
                f"{source} points at {folders[0]}, but no {exe_names(want, platform)[0]} was "
                f"found there or in its bin folder. Point it at the RV bin folder "
                f"(<install>/bin, or RV.app/Contents/MacOS on macOS).")
    raise RvNotFound(
        f"{want} not found. Tried --rv-bin, RV_BIN, RVPUSH_RV_EXECUTABLE_PATH, RV_PATH, "
        f"RV_APP_RV, RV_HOME, PATH, the Windows registry and the usual install folders. "
        f"Install RV or OpenRV, or pass --rv-bin <RV bin folder>, or set RV_BIN to it.")


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


def locate(want="rvio", rv_bin=None, versions=True, env=None, platform=None, home=None,
           root="/", registry=None):
    """Full report as a dict (see the module docstring)."""
    report = {"found": False, "want": want, "platform": os_kind(platform), "bin_dir": None,
              "source": None, "tools": {}, "versions": {}, "searched": []}
    try:
        folder, source, searched = find_rv(want, rv_bin, env, platform, home, root, registry)
    except RvNotFound as exc:
        report["error"] = str(exc)
        return report
    report.update(found=True, bin_dir=str(folder), source=source,
                  tools=tools_in(folder, platform), searched=searched)
    if versions:
        for t in VERSION_TOOLS:
            if report["tools"].get(t):
                report["versions"][t] = tool_version(report["tools"][t])
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Find the RV / OpenRV command-line tools (rv, rvio, rvls, rvpkg, rvpush) "
                    "and print their paths and versions as JSON.",
        epilog="Search order: --rv-bin, RV_BIN, RVPUSH_RV_EXECUTABLE_PATH, RV_PATH, RV_APP_RV, "
               "RV_HOME, PATH, the Windows registry, then the usual install folders. "
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
