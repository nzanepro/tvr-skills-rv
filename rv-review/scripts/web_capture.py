"""Capture web pages (URLs or local HTML files) at named breakpoints for review in RV.

    web_capture.py PAGE [PAGE ...] --out DIR [--version NAME] [--breakpoints ...]

Writes DIR/<version>/<page>__<breakpoint>.png (DIR/<version>-<browser>/... with several
browsers; DIR/<version>/<breakpoint>/<page>.png with --group-by breakpoint), so two versions
pair up by relative path:

    web_capture.py http://localhost:3000/ http://localhost:3000/pricing --version after --out caps
    web_capture.py https://staging.example.com/ https://staging.example.com/pricing --version before --out caps
    compare_dirs.py caps/before caps/after --out review/web        # changed pages, with diffs
    review_set.py caps --out review/versions                       # every page: before, after

Page names come from --names, else the URL path ("/" -> index, "/pricing" -> pricing) or the
HTML file's name, so the same page gets the same name in every version.

Backends (first found, or --backend): Playwright for Python (import playwright), Playwright
for Node (node can require('playwright') from the current folder), then the Chrome, Edge or
Chromium already installed, driven headless from its command line (viewport only: no
full-page capture, no --wait-for, one browser). Nothing is installed; --list-backends shows
what was found. Playwright's browsers must have been installed by the user
(playwright install).

Full pages: --full-page captures the whole scroll height (Playwright only). Tall pages stay
one tall frame (RV fits it to the window: F fits, 1 shows 1:1, Alt+drag or middle-drag pans)
unless --tile-height N splits them into N-pixel tiles (<page>__<breakpoint>__t01.png ...).

Output: one JSON line with the backend, files written and anything skipped.
Exit status: 0 captured, 1 some captures failed, 2 no backend / bad arguments.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import pathname2url

DEFAULT_BREAKPOINTS = "mobile=375,tablet=768,desktop=1440"
DEFAULT_HEIGHT = 900           # viewport height; full-page captures grow beyond it
BROWSERS = ("chromium", "firefox", "webkit")
BACKENDS = ("playwright-python", "playwright-node", "chrome-cli")
NAV_TIMEOUT_MS = 30000

CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
                "msedge", "microsoft-edge", "microsoft-edge-stable")


class CaptureError(RuntimeError):
    """A problem the caller can fix; the message says how."""


# --- finding a browser ------------------------------------------------------------------

def chrome_candidates(env=None, platform=None):
    """Install locations of Chrome, Edge and Chromium to try, in order, for this OS."""
    env = os.environ if env is None else env
    p = platform or sys.platform
    out = []
    if p.startswith("win"):
        for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = env.get(var)
            if base:
                out += [Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe",
                        Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                        Path(base) / "Chromium" / "Application" / "chrome.exe"]
    elif p == "darwin":
        for app, exe in (("Google Chrome", "Google Chrome"), ("Chromium", "Chromium"),
                         ("Microsoft Edge", "Microsoft Edge")):
            out.append(Path("/Applications") / f"{app}.app" / "Contents" / "MacOS" / exe)
    return out


def find_chrome(env=None, platform=None, which=shutil.which):
    """Path of a Chromium-based browser (Chrome, Edge, Chromium), or None."""
    env = os.environ if env is None else env
    if env.get("CHROME_PATH") and Path(env["CHROME_PATH"]).is_file():
        return str(env["CHROME_PATH"])
    for n in CHROME_NAMES:
        hit = which(n, path=env.get("PATH")) if which is shutil.which else which(n)
        if hit:
            return hit
    for c in chrome_candidates(env, platform):
        if c.is_file():
            return str(c)
    return None


def has_playwright_python():
    try:
        import importlib.util
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


def has_playwright_node(cwd=None, which=shutil.which):
    node = which("node")
    if not node:
        return False
    try:
        r = subprocess.run([node, "-e", "require.resolve('playwright')"], cwd=cwd or os.getcwd(),
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def detect_backends(cwd=None):
    """{backend: detail or None} for every backend, in preference order."""
    return {"playwright-python": "python package" if has_playwright_python() else None,
            "playwright-node": "node package" if has_playwright_node(cwd) else None,
            "chrome-cli": find_chrome()}


def pick_backend(wanted="auto", found=None):
    found = detect_backends() if found is None else found
    if wanted != "auto":
        if not found.get(wanted):
            raise CaptureError(f"backend {wanted} not available here; found: "
                               f"{[k for k, v in found.items() if v] or 'none'}")
        return wanted
    for b in BACKENDS:
        if found.get(b):
            return b
    raise CaptureError("no capture backend found: install Playwright (pip install playwright, "
                       "then playwright install chromium; or npm i -D playwright in the "
                       "project) or a Chrome / Edge / Chromium browser, or set CHROME_PATH")


# --- names, breakpoints, jobs -----------------------------------------------------------

def parse_breakpoints(text):
    """'mobile=375,tablet=768x1024,desktop=1440' -> [(name, width, height or None)]."""
    out = []
    for part in [p.strip() for p in (text or "").split(",") if p.strip()]:
        name, _, size = part.partition("=")
        if not size:
            name, size = f"w{name}", name
        m = re.fullmatch(r"(\d+)(?:x(\d+))?", size.strip())
        if not m or not re.fullmatch(r"[A-Za-z0-9_-]+", name.strip()):
            raise CaptureError(f"breakpoint {part!r}: use name=WIDTH or name=WIDTHxHEIGHT, "
                               f"names from [A-Za-z0-9_-]")
        out.append((name.strip(), int(m.group(1)), int(m.group(2)) if m.group(2) else None))
    if not out:
        raise CaptureError("no breakpoints given")
    return out


def page_url(page):
    """(url, is_local_file) for a URL or a local HTML path."""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", page):
        return page, page.startswith("file:")
    p = Path(page)
    if not p.is_file():
        raise CaptureError(f"not a URL and not a file: {page}")
    return "file:" + pathname2url(str(Path(os.path.abspath(p)))), True


def page_name(page):
    """Stable name for a page: URL path slug or file stem."""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", page) and not page.startswith("file:"):
        u = urlparse(page)
        path = u.path.strip("/")
        if not path or path == "index.html" or path.endswith("/index.html"):
            path = path[:-len("index.html")].strip("/") if path else ""
            path = path or "index"
        name = re.sub(r"\.html?$", "", path)
        if u.query:
            name += "_" + u.query
        if u.fragment:
            name += "_" + u.fragment
    else:
        stem = Path(urlparse(page).path if page.startswith("file:") else page).stem
        name = stem or "page"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "index"


def plan(pages, out, version="capture", breakpoints=DEFAULT_BREAKPOINTS, browsers=("chromium",),
         names=None, height=DEFAULT_HEIGHT, group_by="version"):
    """Capture jobs: one per page x breakpoint x browser, with the output path.

    group_by 'version': <out>/<version>/<page>__<breakpoint>.png (versions pair up);
    'breakpoint': <out>/<version>/<breakpoint>/<page>.png (breakpoints are the variants)."""
    bps = parse_breakpoints(breakpoints) if isinstance(breakpoints, str) else breakpoints
    names = list(names or [])
    if names and len(names) != len(pages):
        raise CaptureError(f"--names gives {len(names)} names for {len(pages)} pages")
    for n in names:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", n):
            raise CaptureError(f"page name {n!r}: use [A-Za-z0-9_-]")
    jobs, seen = [], set()
    for i, page in enumerate(pages):
        url, local = page_url(page)
        name = names[i] if names else page_name(page)
        if name in seen:
            raise CaptureError(f"two pages are named {name!r}; pass --names to tell them apart")
        seen.add(name)
        for browser in browsers:
            folder = version if len(browsers) == 1 else f"{version}-{browser}"
            for bp, w, h in bps:
                rel = Path(folder) / bp / f"{name}.png" if group_by == "breakpoint"                     else Path(folder) / f"{name}__{bp}.png"
                jobs.append({"page": page, "url": url, "local": local, "name": name,
                             "breakpoint": bp, "width": w, "height": h or height,
                             "browser": browser, "out": str(Path(os.path.abspath(out)) / rel)})
    return jobs


# --- backends ---------------------------------------------------------------------------

def chrome_args(chrome, url, out, width, height, scale=1.0, user_data_dir=None,
                color_scheme=None, transparent=False):
    """Headless Chrome / Edge command line for one viewport screenshot."""
    args = [str(chrome), "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            f"--window-size={width},{height}", f"--force-device-scale-factor={scale:g}",
            f"--screenshot={out}"]
    if user_data_dir:
        args.append(f"--user-data-dir={user_data_dir}")
    if transparent:
        args.append("--default-background-color=00000000")
    if color_scheme == "dark":
        args.append("--force-dark-mode")     # prefers-color-scheme: dark
    args.append(url)
    return args


def run_chrome_cli(jobs, opts, chrome=None):
    chrome = chrome or find_chrome()
    done, failed = [], []
    with tempfile.TemporaryDirectory(prefix="rv-review-chrome-") as profile:
        for j in jobs:
            Path(j["out"]).parent.mkdir(parents=True, exist_ok=True)
            cmd = chrome_args(chrome, j["url"], str(Path(os.path.abspath(j["out"]))), j["width"],
                              j["height"], opts.get("scale", 1.0), profile,
                              opts.get("color_scheme"), opts.get("transparent", False))
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                ok = r.returncode == 0 and Path(j["out"]).is_file()
            except subprocess.TimeoutExpired:
                ok, r = False, None
            if ok:
                _fix_size(j["out"], round(j["width"] * opts.get("scale", 1.0)),
                          round(j["height"] * opts.get("scale", 1.0)))
                done.append(j["out"])
            else:
                failed.append({"out": j["out"], "error": (r.stderr[-400:] if r else "timed out")})
    return done, failed


def _fix_size(path, w, h):
    """Crop or pad a CLI screenshot to exactly w x h (some builds add a few pixels)."""
    try:
        from PIL import Image
    except ImportError:
        return
    with Image.open(path) as im:
        if im.size == (w, h):
            return
        canvas = Image.new(im.mode, (w, h))
        canvas.paste(im.crop((0, 0, min(w, im.width), min(h, im.height))), (0, 0))
    canvas.save(path)


PLAYWRIGHT_JS = r"""
const pw = require('playwright');
const cfg = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
(async () => {
  const results = {done: [], failed: []};
  const byBrowser = {};
  for (const j of cfg.jobs) (byBrowser[j.browser] = byBrowser[j.browser] || []).push(j);
  for (const [name, jobs] of Object.entries(byBrowser)) {
    let browser;
    try { browser = await pw[name].launch(); }
    catch (e) { for (const j of jobs) results.failed.push({out: j.out, error: String(e).slice(0, 400)}); continue; }
    for (const j of jobs) {
      try {
        const ctx = await browser.newContext({viewport: {width: j.width, height: j.height},
          deviceScaleFactor: cfg.scale, colorScheme: cfg.color_scheme || undefined,
          locale: cfg.locale || undefined, reducedMotion: cfg.reduced_motion ? 'reduce' : undefined});
        const page = await ctx.newPage();
        await page.goto(j.url, {waitUntil: cfg.wait_until, timeout: cfg.timeout});
        if (cfg.wait_for) await page.waitForSelector(cfg.wait_for, {timeout: cfg.timeout});
        if (cfg.wait_ms) await page.waitForTimeout(cfg.wait_ms);
        require('fs').mkdirSync(require('path').dirname(j.out), {recursive: true});
        await page.screenshot({path: j.out, fullPage: cfg.full_page, animations: 'disabled',
                               omitBackground: cfg.transparent});
        await ctx.close();
        results.done.push(j.out);
      } catch (e) { results.failed.push({out: j.out, error: String(e).slice(0, 400)}); }
    }
    await browser.close();
  }
  console.log(JSON.stringify(results));
})();
"""


def run_playwright_node(jobs, opts):
    node = shutil.which("node")
    with tempfile.TemporaryDirectory(prefix="rv-review-pw-") as tmp:
        script, cfg = Path(tmp) / "capture.js", Path(tmp) / "jobs.json"
        script.write_text(PLAYWRIGHT_JS, encoding="utf-8")
        cfg.write_text(json.dumps(dict(opts, jobs=jobs)), encoding="utf-8")
        # run from the current folder so require('playwright') finds the project's copy
        env = dict(os.environ, NODE_PATH=os.pathsep.join(
            filter(None, [os.environ.get("NODE_PATH"), str(Path.cwd() / "node_modules")])))
        r = subprocess.run([node, str(script), str(cfg)], capture_output=True, text=True,
                           cwd=os.getcwd(), env=env)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise CaptureError(f"Playwright (node) failed: {(r.stderr or r.stdout)[-600:]}") from None
    return res["done"], res["failed"]


def run_playwright_python(jobs, opts):
    from playwright.sync_api import sync_playwright
    done, failed = [], []
    with sync_playwright() as p:
        by_browser = {}
        for j in jobs:
            by_browser.setdefault(j["browser"], []).append(j)
        for name, group in by_browser.items():
            try:
                browser = getattr(p, name).launch()
            except Exception as e:                       # browser not installed and similar
                failed += [{"out": j["out"], "error": str(e)[:400]} for j in group]
                continue
            for j in group:
                try:
                    ctx = browser.new_context(
                        viewport={"width": j["width"], "height": j["height"]},
                        device_scale_factor=opts["scale"], color_scheme=opts.get("color_scheme"),
                        locale=opts.get("locale"),
                        reduced_motion="reduce" if opts.get("reduced_motion") else None)
                    page = ctx.new_page()
                    page.goto(j["url"], wait_until=opts["wait_until"], timeout=opts["timeout"])
                    if opts.get("wait_for"):
                        page.wait_for_selector(opts["wait_for"], timeout=opts["timeout"])
                    if opts.get("wait_ms"):
                        page.wait_for_timeout(opts["wait_ms"])
                    Path(j["out"]).parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=j["out"], full_page=opts["full_page"],
                                    animations="disabled", omit_background=opts["transparent"])
                    ctx.close()
                    done.append(j["out"])
                except Exception as e:
                    failed.append({"out": j["out"], "error": str(e)[:400]})
            browser.close()
    return done, failed


def tile(path, tile_height):
    """Split a tall capture into tiles of tile_height px: <stem>__t01.png ...; returns paths."""
    from PIL import Image
    p = Path(path)
    with Image.open(p) as im:
        if im.height <= tile_height:
            return [str(p)]
        out = []
        for k, top in enumerate(range(0, im.height, tile_height), 1):
            t = p.with_name(f"{p.stem}__t{k:02d}{p.suffix}")
            im.crop((0, top, im.width, min(im.height, top + tile_height))).save(t)
            out.append(str(t))
    p.unlink()
    return out


def capture(jobs, backend, opts):
    if backend == "chrome-cli":
        skipped = []
        if opts.get("full_page"):
            skipped.append("--full-page needs Playwright; captured the viewport (raise --height)")
        if opts.get("wait_for"):
            skipped.append("--wait-for needs Playwright; ignored")
        if {j["browser"] for j in jobs} - {"chromium"}:
            raise CaptureError("the chrome-cli backend captures Chromium only; install "
                               "Playwright for firefox / webkit")
        done, failed = run_chrome_cli(jobs, opts)
        return done, failed, skipped
    run = run_playwright_python if backend == "playwright-python" else run_playwright_node
    done, failed = run(jobs, opts)
    return done, failed, []


def build_parser():
    ap = argparse.ArgumentParser(prog="web_capture.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pages", nargs="*", metavar="PAGE", help="URLs or local HTML files")
    ap.add_argument("--out", metavar="DIR", help="capture root (required unless --list-backends)")
    ap.add_argument("--version", default="capture", metavar="NAME",
                    help="folder for this version, e.g. before / after / main / pr-123")
    ap.add_argument("--names", metavar="A,B,...", help="page names, one per PAGE, in order")
    ap.add_argument("--breakpoints", default=DEFAULT_BREAKPOINTS,
                    help=f"name=WIDTH[xHEIGHT],... (default {DEFAULT_BREAKPOINTS})")
    ap.add_argument("--height", type=int, default=DEFAULT_HEIGHT,
                    help=f"viewport height when a breakpoint gives none (default {DEFAULT_HEIGHT})")
    ap.add_argument("--browsers", default="chromium",
                    help="comma list of chromium, firefox, webkit (Playwright)")
    ap.add_argument("--group-by", choices=("version", "breakpoint"), default="version",
                    help="version: <version>/<page>__<bp>.png (compare versions); breakpoint: "
                         "<version>/<bp>/<page>.png (review_set.py flips breakpoints)")
    ap.add_argument("--full-page", action="store_true", help="capture the whole scroll height")
    ap.add_argument("--tile-height", type=int, metavar="PX",
                    help="split captures taller than PX into tiles")
    ap.add_argument("--wait-for", metavar="SELECTOR", help="wait for this CSS selector first")
    ap.add_argument("--wait-ms", type=int, default=0, help="extra wait after loading, in ms")
    ap.add_argument("--wait-until", default="networkidle",
                    choices=("load", "domcontentloaded", "networkidle", "commit"),
                    help="navigation event to wait for (Playwright; default networkidle)")
    ap.add_argument("--scale", type=float, default=1.0, help="device scale factor (2 = retina)")
    ap.add_argument("--color-scheme", choices=("light", "dark", "no-preference"),
                    help="prefers-color-scheme for the page")
    ap.add_argument("--locale", help="browser locale, e.g. ar-EG or de-DE (Playwright)")
    ap.add_argument("--reduced-motion", action="store_true", help="prefers-reduced-motion: reduce")
    ap.add_argument("--transparent", action="store_true", help="keep a transparent page background")
    ap.add_argument("--backend", choices=("auto",) + BACKENDS, default="auto")
    ap.add_argument("--list-backends", action="store_true", help="show what was found and exit")
    ap.add_argument("--dry-run", action="store_true", help="print the capture plan only")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        if a.list_backends:
            found = detect_backends()
            print(json.dumps({"ok": True, "backends": found,
                              "first": next((b for b in BACKENDS if found.get(b)), None)}))
            return 0
        if not a.pages or not a.out:
            raise CaptureError("give one or more PAGEs and --out DIR (see --help)")
        browsers = tuple(b.strip() for b in a.browsers.split(",") if b.strip())
        bad = [b for b in browsers if b not in BROWSERS]
        if bad:
            raise CaptureError(f"unknown browser(s) {bad}; use {', '.join(BROWSERS)}")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", a.version):
            raise CaptureError("--version: use letters, digits, '.', '_' or '-'")
        names = [n.strip() for n in a.names.split(",")] if a.names else None
        jobs = plan(a.pages, a.out, a.version, a.breakpoints, browsers, names, a.height, a.group_by)
        opts = {"scale": a.scale, "color_scheme": a.color_scheme, "locale": a.locale,
                "reduced_motion": a.reduced_motion, "full_page": a.full_page,
                "wait_for": a.wait_for, "wait_ms": a.wait_ms, "wait_until": a.wait_until,
                "timeout": NAV_TIMEOUT_MS, "transparent": a.transparent}
        if a.dry_run:
            print(json.dumps({"ok": True, "dry_run": True, "jobs": jobs}))
            return 0
        backend = pick_backend(a.backend)
        done, failed, notes = capture(jobs, backend, opts)
        if a.tile_height:
            done = [t for d in done for t in tile(d, a.tile_height)]
    except CaptureError as e:
        print(f"web_capture: {e}", file=sys.stderr)
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    print(json.dumps({"ok": not failed, "backend": backend, "root": str(Path(os.path.abspath(a.out))),
                      "version": a.version, "files": done, "failed": failed, "notes": notes}))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
