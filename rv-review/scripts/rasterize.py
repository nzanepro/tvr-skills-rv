"""Rasterise SVG files to PNG for review in RV (RV does not read SVG).

    rasterize.py a.svg b.svg ... --out DIR [--width W | --height H | --scale S | --dpi D]
                 [--background transparent|checker|#rrggbb] [--same-size] [--backend NAME]

Backends, tried in this order (--list-backends shows what is installed; nothing is
installed by this script):
    resvg          resvg command line (resvg.org), the most faithful renderer
    rsvg-convert   librsvg's command line
    cairosvg       the CairoSVG Python package (import cairosvg)
    inkscape       Inkscape 1.x command line
    playwright     Playwright for Python with its Chromium (a browser renders the SVG)
    chrome         an installed Chrome, Edge or Chromium, headless from its command line
Renderers differ in font fallback, filters and text layout: rasterise every version of a file
with the same backend (the script does, and reports which) before comparing them.

Size: the SVG's width / height (px, pt, pc, mm, cm, in; % and missing sizes fall back to the
viewBox, then 300 x 150), times --scale (or --dpi / 96). --width or --height fixes one side and
keeps the aspect; both fix the canvas. --same-size renders every file at the largest size among
them, so versions line up frame for frame.

Background: transparent keeps the alpha (RV shows it over its own background); checker
flattens onto a grey checkerboard so transparent areas are visible; #rrggbb flattens onto a
colour, e.g. #ffffff for icons meant for a light UI.

Writes DIR/<name>.png for each input and prints one JSON line with the backend and files.
Exit status: 0 ok, 1 a file failed, 2 no backend / bad arguments.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import pathname2url

BACKENDS = ("resvg", "rsvg-convert", "cairosvg", "inkscape", "playwright", "chrome")
DEFAULT_SIZE = (300.0, 150.0)          # what browsers use for an SVG without any size
UNITS = {"px": 1.0, "": 1.0, "pt": 96 / 72, "pc": 16.0, "mm": 96 / 25.4, "cm": 96 / 2.54,
         "in": 96.0}
CHECKER = ((204, 204, 204), (255, 255, 255))
CHECK_PX = 8


class RasterizeError(RuntimeError):
    """A problem the caller can fix; the message says how."""


def _length(text):
    m = re.fullmatch(r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-z%]*)\s*", text or "")
    if not m or m.group(2) == "%" or m.group(2) not in UNITS:
        return None
    try:
        return float(m.group(1)) * UNITS[m.group(2)]
    except ValueError:
        return None


def svg_size(path):
    """(width, height) in CSS pixels from the SVG's width / height or viewBox, else None."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    w, h = _length(root.get("width")), _length(root.get("height"))
    vb = root.get("viewBox") or root.get("viewbox")
    vbw = vbh = None
    if vb:
        parts = re.split(r"[\s,]+", vb.strip())
        if len(parts) == 4:
            try:
                vbw, vbh = float(parts[2]), float(parts[3])
            except ValueError:
                pass
    if w and h:
        return w, h
    if vbw and vbh:
        if w:
            return w, w * vbh / vbw
        if h:
            return h * vbw / vbh, h
        return vbw, vbh
    return None


def target_size(path, width=None, height=None, scale=1.0):
    """Output size in pixels (integers) for one SVG."""
    base = svg_size(path) or DEFAULT_SIZE
    if width and height:
        return int(width), int(height)
    if width:
        return int(width), max(1, round(width * base[1] / base[0]))
    if height:
        return max(1, round(height * base[0] / base[1])), int(height)
    return max(1, round(base[0] * scale)), max(1, round(base[1] * scale))


# --- backend detection ------------------------------------------------------------------

def _inkscape(which=shutil.which, platform=None):
    hit = which("inkscape")
    if hit:
        return hit
    p = platform or sys.platform
    cands = []
    if p.startswith("win"):
        for var in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(var):
                cands.append(Path(os.environ[var]) / "Inkscape" / "bin" / "inkscape.exe")
    elif p == "darwin":
        cands.append(Path("/Applications/Inkscape.app/Contents/MacOS/inkscape"))
    return next((str(c) for c in cands if c.is_file()), None)


