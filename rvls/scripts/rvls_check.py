#!/usr/bin/env python3
"""List image sequences and movies with rvls, as JSON, and check them against expectations.

    python rvls_check.py PATH [PATH ...] [--expect-range 1001-1100] [--expect-count N]
                         [--expect-res 1920x1080] [--expect-channels 4] [--no-gaps]

PATH is a folder, a file, or a sequence spec (name.#.exr, name.@@@@.exr, name.%04d.exr,
name.1001-1100#.exr). rvls itself does not accept sequence specs, so for a spec this script
lists the parent folder and keeps the matching sequence.

Why: "rvls -l" prints a range such as plate.1-5,8-10#.exr and a #fr column that counts the
span (10), not the files (8), and it exits 0 even when a path does not exist. This script
expands every range into frames, reports missing frames, and fails when an expectation is not
met, so it is a reliable check after rvio, a render or a copy.

Output (stdout):
  {"ok": true, "problems": [], "entries": [
     {"spec": "out/plate.1-5,8-10#.exr", "pattern": "out/plate.#.exr", "sequence": true,
      "first": 1, "last": 10, "count": 8, "span": 10, "missing": [6, 7], "padding": 4,
      "width": 64, "height": 64, "type": "16f", "channels": 4, "fps": 0.0,
      "frames_reported": 10, "audio_channels": null, "readable": true}]}
For a movie, count and span are the frames inside it and first / last are null.

--parse FILE reads saved "rvls -l" (or plain rvls) output instead of running rvls ("-" for
stdin), which also makes this script usable without RV.

Exit status: 0 listed and every expectation met; 1 an expectation failed or nothing matched;
127 rvls not found; 2 bad arguments.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rv_tool  # noqa: E402  (sibling script)

_ITEM = r"-?\d+(?:--?\d+(?:x\d+)?)?"
# rvls writes sequences as prefix + range list + padding (# = 4 digits, @@@ = 3) + suffix
RVLS_SEQ_RE = re.compile(r"^(?P<prefix>.*?)(?P<ranges>" + _ITEM + r"(?:," + _ITEM + r")*)"
                         r"(?P<pad>#|@+)(?P<suffix>[^#@/\\]*)$")
# user specs may also use ####, %04d or no range
SPEC_TOKEN_RE = re.compile(r"(?P<ranges>" + _ITEM + r"(?:," + _ITEM + r")*)?"
                           r"(?P<pad>#+|@+|%0?(?P<w>\d*)d)")


def parse_ranges(text):
    """Frames listed by a range list such as '1-5,8-10', '1-9x2' or '-2-1'."""
    frames = []
    for item in text.split(","):
        m = re.fullmatch(r"(-?\d+)(?:-(-?\d+)(?:x(\d+))?)?", item.strip())
        if not m:
            raise ValueError(f"cannot read frame range '{item}'")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) is not None else a
        step = int(m.group(3)) if m.group(3) else 1
        frames.extend(range(a, b + 1, step) if b >= a else range(a, b - 1, -step))
    return sorted(set(frames))


def pad_digits(pad):
    """# -> 4, #### -> 4, @@@ -> 3, @ -> 1, %05d -> 5, %d -> 0."""
    if pad == "#":
        return 4
    if pad[0] in "#@":
        return len(pad)
    w = re.fullmatch(r"%0?(\d*)d", pad).group(1)
    return int(w) if w else 0


def parse_sequence_name(name):
    """Split an rvls sequence name; None for a plain file name."""
    cut = max(name.rfind("/"), name.rfind("\\")) + 1      # keep rvls's own separator
    head, base = name[:cut], name[cut:]
    m = RVLS_SEQ_RE.match(base)
    if not m:
        return None
    try:
        frames = parse_ranges(m.group("ranges"))
    except ValueError:
        return None
    prefix = head + m.group("prefix")
    return {"prefix": prefix, "suffix": m.group("suffix"), "frames": frames,
            "padding": pad_digits(m.group("pad")), "pad_token": m.group("pad")}


def describe(name):
    """Entry dict with frame facts for one rvls name."""
    seq = parse_sequence_name(name)
    if not seq:
        return {"spec": name, "pattern": name, "sequence": False, "first": None,
                "last": None, "count": 1, "span": 1, "missing": [], "padding": None}
    fr = seq["frames"]
    have = set(fr)
    missing = [f for f in range(fr[0], fr[-1] + 1) if f not in have]
    return {"spec": name, "pattern": seq["prefix"] + seq["pad_token"] + seq["suffix"],
            "sequence": True, "first": fr[0], "last": fr[-1], "count": len(fr),
            "span": fr[-1] - fr[0] + 1, "missing": missing, "padding": seq["padding"],
            "frames": fr}


