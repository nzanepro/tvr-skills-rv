#!/usr/bin/env python3
"""Show RV packages as JSON: what is available, installed, loaded and optional, and where.

    python rvpkg_list.py [--name TEXT] [--include DIR ...] [--only DIR] [--info]
    python rvpkg_list.py --parse FILE          # parse saved "rvpkg -list" output

Runs "rvpkg -list" (and "rvpkg -env" for the support areas) and turns lines such as
    I L - 1.2 "Additional RV Nodes" /path/Packages/additional_nodes-1.2.rvpkg
into records. The three flag columns are I = installed, L = loaded (RV will load it: installed
and, if optional, opted in), O = optional (loads only after opt-in); "-" means no. --info adds the fields of "rvpkg -info <path>" (author, requires, modes, files,
writable, ...) for each listed package.

Use it before and after -add / -install / -uninstall / -optin / -remove to confirm the change:
rvpkg itself exits 0 and prints "No matching packages found" when a name does not match.

Output:
  {"support_areas": ["/home/me/.rv/Packages", ...],
   "packages": [{"installed": true, "loaded": true, "optional": false, "version": "1.2",
                 "name": "Additional RV Nodes", "path": ".../additional_nodes-1.2.rvpkg",
                 "file": "additional_nodes-1.2.rvpkg", "area": ".../Packages"}]}

Exit status: 0 listed (also when the filter matches nothing; check "packages"); 1 rvpkg
failed; 127 rvpkg not found; 2 bad arguments.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rv_tool  # noqa: E402  (sibling script)

LIST_RE = re.compile(r'^(?P<i>[I-])\s+(?P<l>[L-])\s+(?P<o>[O-])\s+(?P<version>\S+)\s+'
                     r'"(?P<name>[^"]*)"\s+(?P<path>.+?)\s*$')
INFO_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z-]*):\s?(?P<value>.*)$")


def _split_path(path):
    cut = max(path.rfind("/"), path.rfind("\\"))
    return (path[:cut], path[cut + 1:]) if cut >= 0 else ("", path)


def parse_list(text):
    """Package records from "rvpkg -list" output; other lines are ignored."""
    out = []
    for line in text.splitlines():
        m = LIST_RE.match(line.strip())
        if not m:
            continue
        area, file = _split_path(m.group("path"))
        out.append({"installed": m.group("i") == "I", "loaded": m.group("l") == "L",
                    "optional": m.group("o") == "O", "version": m.group("version"),
                    "name": m.group("name"), "path": m.group("path"), "file": file,
                    "area": area})
    return out


def parse_info(text):
    """List of dicts from "rvpkg -info" output (one block per matching package)."""
    blocks, cur = [], None
    for line in text.splitlines():
        m = INFO_RE.match(line.rstrip())
        if not m:
            continue
        key = m.group("key").lower().replace("-", "_")
        if key == "name":
            cur = {}
            blocks.append(cur)
        if cur is None:
            continue
        val = m.group("value").strip()
        if val in ("YES", "NO"):
            val = val == "YES"
        cur[key] = val
    return blocks


def area_args(a):
    args = []
    for d in a.include or []:
        args += ["-include", d]
    if a.only:
        args += ["-only", a.only]
    return args


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="List RV packages (rvpkg -list) as JSON with installed / loaded / "
                    "optional flags, support areas and optional -info details.",
        epilog="Exit: 0 listed, 1 rvpkg failed, 127 rvpkg not found.")
    ap.add_argument("--name", help="keep packages whose name or file contains this text "
                                   "(case-insensitive)")
    ap.add_argument("--include", action="append", metavar="DIR",
                    help="also look in this support area (rvpkg -include)")
    ap.add_argument("--only", metavar="DIR",
                    help="use this support area instead of RV_SUPPORT_PATH (rvpkg -only); "
                         "the install's own Packages folder is still listed")
    ap.add_argument("--info", action="store_true", help="add rvpkg -info details")
    ap.add_argument("--parse", metavar="FILE", help="parse saved -list output ('-' = stdin)")
    ap.add_argument("--rv-bin", help="RV bin folder (see rv_find.py)")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds per rvpkg call")
    a = ap.parse_args(argv)

    report = {"support_areas": [], "packages": []}
    if a.parse:
        text = sys.stdin.read() if a.parse == "-" else Path(a.parse).read_text(errors="replace")
        report["packages"] = parse_list(text)
    else:
        res = rv_tool.run("rvpkg", area_args(a) + ["-list"], a.rv_bin, a.timeout, strict=False)
        if res["exit"] == 127:
            print(res["message"], file=sys.stderr)
            return 127
        if res["exit"] != 0:
            print(res["message"] + "\n" + res["stderr"], file=sys.stderr)
            return 1
        report["packages"] = parse_list(res["stdout"])
        env = rv_tool.run("rvpkg", area_args(a) + ["-env"], a.rv_bin, a.timeout, strict=False)
        report["support_areas"] = [l.strip() for l in env["stdout"].splitlines() if l.strip()]
    if a.name:
        t = a.name.lower()
        report["packages"] = [p for p in report["packages"]
                              if t in p["name"].lower() or t in p["file"].lower()]
    if a.info and not a.parse:
        for p in report["packages"]:
            r = rv_tool.run("rvpkg", area_args(a) + ["-info", p["path"]], a.rv_bin,
                            a.timeout, strict=False)
            blocks = parse_info(r["stdout"])
            p["info"] = blocks[0] if blocks else None
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