def _module(name):
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def detect(which=shutil.which, has_module=_module, find_chrome=None, platform=None):
    """{backend: path or 'python package' or None}, in the order they are tried."""
    if find_chrome is None:
        import web_capture
        find_chrome = web_capture.find_chrome
    return {"resvg": which("resvg"),
            "rsvg-convert": which("rsvg-convert"),
            "cairosvg": "python package" if has_module("cairosvg") else None,
            "inkscape": _inkscape(which, platform),
            "playwright": "python package" if has_module("playwright") else None,
            "chrome": find_chrome()}


def choose(found, wanted=None):
    if wanted:
        if wanted not in BACKENDS:
            raise RasterizeError(f"unknown backend {wanted}; one of {', '.join(BACKENDS)}")
        if not found.get(wanted):
            raise RasterizeError(f"backend {wanted} not found; available: "
                                 f"{[k for k, v in found.items() if v] or 'none'}")
        return wanted
    for b in BACKENDS:
        if found.get(b):
            return b
    raise RasterizeError("no SVG rasteriser found. Install one of: resvg (resvg.org), "
                         "rsvg-convert (librsvg), pip install cairosvg, Inkscape, pip install "
                         "playwright + playwright install chromium, or Chrome / Edge; then run "
                         "again (--list-backends shows what is found).")


def command(backend, exe, svg, out, w, h):
    """Command line for the CLI backends (always a transparent background)."""
    svg, out = str(svg), str(out)
    if backend == "resvg":
        return [exe, "--width", str(w), "--height", str(h), svg, out]
    if backend == "rsvg-convert":
        return [exe, "--width", str(w), "--height", str(h), "--keep-aspect-ratio", "-o", out, svg]
    if backend == "inkscape":
        return [exe, svg, "--export-type=png", f"--export-filename={out}",
                f"--export-width={w}", f"--export-height={h}"]
    raise ValueError(backend)


def _html_page(svg, w, h):
    src = "file:" + pathname2url(str(Path(os.path.abspath(svg))))
    return ("<!doctype html><html><head><style>html,body{margin:0;padding:0;background:transparent;"
            "overflow:hidden}img{display:block}</style></head><body>"
            f'<img src="{src}" width="{w}" height="{h}"></body></html>')


def _render_browser(backend, exe, svg, out, w, h):
    with tempfile.TemporaryDirectory(prefix="rv-review-svg-") as tmp:
        page = Path(tmp) / "svg.html"
        page.write_text(_html_page(svg, w, h), encoding="utf-8")
        url = "file:" + pathname2url(str(page))
        if backend == "playwright":
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b = p.chromium.launch(args=["--allow-file-access-from-files"])
                pg = b.new_page(viewport={"width": w, "height": h})
                pg.goto(url)
                pg.wait_for_load_state("load")
                pg.screenshot(path=str(out), omit_background=True)
                b.close()
            return
        import web_capture
        cmd = web_capture.chrome_args(exe, url, str(Path(os.path.abspath(out))), w, h, 1.0,
                                      str(Path(tmp) / "profile"), transparent=True)
        cmd.insert(-1, "--allow-file-access-from-files")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if not Path(out).is_file():
            raise RasterizeError(f"{backend} did not write {out}: {r.stderr[-400:]}")
        web_capture._fix_size(str(out), w, h)


