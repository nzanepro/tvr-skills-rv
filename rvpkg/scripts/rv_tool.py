#!/usr/bin/env python3
"""Run one RV command-line tool safely and report failures clearly.

    python rv_tool.py [options] TOOL [--] ARG ...

TOOL is rvio, rvls, rvpkg, rvpush, rvio_hw, rvio_sw, mu-interp or py-interp. Arguments are
passed as a list (no shell, so spaces, brackets and # need no extra quoting), stdin is closed
(rvpkg cannot sit waiting for a y/n answer), a timeout applies, and on Windows no console
window flashes up. rv, rvprof and rvshell open windows and are refused.

Why a wrapper: rvio and rvpkg often exit 0 after a failure. rvio given a missing input prints
"ERROR: Open of '...' failed" and still writes a placeholder movie; rvpkg prints "No matching
packages found" and exits 0. This script treats those lines as failures (exit 3) for rvio,
rvio_hw, rvio_sw and rvpkg. For rvls they are reported but not fatal, because rvls prints an
ERROR for every non-image file in a folder. --strict / --allow-errors override the default.

Output: the tool's stdout and stderr are passed through (rvio's per-frame "Writing frame"
progress lines are dropped unless --progress), then one summary line on stderr. --json prints
a single JSON object instead:
  {"tool": "rvio", "argv": [...], "returncode": 0, "exit": 0, "elapsed_s": 1.2,
   "error_lines": [], "stdout": "...", "stderr": "..."}

Exit status: 0 OK; 1 the tool exited non-zero (its own code is in the summary and in
"returncode"); 3 the tool exited 0 but printed errors; 124 timed out; 127 tool not found;
2 bad arguments or a window-opening tool.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rv_find  # noqa: E402  (sibling script, not an installed package)

CLI_TOOLS = ("rvio", "rvls", "rvpkg", "rvpush", "rvio_hw", "rvio_sw", "mu-interp",
             "py-interp")
GUI_TOOLS = ("rv", "rvprof", "rvshell")
STRICT_DEFAULT = {"rvio": True, "rvio_hw": True, "rvio_sw": True, "rvpkg": True}
PROGRESS_MARKERS = ("Writing frame",)
DEFAULT_TIMEOUT_S = 1800.0


def error_lines(text):
    """Lines that report a failure even when the exit code is 0."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("ERROR:") or s == "No matching packages found" or s == "exiting":
            out.append(s)
    return out


def drop_progress(text):
    """text without rvio's per-frame progress lines."""
    return "\n".join(l for l in (text or "").splitlines()
                     if not any(m in l for m in PROGRESS_MARKERS))


def decide_exit(tool, returncode, errors, strict=None):
    """(exit code, message) for a finished run."""
    if strict is None:
        strict = STRICT_DEFAULT.get(tool, False)
    if returncode != 0:
        first = f": {errors[0]}" if errors else ""
        # rvio exits with -1, which shows up as 255 or 4294967295 depending on the OS
        shown = returncode - 2 ** 32 if returncode >= 2 ** 31 else returncode
        return 1, f"{tool} failed with exit code {shown}{first}"
    if errors and strict:
        return 3, (f"{tool} exited 0 but reported {len(errors)} error(s); treat the output as "
                   f"bad (rvio may have written a placeholder). First: {errors[0]}")
    if errors:
        return 0, f"{tool} finished with {len(errors)} reported error(s) (not fatal for {tool})"
    return 0, f"{tool} finished OK"


def run(tool, args, rv_bin=None, timeout=DEFAULT_TIMEOUT_S, cwd=None, strict=None, env=None):
    """Run tool with args; return the JSON-able result dict (never raises for tool failures)."""
    result = {"tool": tool, "argv": None, "returncode": None, "exit": None, "elapsed_s": None,
              "error_lines": [], "stdout": "", "stderr": "", "message": ""}
    report = rv_find.locate(tool, rv_bin, versions=False)
    if not report["found"] or not report["tools"].get(tool):
        result.update(exit=127, message=report.get("error", f"{tool} not found"))
        return result
    argv = [report["tools"][tool]] + [str(a) for a in args]
    result["argv"] = argv
    t0 = time.monotonic()
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              errors="replace", timeout=timeout, cwd=cwd, env=env,
                              creationflags=rv_find.no_window_flags())
    except subprocess.TimeoutExpired as exc:
        result.update(elapsed_s=round(time.monotonic() - t0, 3), exit=124,
                      stdout=exc.stdout if isinstance(exc.stdout, str) else "",
                      stderr=exc.stderr if isinstance(exc.stderr, str) else "",
                      message=f"{tool} timed out after {timeout:g} s and was stopped; any "
                              f"output it was writing is incomplete. Raise --timeout for "
                              f"long jobs.")
        return result
    except OSError as exc:
        result.update(exit=127, message=f"could not start {argv[0]}: {exc}")
        return result
    errors = error_lines(proc.stdout) + error_lines(proc.stderr)
    code, message = decide_exit(tool, proc.returncode, errors, strict)
    result.update(returncode=proc.returncode, exit=code, error_lines=errors,
                  elapsed_s=round(time.monotonic() - t0, 3), stdout=proc.stdout,
                  stderr=proc.stderr, message=message)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Run an RV command-line tool (rvio, rvls, rvpkg, rvpush, ...) with an "
                    "argument list, closed stdin and a timeout, and fail loudly when it prints "
                    "errors but exits 0.",
        epilog="Example: python rv_tool.py rvio -- in.#.exr -outsrgb -o out.mov   "
               "Exit: 0 OK, 1 tool failed, 3 errors printed with exit 0, 124 timeout, "
               "127 not found.")
    ap.add_argument("tool", choices=CLI_TOOLS + GUI_TOOLS, help="tool to run")
    ap.add_argument("args", nargs=argparse.REMAINDER, help="arguments for the tool (after --)")
    ap.add_argument("--rv-bin", help="RV bin folder or install root (see rv_find.py)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                    help=f"seconds before the tool is stopped (default {DEFAULT_TIMEOUT_S:g})")
    ap.add_argument("--cwd", help="working directory for the tool")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--strict", dest="strict", action="store_true", default=None,
                   help="exit 3 when ERROR lines appear even though the tool exited 0")
    g.add_argument("--allow-errors", dest="strict", action="store_false",
                   help="report ERROR lines but keep the tool's exit code")
    ap.add_argument("--json", action="store_true", help="print one JSON result object")
    ap.add_argument("--progress", action="store_true",
                    help="keep rvio's per-frame 'Writing frame' lines")
    args = ap.parse_args(argv)

    if args.tool in GUI_TOOLS:
        print(f"{args.tool} opens a window; this wrapper only runs command-line tools. Use the "
              f"rv-review skill to show media in RV.", file=sys.stderr)
        return 2
    tool_args = args.args[1:] if args.args[:1] == ["--"] else args.args
    res = run(args.tool, tool_args, args.rv_bin, args.timeout, args.cwd, args.strict)
    if args.json:
        print(json.dumps(res, indent=2))
        return res["exit"]
    out = res["stdout"] if args.progress else drop_progress(res["stdout"])
    err = res["stderr"] if args.progress else drop_progress(res["stderr"])
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print(err.rstrip(), file=sys.stderr)
    took = f" in {res['elapsed_s']:.1f} s" if res["elapsed_s"] is not None else ""
    print(f"[rv_tool] {res['message']}{took}", file=sys.stderr)
    return res["exit"]


if __name__ == "__main__":
    sys.exit(main())
