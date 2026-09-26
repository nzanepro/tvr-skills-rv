"""Capture the current screen of a mobile simulator, device or desktop app in several variants
(appearance, text size, display size, locale) into a review-set folder layout.

    app_capture.py ios      --screen NAME --out DIR [--device booted|UDID] [--appearance light,dark]
                            [--content-size large,extra-extra-large,...] [--bundle ID --locales ...]
    app_capture.py android  --screen NAME --out DIR [--serial S] [--night no,yes]
                            [--font-scale 1.0,1.3,2.0] [--display 1080x2400@420,...] [--demo-mode]
    app_capture.py electron --screen NAME --out DIR --main main.js [--size 1280x800,...]
                            [--color-scheme light,dark]

Every combination is one variant folder: DIR/<variant>/<screen>.png, the variant named from
the settings (for example ios-dark-xxl, android-night-fs1.3). Navigate the app to the screen
first (by hand, a UI test, or a deep link), run this once per screen, then:

    review_set.py DIR --out review/<name>        # every screen: all variants back to back

The device settings changed for the capture (appearance, text size, night mode, font scale,
display size) are put back afterwards. Nothing is installed: iOS needs macOS with Xcode's
xcrun simctl and a booted simulator; Android needs adb (PATH, ANDROID_HOME or
ANDROID_SDK_ROOT) and one device or emulator (or --serial); Electron needs Node with the
project's own Playwright (Playwright's Electron support is experimental). --dry-run prints the
commands without running anything, on any OS.

Output: one JSON line with the files written and the commands run.
Exit status: 0 captured, 1 a capture failed, 2 tool or device missing / bad arguments.
"""
import argparse
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SETTLE_S = 1.5        # UI re-layout after an appearance / text size change before the screenshot
IOS_CONTENT_SIZES = ("extra-small", "small", "medium", "large", "extra-large", "extra-extra-large",
                     "extra-extra-extra-large", "accessibility-medium", "accessibility-large",
                     "accessibility-extra-large", "accessibility-extra-extra-large",
                     "accessibility-extra-extra-extra-large")
IOS_SHORT = {"extra-small": "xs", "small": "s", "medium": "m", "large": "l", "extra-large": "xl",
             "extra-extra-large": "xxl", "extra-extra-extra-large": "xxxl",
             "accessibility-medium": "ax1", "accessibility-large": "ax2",
             "accessibility-extra-large": "ax3", "accessibility-extra-extra-large": "ax4",
             "accessibility-extra-extra-extra-large": "ax5"}


class CaptureError(RuntimeError):
    """A problem the caller can fix; the message says how."""


def _list(text, allowed=None, what="value"):
    vals = [v.strip() for v in (text or "").split(",") if v.strip()]
    if allowed:
        bad = [v for v in vals if v not in allowed]
        if bad:
            raise CaptureError(f"unknown {what} {bad}; use {', '.join(allowed)}")
    return vals


def _safe(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(s)).strip("-") or "x"


# --- iOS Simulator ----------------------------------------------------------------------

def ios_variants(appearances, sizes, locales):
    """[(variant name, {appearance, content_size, locale})] for every combination."""
    out = []
    for ap, cs, loc in itertools.product(appearances or [None], sizes or [None], locales or [None]):
        parts = ["ios"] + [x for x in (ap, IOS_SHORT.get(cs, cs) if cs else None, loc) if x]
        out.append(("-".join(_safe(p) for p in parts), {"appearance": ap, "content_size": cs,
                                                        "locale": loc}))
    return out


def ios_commands(device, settings, out_png, bundle=None):
    """simctl commands for one variant: settings, optional app relaunch, screenshot."""
    x = ["xcrun", "simctl"]
    cmds = []
    if settings.get("appearance"):
        cmds.append(x + ["ui", device, "appearance", settings["appearance"]])
    if settings.get("content_size"):
        cmds.append(x + ["ui", device, "content_size", settings["content_size"]])
    if settings.get("locale") and bundle:
        lang = settings["locale"].split("-")[0]
        # standard launch arguments that override the app's language and region
        cmds.append(x + ["terminate", device, bundle])
        cmds.append(x + ["launch", device, bundle, "-AppleLanguages", f"({lang})",
                         "-AppleLocale", settings["locale"].replace("-", "_")])
    cmds.append(x + ["io", device, "screenshot", "--type=png", str(out_png)])
    return cmds


def ios_status_bar(device, clean=True):
    x = ["xcrun", "simctl", "status_bar", device]
    return x + (["override", "--time", "9:41", "--batteryState", "charged", "--batteryLevel", "100",
                 "--cellularBars", "4", "--wifiBars", "3"] if clean else ["clear"])