def flatten(png, background):
    """Composite a transparent PNG onto a checker or a colour (needs Pillow)."""
    if background in (None, "", "transparent"):
        return
    from PIL import Image
    with Image.open(png) as im:
        im = im.convert("RGBA")
        if background == "checker":
            import numpy as np
            yy, xx = np.mgrid[0:im.height, 0:im.width]
            odd = ((yy // CHECK_PX + xx // CHECK_PX) % 2).astype(bool)
            arr = np.empty((im.height, im.width, 4), np.uint8)
            arr[~odd], arr[odd] = CHECKER[0] + (255,), CHECKER[1] + (255,)
            bg = Image.fromarray(arr, "RGBA")
        else:
            m = re.fullmatch(r"#?([0-9a-fA-F]{6})", background)
            if not m:
                raise RasterizeError(f"background {background!r}: use transparent, checker or #rrggbb")
            c = tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
            bg = Image.new("RGBA", im.size, c + (255,))
        bg.alpha_composite(im)
        bg.convert("RGB").save(png)


def rasterize(svg, out, size=None, scale=1.0, background="transparent", backend=None,
              found=None):
    """Render one SVG to out (PNG). Returns {"out", "backend", "size"}."""
    svg, out = Path(svg), Path(out)
    if not svg.is_file():
        raise RasterizeError(f"SVG not found: {svg}")
    found = detect() if found is None else found
    b = choose(found, backend)
    w, h = size if size else target_size(svg, scale=scale)
    out.parent.mkdir(parents=True, exist_ok=True)
    if b == "cairosvg":
        import cairosvg
        cairosvg.svg2png(url=str(svg), write_to=str(out), output_width=w, output_height=h)
    elif b in ("playwright", "chrome"):
        _render_browser(b, found[b], svg, out, w, h)
    else:
        r = subprocess.run(command(b, found[b], svg, out, w, h), capture_output=True, text=True)
        if r.returncode != 0 or not out.is_file():
            raise RasterizeError(f"{b} failed on {svg}: {(r.stderr or r.stdout)[-400:]}")
    flatten(out, background)
    return {"out": str(Path(os.path.abspath(out))), "backend": b, "size": [w, h]}


def build_parser():
    ap = argparse.ArgumentParser(prog="rasterize.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="examples:\n"
                                        "  python rasterize.py icons/v1/*.svg --out review/v1 --scale 4 --background checker\n"
                                        "  python rasterize.py logo_v1.svg logo_v2.svg --same-size --width 1024 --out review/logo")
    ap.add_argument("svgs", nargs="*", metavar="SVG")
    ap.add_argument("--out", metavar="DIR", help="folder for the PNGs")
    ap.add_argument("--width", type=int, help="output width in px (keeps the aspect)")
    ap.add_argument("--height", type=int, help="output height in px (keeps the aspect)")
    ap.add_argument("--scale", type=float, default=1.0, help="multiplier on the SVG's own size")
    ap.add_argument("--dpi", type=float, help="alternative to --scale: DPI / 96")
    ap.add_argument("--same-size", action="store_true", help="render all files at one size")
    ap.add_argument("--background", default="transparent",
                    help="transparent (default), checker, or a colour like #ffffff")
    ap.add_argument("--backend", choices=BACKENDS, help="force a backend")
    ap.add_argument("--list-backends", action="store_true", help="show what is installed and exit")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        found = detect()
        if a.list_backends:
            print(json.dumps({"ok": True, "backends": found,
                              "first": next((b for b in BACKENDS if found.get(b)), None)}))
            return 0
        if not a.svgs or not a.out:
            raise RasterizeError("give SVG files and --out DIR (see --help)")
        scale = a.dpi / 96.0 if a.dpi else a.scale
        sizes = {s: target_size(s, a.width, a.height, scale) for s in a.svgs}
        if a.same_size:
            big = (max(w for w, _ in sizes.values()), max(h for _, h in sizes.values()))
            sizes = {s: big for s in sizes}
        backend = choose(found, a.backend)
        files, failed = [], []
        stems = {}
        for s in a.svgs:
            stem = Path(s).stem
            stems[stem] = stems.get(stem, 0) + 1
            name = stem if stems[stem] == 1 else f"{stem}_{stems[stem]}"
            try:
                files.append(rasterize(s, Path(a.out) / f"{name}.png", sizes[s], scale,
                                       a.background, backend, found))
            except (RasterizeError, OSError, subprocess.SubprocessError) as e:
                failed.append({"svg": s, "error": str(e)})
    except RasterizeError as e:
        print(f"rasterize: {e}", file=sys.stderr)
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    print(json.dumps({"ok": not failed, "backend": backend, "files": files, "failed": failed}))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