def _columns(header):
    """[(name, start, end)] for each header word of an rvls -l header line."""
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", header)]


def parse_long_line(line, header):
    """Values from one rvls -l row, placed by the column they sit under."""
    file_col = header.index("file")
    name = line[file_col:].strip() if len(line) > file_col else ""
    left = line[:file_col]
    if not name or (file_col > 0 and len(line) > file_col and line[file_col - 1] != " "):
        # row wider than the header: fall back to splitting on runs of spaces
        m = re.match(r"^\s*(\d+)\s+x\s+(\d+)\s+(\S+)\s+(\d+)\s+(?:([\d.]+)\s+)?(?:(\d+)\s+)?"
                     r"(?:(\d+)\s+)?(\S.*)$", line)
        if not m:
            return None
        w, h, typ, ch, fps, fr, ach, name = m.groups()
        return {"name": name.strip(), "width": int(w), "height": int(h), "type": typ,
                "channels": int(ch), "fps": float(fps) if fps else None,
                "frames_reported": int(fr) if fr else None,
                "audio_channels": int(ach) if ach else None}
    cols = [c for c in _columns(header) if c[1] < file_col]
    vals = {}
    for m in re.finditer(r"\S+", left):
        if m.group(0) == "x":
            continue
        centre = (m.start() + m.end()) / 2.0
        best = min((c for c in cols if c[0] != "x"),
                   key=lambda c: min(abs(centre - c[1]), abs(centre - (c[2] - 1)),
                                     0 if c[1] <= centre <= c[2] else 1e9))
        vals.setdefault(best[0], m.group(0))

    def num(key, cast):
        try:
            return cast(vals[key]) if key in vals else None
        except ValueError:
            return None
    return {"name": name, "width": num("w", int), "height": num("h", int),
            "type": vals.get("typ"), "channels": num("#ch", int), "fps": num("fps", float),
            "frames_reported": num("#fr", int), "audio_channels": num("#ach", int)}


def parse_rvls_output(text):
    """Entries from rvls or rvls -l output (INFO / ERROR / WARNING lines are skipped)."""
    entries, header = [], None
    for line in text.splitlines():
        s = line.strip()
        if not s or re.match(r"^(INFO|ERROR|WARNING|Importing)\b", s):
            continue
        if re.match(r"^w\s+x\s+h\s+typ", s):
            header = line
            continue
        if header is not None:
            row = parse_long_line(line, header)
            if row is None:
                continue
            e = describe(row.pop("name"))
            e.update(row)
            if not e["sequence"] and row.get("frames_reported"):
                e["count"] = e["span"] = row["frames_reported"]    # frames inside a movie
            e["readable"] = bool(row.get("width") or row.get("audio_channels"))
        else:
            e = describe(s)
        entries.append(e)
    return entries


def spec_matcher(spec):
    """(folder, predicate(entry)) selecting the rvls entry for a user sequence spec."""
    folder, base = os.path.split(spec)
    matches = list(SPEC_TOKEN_RE.finditer(base))
    if not matches:
        return None
    m = matches[-1]
    prefix, suffix = base[: m.start()], base[m.end():]
    want = parse_ranges(m.group("ranges")) if m.group("ranges") else None

    def pred(entry):
        if not entry["sequence"]:
            return False
        seq = parse_sequence_name(entry["spec"])
        if seq is None:
            return False
        cut = max(seq["prefix"].rfind("/"), seq["prefix"].rfind("\\")) + 1
        return seq["prefix"][cut:] == prefix and seq["suffix"] == suffix
    return (folder or "."), pred, want