# --- Android ----------------------------------------------------------------------------

def find_adb(env=None, which=shutil.which):
    env = os.environ if env is None else env
    hit = which("adb")
    if hit:
        return hit
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if env.get(var):
            p = Path(env[var]) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
            if p.is_file():
                return str(p)
    return None


def parse_display(text):
    """'1080x2400@420' -> (1080, 2400, 420); '1080x2400' -> (1080, 2400, None)."""
    m = re.fullmatch(r"(\d+)x(\d+)(?:@(\d+))?", text.strip())
    if not m:
        raise CaptureError(f"display {text!r}: use WIDTHxHEIGHT or WIDTHxHEIGHT@DENSITY")
    return int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None


def android_variants(nights, scales, displays):
    out = []
    for n, fs, d in itertools.product(nights or [None], scales or [None], displays or [None]):
        parts = ["android"]
        if n:
            parts.append("night" if n == "yes" else "day" if n == "no" else f"night-{n}")
        if fs:
            parts.append(f"fs{fs}")
        if d:
            parts.append(d.replace("@", "-"))
        out.append(("-".join(_safe(p) for p in parts), {"night": n, "font_scale": fs, "display": d}))
    return out


def android_commands(adb, serial, settings):
    """adb commands that apply one variant's settings (the screenshot is taken separately)."""
    a = [adb] + (["-s", serial] if serial else [])
    cmds = []
    if settings.get("night"):
        cmds.append(a + ["shell", "cmd", "uimode", "night", settings["night"]])
    if settings.get("font_scale"):
        cmds.append(a + ["shell", "settings", "put", "system", "font_scale", str(settings["font_scale"])])
    if settings.get("display"):
        w, h, dpi = parse_display(settings["display"])
        cmds.append(a + ["shell", "wm", "size", f"{w}x{h}"])
        if dpi:
            cmds.append(a + ["shell", "wm", "density", str(dpi)])
    return cmds


def android_screenshot_cmd(adb, serial):
    return [adb] + (["-s", serial] if serial else []) + ["exec-out", "screencap", "-p"]


def android_demo_mode(adb, serial, on=True):
    """SystemUI demo mode (AOSP packages/SystemUI/docs/demo_mode.md): a fixed clock and full
    bars so status bars do not differ between captures."""
    a = [adb] + (["-s", serial] if serial else [])
    b = a + ["shell", "am", "broadcast", "-a", "com.android.systemui.demo", "-e", "command"]
    if not on:
        return [b + ["exit"]]
    return [a + ["shell", "settings", "put", "global", "sysui_demo_allowed", "1"],
            b + ["enter"], b + ["clock", "-e", "hhmm", "0941"],
            b + ["battery", "-e", "level", "100", "-e", "plugged", "false"],
            b + ["network", "-e", "wifi", "show", "-e", "level", "4"],
            b + ["notifications", "-e", "visible", "false"]]


# --- running ----------------------------------------------------------------------------

