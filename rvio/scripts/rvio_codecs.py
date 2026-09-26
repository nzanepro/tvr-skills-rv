#!/usr/bin/env python3
"""Find out which movie codecs this rvio can really write.

    python rvio_codecs.py [--container mov] [--codec NAME ...] [--rv-bin DIR]

"rvio -formats" lists only a short fixed set of encoders, and what an OpenRV build can write
depends on how its FFmpeg was configured (stock builds leave out ProRes, DNxHD, MPEG-2 and
AAC; some builds add them back). This script encodes two frames of generated colour bars
(a .movieproc source, so no input files are needed) with each codec into a temporary folder,
reads nothing else, deletes the files, and prints JSON:

  {"rvio": "/path/to/rvio", "container": "mov",
   "codecs": {"mjpeg": {"ok": true, "seconds": 1.1},
              "libx264": {"ok": false, "error": "ERROR: Invalid video codec: libx264"}}}

dnxhd is tried at 1920x1080 with a 36 Mbit/s rate (its only accepted shapes are fixed
profiles). Each attempt takes about a second.

Exit status: 0 the probe ran (some codecs may have failed); 127 rvio not found; 2 bad arguments.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rv_tool  # noqa: E402  (sibling script)

DEFAULT_CODECS = ("mjpeg", "mpeg4", "png", "prores_ks", "prores_aw", "dnxhd", "mpeg2video",
                  "mpeg1video", "dvvideo", "cfhd", "v210", "v410", "jpeg2000", "tiff",
                  "libx264", "h264", "hevc", "libx265", "libvpx-vp9", "libaom-av1", "qtrle",
                  "prores")
BARS = "smptebars,start=1,end=2,fps=24,width={w},height={h}.movieproc"


def probe(codec, container, folder, rv_bin=None, timeout=120.0):
    """{'ok': bool, 'seconds': float, 'error': str} for one codec."""
    w, h, extra = 64, 64, []
    if codec == "dnxhd":
        w, h, extra = 1920, 1080, ["-outparams", "vcc:b=36000000"]
    if codec == "dvvideo":
        w, h = 720, 576
    out = Path(folder) / f"probe_{codec}.{container}"
    res = rv_tool.run("rvio", [BARS.format(w=w, h=h), "-codec", codec, "-o", str(out)] + extra,
                      rv_bin=rv_bin, timeout=timeout)
    if res["exit"] == 127:
        raise FileNotFoundError(res["message"])
    ok = res["exit"] == 0 and out.is_file() and out.stat().st_size > 0
    entry = {"ok": ok, "seconds": res["elapsed_s"]}
    if not ok:
        entry["error"] = (res["error_lines"] or [res["message"]])[0]
    try:
        out.unlink()
    except OSError:
        pass
    return entry


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Encode two frames of colour bars with each codec to see which movie "
                    "codecs this rvio build can write.",
        epilog="Exit: 0 probe ran, 127 rvio not found.")
    ap.add_argument("--container", default="mov", help="movie extension to test (default mov)")
    ap.add_argument("--codec", action="append",
                    help="codec to try, repeatable (default: a list of common ones)")
    ap.add_argument("--rv-bin", help="RV bin folder (see rv_find.py)")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds per attempt")
    args = ap.parse_args(argv)

    codecs = args.codec or list(DEFAULT_CODECS)
    report = {"container": args.container, "codecs": {}}
    with tempfile.TemporaryDirectory(prefix="rvio_codecs_") as tmp:
        for c in codecs:
            try:
                report["codecs"][c] = probe(c, args.container, tmp, args.rv_bin, args.timeout)
            except FileNotFoundError as exc:
                print(str(exc), file=sys.stderr)
                return 127
    report["writable"] = [c for c, r in report["codecs"].items() if r["ok"]]
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