def check(entries, a):
    """Problems (strings) for entries against the --expect options."""
    problems = []
    if not entries:
        return ["nothing matched: no file or sequence found"]
    for e in entries:
        label = e["spec"]
        if a.expect_range:
            lo, hi = parse_ranges(a.expect_range)[0], parse_ranges(a.expect_range)[-1]
            if e["first"] is None or e["first"] != lo or e["last"] != hi:
                problems.append(f"{label}: frames {e['first']}-{e['last']}, expected {lo}-{hi}")
        if a.expect_count is not None and e["count"] != a.expect_count:
            problems.append(f"{label}: {e['count']} frame(s), expected {a.expect_count}")
        if a.no_gaps and e["missing"]:
            shown = ", ".join(str(f) for f in e["missing"][:20])
            more = " ..." if len(e["missing"]) > 20 else ""
            problems.append(f"{label}: {len(e['missing'])} missing frame(s): {shown}{more}")
        if a.expect_res:
            w, h = (int(v) for v in a.expect_res.lower().split("x"))
            if (e.get("width"), e.get("height")) != (w, h):
                problems.append(f"{label}: {e.get('width')}x{e.get('height')}, expected "
                                f"{w}x{h}")
        if a.expect_channels is not None and e.get("channels") != a.expect_channels:
            problems.append(f"{label}: {e.get('channels')} channel(s), expected "
                            f"{a.expect_channels}")
        if getattr(a, "readable", False) and e.get("readable") is False:
            problems.append(f"{label}: rvls could not read it (damaged or not an image)")
        if a.expect_type and e.get("type") != a.expect_type:
            problems.append(f"{label}: pixel type {e.get('type')}, expected {a.expect_type}")
    return problems


def list_paths(paths, rv_bin=None, min_seq=None, timeout=300.0):
    """Run rvls -l for each path; (entries, errors). Specs list their folder and filter."""
    entries, errors = [], []
    for p in paths:
        m = spec_matcher(p)
        target = m[0] if m else p
        if not os.path.exists(target):
            errors.append(f"{p}: does not exist")
            continue
        args = ["-l"] + (["-min", str(min_seq)] if min_seq else []) + [target]
        res = rv_tool.run("rvls", args, rv_bin=rv_bin, timeout=timeout)
        if res["exit"] == 127:
            raise FileNotFoundError(res["message"])
        found = parse_rvls_output(res["stdout"])
        if m:
            folder, pred, want = m
            found = [e for e in found if pred(e)]
            if want:
                for e in found:
                    keep = [f for f in e["frames"] if f in set(want)]
                    missing = [f for f in want if f not in set(e["frames"])]
                    e.update(first=keep[0] if keep else None, last=keep[-1] if keep else None,
                             count=len(keep), missing=missing)
            if not found:
                errors.append(f"{p}: no matching sequence in {folder}")
        entries += found
    return entries, errors


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="List sequences and movies with rvls as JSON (frames, gaps, size, "
                    "channels) and check them against expectations.",
        epilog="Example: rvls_check.py renders/shot.#.exr --expect-range 1001-1100 --no-gaps "
               "--expect-res 1920x1080   Exit: 0 ok, 1 check failed, 127 rvls not found.")
    ap.add_argument("paths", nargs="*", help="folders, files or sequence specs")
    ap.add_argument("--parse", metavar="FILE", help="parse saved rvls output ('-' = stdin)")
    ap.add_argument("--expect-range", help="first-last frame, e.g. 1001-1100")
    ap.add_argument("--expect-count", type=int, help="number of frames on disk")
    ap.add_argument("--expect-res", help="WIDTHxHEIGHT, e.g. 1920x1080")
    ap.add_argument("--expect-channels", type=int, help="channel count, e.g. 3 or 4")
    ap.add_argument("--expect-type", help="rvls pixel type, e.g. 8i, 16i, 16f, 32f")
    ap.add_argument("--no-gaps", action="store_true", help="fail when frames are missing")
    ap.add_argument("--readable", action="store_true",
                    help="fail when rvls could not read a file (0 x 0, no audio)")
    ap.add_argument("--min", type=int, dest="min_seq",
                    help="files needed to form a sequence (rvls default 3)")
    ap.add_argument("--rv-bin", help="RV bin folder (see rv_find.py)")
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per rvls call")
    ap.add_argument("--frames", action="store_true", help="include every frame number")
    a = ap.parse_args(argv)
    if not a.paths and not a.parse:
        ap.error("give PATHs or --parse FILE")

    errors = []
    if a.parse:
        text = sys.stdin.read() if a.parse == "-" else Path(a.parse).read_text(errors="replace")
        entries = parse_rvls_output(text)
    else:
        try:
            entries, errors = list_paths(a.paths, a.rv_bin, a.min_seq, a.timeout)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 127
    problems = errors + check(entries, a)
    if not a.frames:
        for e in entries:
            e.pop("frames", None)
    print(json.dumps({"ok": not problems, "problems": problems, "entries": entries}, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