def _run(cmd, dry, log, capture_png=None):
    log.append(cmd if not capture_png else cmd + [">", str(capture_png)])
    if dry:
        return ""
    if capture_png:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode != 0 or not r.stdout.startswith(b"\x89PNG"):
            raise CaptureError(f"screenshot failed: {r.stderr.decode(errors='replace')[-300:]}")
        Path(capture_png).parent.mkdir(parents=True, exist_ok=True)
        Path(capture_png).write_bytes(r.stdout)
        return ""
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise CaptureError(f"{' '.join(cmd[:4])} ... failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def capture_ios(a, dry):
    if not dry and (sys.platform != "darwin" or not shutil.which("xcrun")):
        raise CaptureError("iOS Simulator capture needs macOS with Xcode (xcrun simctl); use "
                           "--dry-run to see the commands")
    variants = ios_variants(_list(a.appearance, ("light", "dark"), "appearance"),
                            _list(a.content_size, IOS_CONTENT_SIZES, "content size"),
                            _list(a.locales))
    if any(v[1]["locale"] for v in variants) and not a.bundle:
        raise CaptureError("--locales relaunches the app with a language: pass --bundle ID")
    log, files = [], []
    restore = []
    if not dry:
        # remember what to put back
        cur_ap = _run(["xcrun", "simctl", "ui", a.device, "appearance"], dry, []).strip()
        cur_cs = _run(["xcrun", "simctl", "ui", a.device, "content_size"], dry, []).strip()
        if cur_ap in ("light", "dark"):
            restore.append(["xcrun", "simctl", "ui", a.device, "appearance", cur_ap])
        if cur_cs in IOS_CONTENT_SIZES:
            restore.append(["xcrun", "simctl", "ui", a.device, "content_size", cur_cs])
    try:
        if a.clean_status_bar:
            _run(ios_status_bar(a.device, True), dry, log)
        for name, settings in variants:
            png = Path(a.out) / name / f"{_safe(a.screen)}.png"
            if not dry:
                png.parent.mkdir(parents=True, exist_ok=True)
            cmds = ios_commands(a.device, settings, png, a.bundle)
            for c in cmds[:-1]:
                _run(c, dry, log)
            if not dry:
                time.sleep(a.settle)
            _run(cmds[-1], dry, log)
            files.append(str(png))
    finally:
        if a.clean_status_bar:
            _run(ios_status_bar(a.device, False), dry, log)
        for c in restore:
            _run(c, dry, log)
    return files, log


def capture_android(a, dry):
    adb = find_adb() or ("adb" if dry else None)
    if not adb:
        raise CaptureError("adb not found on PATH, ANDROID_HOME or ANDROID_SDK_ROOT; install the "
                           "Android platform-tools or use --dry-run to see the commands")
    base = [adb] + (["-s", a.serial] if a.serial else [])
    if not dry:
        devs = [ln.split()[0] for ln in _run([adb, "devices"], dry, []).splitlines()[1:]
                if ln.strip().endswith("device")]
        if not devs:
            raise CaptureError("no Android device or emulator connected (adb devices is empty)")
        if len(devs) > 1 and not a.serial:
            raise CaptureError(f"several devices connected {devs}; pass --serial")
    for fs in _list(a.font_scale):
        try:
            float(fs)
        except ValueError:
            raise CaptureError(f"--font-scale {fs!r} is not a number") from None
    variants = android_variants(_list(a.night, ("yes", "no", "auto"), "night mode"),
                                _list(a.font_scale), _list(a.display))
    for _, s in variants:
        if s["display"]:
            parse_display(s["display"])
    log, files, restore = [], [], []
    if not dry:
        night = _run(base + ["shell", "cmd", "uimode", "night"], dry, [])
        m = re.search(r"Night mode:\s*(\w+)", night)
        if m and m.group(1) in ("yes", "no", "auto"):
            restore.append(base + ["shell", "cmd", "uimode", "night", m.group(1)])
        fs = _run(base + ["shell", "settings", "get", "system", "font_scale"], dry, []).strip()
        restore.append(base + ["shell", "settings", "put", "system", "font_scale",
                               fs if re.fullmatch(r"[0-9.]+", fs) else "1.0"])
    if any(s["display"] for _, s in variants):
        restore += [base + ["shell", "wm", "size", "reset"], base + ["shell", "wm", "density", "reset"]]
    try:
        if a.demo_mode:
            for c in android_demo_mode(adb, a.serial, True):
                _run(c, dry, log)
        for name, settings in variants:
            png = Path(a.out) / name / f"{_safe(a.screen)}.png"
            for c in android_commands(adb, a.serial, settings):
                _run(c, dry, log)
            if not dry:
                time.sleep(a.settle)
            _run(android_screenshot_cmd(adb, a.serial), dry, log, capture_png=png)
            files.append(str(png))
    finally:
        if a.demo_mode:
            for c in android_demo_mode(adb, a.serial, False):
                _run(c, dry, log)
        for c in restore:
            _run(c, dry, log)
    return files, log


ELECTRON_JS = r"""
const { _electron: electron } = require('playwright');
const cfg = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
(async () => {
  const app = await electron.launch({ args: [cfg.main], cwd: cfg.cwd });
  const win = await app.firstWindow();
  await win.waitForLoadState('domcontentloaded');
  const done = [];
  for (const v of cfg.variants) {
    if (v.width) await win.setViewportSize({ width: v.width, height: v.height });
    if (v.color_scheme) await win.emulateMedia({ colorScheme: v.color_scheme });
    if (cfg.wait_for) await win.waitForSelector(cfg.wait_for);
    await win.waitForTimeout(cfg.settle_ms);
    require('fs').mkdirSync(require('path').dirname(v.out), { recursive: true });
    await win.screenshot({ path: v.out, animations: 'disabled' });
    done.push(v.out);
  }
  await app.close();
  console.log(JSON.stringify({ done }));
})().catch(e => { console.error(String(e)); process.exit(1); });
"""


def electron_variants(sizes, schemes, out, screen):
    res = []
    for size, cs in itertools.product(sizes or [None], schemes or [None]):
        w = h = None
        if size:
            m = re.fullmatch(r"(\d+)x(\d+)", size)
            if not m:
                raise CaptureError(f"--size {size!r}: use WIDTHxHEIGHT")
            w, h = int(m.group(1)), int(m.group(2))
        name = "-".join(["electron"] + [x for x in (size, cs) if x])
        res.append({"name": name, "width": w, "height": h, "color_scheme": cs,
                    "out": str(Path(out) / name / f"{_safe(screen)}.png")})
    return res


def capture_electron(a, dry):
    node = shutil.which("node")
    variants = electron_variants(_list(a.size), _list(a.color_scheme, ("light", "dark"), "color scheme"),
                                 a.out, a.screen)
    cfg = {"main": a.main, "cwd": os.getcwd(), "variants": variants, "wait_for": a.wait_for,
           "settle_ms": int(a.settle * 1000)}
    log = [["node", "<electron capture script>", json.dumps(cfg)]]
    if dry:
        return [v["out"] for v in variants], log
    if not node:
        raise CaptureError("node not found; Electron capture uses the project's Playwright")
    with tempfile.TemporaryDirectory(prefix="rv-review-electron-") as tmp:
        script, conf = Path(tmp) / "cap.js", Path(tmp) / "cfg.json"
        script.write_text(ELECTRON_JS, encoding="utf-8")
        conf.write_text(json.dumps(cfg), encoding="utf-8")
        env = dict(os.environ, NODE_PATH=os.pathsep.join(
            filter(None, [os.environ.get("NODE_PATH"), str(Path.cwd() / "node_modules")])))
        r = subprocess.run([node, str(script), str(conf)], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise CaptureError(f"Electron capture failed (is playwright installed in this project?): "
                           f"{r.stderr[-400:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])["done"], log


def build_parser():
    ap = argparse.ArgumentParser(prog="app_capture.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="platform", required=True, metavar="{ios,android,electron}")

    def common(p):
        p.add_argument("--screen", required=True, help="name of the screen being captured")
        p.add_argument("--out", required=True, metavar="DIR", help="capture root")
        p.add_argument("--settle", type=float, default=SETTLE_S,
                       help=f"seconds to wait after a settings change (default {SETTLE_S})")
        p.add_argument("--dry-run", action="store_true", help="print the commands only")

    i = sub.add_parser("ios", help="iOS Simulator (macOS + Xcode)",
                       description="Capture the booted iOS Simulator in several appearances, "
                                   "text sizes and locales.")
    common(i)
    i.add_argument("--device", default="booted", help="simulator UDID or 'booted' (default)")
    i.add_argument("--appearance", help="comma list: light,dark")
    i.add_argument("--content-size", help="comma list of Dynamic Type sizes: "
                   + ", ".join(IOS_CONTENT_SIZES))
    i.add_argument("--locales", help="comma list like en-US,de-DE,ar-SA (relaunches --bundle)")
    i.add_argument("--bundle", help="app bundle id, needed for --locales")
    i.add_argument("--clean-status-bar", action="store_true",
                   help="override the status bar (9:41, full bars) during the capture")
    d = sub.add_parser("android", help="Android emulator or device (adb)",
                       description="Capture an Android device in several night modes, font "
                                   "scales and display sizes.")
    common(d)
    d.add_argument("--serial", help="device serial when several are connected")
    d.add_argument("--night", help="comma list of no,yes,auto")
    d.add_argument("--font-scale", help="comma list like 1.0,1.3,2.0")
    d.add_argument("--display", help="comma list of WIDTHxHEIGHT[@DENSITY] to emulate other devices")
    d.add_argument("--demo-mode", action="store_true", help="SystemUI demo mode: fixed status bar")
    e = sub.add_parser("electron", help="Electron app through the project's Playwright (Node)",
                       description="Launch an Electron app with Playwright and capture its "
                                   "first window at several sizes and colour schemes.")
    common(e)
    e.add_argument("--main", required=True, help="the app's main script (e.g. main.js or .)")
    e.add_argument("--size", help="comma list of WIDTHxHEIGHT window sizes")
    e.add_argument("--color-scheme", help="comma list: light,dark")
    e.add_argument("--wait-for", metavar="SELECTOR", help="wait for this CSS selector")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        fn = {"ios": capture_ios, "android": capture_android, "electron": capture_electron}[a.platform]
        files, log = fn(a, a.dry_run)
    except CaptureError as e:
        print(f"app_capture: {e}", file=sys.stderr)
        print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    except (OSError, subprocess.SubprocessError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    print(json.dumps({"ok": True, "dry_run": a.dry_run, "platform": a.platform,
                      "root": str(Path(os.path.abspath(a.out))), "files": files, "commands": log}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
