"""Tests for the headless-browser path shared by web_capture.py and rasterize.py:

- find_chrome's order: --chrome (or the config file's "chrome"), chrome-headless-shell on
  PATH, Playwright's headless shell (newest revision first, per OS, --playwright-browsers),
  then Chrome / Edge / Chromium;
- chrome_args for a headless shell;
- run_chrome_command / chrome_screenshot: some Chrome builds write the screenshot and never
  exit, so the runner stops the browser (and its children) once the file is complete.

The "browser" here is a small Python script run with sys.executable that mimics Chrome's
--screenshot / --user-data-dir handling; no real browser is launched. Each runner test
takes well under 3 s.
"""
import functools
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "rv-review" / "scripts"

FAKE_CHROME = r'''
import os, subprocess, sys, time
opts = {}
for a in sys.argv[1:]:
    if a.startswith("--") and "=" in a:
        k, v = a[2:].split("=", 1)
        opts[k] = v
mode = opts.get("fake-mode", "exit")
profile = opts.get("user-data-dir")
if profile:
    os.makedirs(profile, exist_ok=True)
    with open(os.path.join(profile, "SingletonLock"), "w") as f:
        f.write("fake")
if opts.get("fake-pidfile"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(opts["fake-pidfile"], "w") as f:
        f.write("%d %d %s" % (os.getpid(), child.pid, profile or ""))
if mode == "never":
    time.sleep(60)
    sys.exit(0)
if mode == "fail":
    print("fake: could not load the page", flush=True)
    sys.exit(3)
data = open(opts["fake-png"], "rb").read()
with open(opts["screenshot"], "wb") as f:
    f.write(data)
if mode != "silent-hang":
    print("%d bytes written to file %s" % (len(data), opts["screenshot"]), flush=True)
if mode in ("hang", "silent-hang"):
    time.sleep(60)
'''


@pytest.fixture()
def fake(tmp_path):
    """Build a fake-browser command prefix: fake(mode, pidfile=False) -> (prefix, pidfile)."""
    script = tmp_path / "fake_chrome.py"
    script.write_text(FAKE_CHROME, encoding="utf-8")
    png = tmp_path / "fake_src.png"
    Image.new("RGBA", (40, 30), (10, 120, 200, 255)).save(png)

    def make(mode, pidfile=False):
        pf = tmp_path / f"pids_{mode}.txt"
        prefix = [sys.executable, str(script), f"--fake-mode={mode}", f"--fake-png={png}"]
        if pidfile:
            prefix.append(f"--fake-pidfile={pf}")
        return prefix, pf
    return make


def _alive(pid):
    """True while process pid is running (a zombie counts as gone)."""
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x00100000 | 0x1000, False, pid)   # SYNCHRONIZE | QUERY_LIMITED
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(h, 0) == 0x102          # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat = Path(f"/proc/{pid}/stat")
    try:
        return stat.read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def _gone(pid, within=5.0):
    end = time.monotonic() + within
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def _pids(pidfile):
    parent, child, profile = (pidfile.read_text().split(" ", 2) + [""])[:3]
    return int(parent), int(child), profile


def _run(wc, prefix, out, log, **kw):
    cmd = wc.chrome_args(prefix, "file:///page.html", str(out), 40, 30)
    return wc.run_chrome_command(cmd, out, str(log), **kw)


# ---------------------------------------------------------------------------
# find_chrome order
# ---------------------------------------------------------------------------

def _which_only(*hits):
    calls = []

    def which(name):
        calls.append(name)
        return f"fake/{name}" if name in hits else None
    which.calls = calls
    return which


def _exe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _shell_tree(root, platform, revisions):
    """Fake Playwright cache under root with chromium_headless_shell-<rev> folders."""
    sub = {"win32": "chrome-headless-shell-win64", "darwin": "chrome-headless-shell-mac-arm64",
           "linux": "chrome-headless-shell-linux64"}[platform]
    exe = "chrome-headless-shell.exe" if platform == "win32" else "chrome-headless-shell"
    out = {}
    for rev in revisions:
        out[rev] = _exe(root / f"chromium_headless_shell-{rev}" / sub / exe)
    _exe(root / "chromium-1300" / "chrome-linux" / "chrome")      # full browser: not a shell
    return out


def _platform_where(tmp_path, platform):
    """(find_chrome keyword arguments, Playwright cache root) for a platform, under tmp_path."""
    home = tmp_path / "home"
    kw = {"home": home, "local": tmp_path / "local", "program_files": [tmp_path / "pf"]}
    if platform == "win32":
        return kw, tmp_path / "local" / "ms-playwright"
    if platform == "darwin":
        return kw, home / "Library" / "Caches" / "ms-playwright"
    return kw, home / ".cache" / "ms-playwright"


def test_find_chrome_flag_wins_over_headless_shells(tmp_path, wc):
    kw, root = _platform_where(tmp_path, "linux")
    _shell_tree(root, "linux", [1169])
    exe = _exe(tmp_path / "my-chrome")
    which = _which_only("chrome-headless-shell", "google-chrome")
    assert wc.find_chrome(str(exe), platform="linux", which=which, **kw) == str(exe)
    assert which.calls == []


def test_find_chrome_prefers_headless_shell_on_path(tmp_path, wc):
    kw, root = _platform_where(tmp_path, "linux")
    _shell_tree(root, "linux", [1169])
    which = _which_only("chrome-headless-shell", "google-chrome")
    assert wc.find_chrome(platform="linux", which=which, **kw) == "fake/chrome-headless-shell"
    assert which.calls == ["chrome-headless-shell"]


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_find_chrome_playwright_shell_newest_first_before_browsers(tmp_path, wc, platform):
    kw, root = _platform_where(tmp_path, platform)
    shells = _shell_tree(root, platform, [999, 1169, 1100])
    which = _which_only("google-chrome", "msedge")
    assert wc.find_chrome(platform=platform, which=which, **kw) == str(shells[1169])
    # revision order is numeric, not alphabetical (999 sorts after 1169 as text)
    cands = wc.headless_shell_candidates(None, platform, kw["home"], kw["local"])
    assert cands == [shells[1169], shells[1100], shells[999]]


def test_find_chrome_skips_shell_revision_without_executable(tmp_path, wc):
    kw, root = _platform_where(tmp_path, "linux")
    shells = _shell_tree(root, "linux", [1169])
    (root / "chromium_headless_shell-1200" / "chrome-headless-shell-linux64").mkdir(parents=True)
    assert wc.find_chrome(platform="linux", which=_which_only(), **kw) == str(shells[1169])


def test_find_chrome_falls_back_to_browsers_without_a_shell(tmp_path, wc):
    kw, _ = _platform_where(tmp_path, "linux")
    which = _which_only("chromium", "msedge")
    assert wc.find_chrome(platform="linux", which=which, **kw) == "fake/chromium"
    assert which.calls[0] == "chrome-headless-shell"


def test_find_chrome_windows_install_folders(tmp_path, wc):
    kw, _ = _platform_where(tmp_path, "win32")
    edge = _exe(tmp_path / "local" / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    assert wc.find_chrome(platform="win32", which=_which_only(), **kw) == str(edge)
    chrome = _exe(tmp_path / "pf" / "Google" / "Chrome" / "Application" / "chrome.exe")
    assert wc.find_chrome(platform="win32", which=_which_only(), **kw) == str(chrome)


def test_playwright_browsers_folder_is_searched_first(tmp_path, wc):
    kw, root = _platform_where(tmp_path, "darwin")
    default = _shell_tree(root, "darwin", [1300])
    custom = _shell_tree(tmp_path / "pw", "darwin", [1000])
    assert wc.find_chrome(browsers_dir=str(tmp_path / "pw"), platform="darwin",
                          which=_which_only(), **kw) == str(custom[1000])
    assert wc.headless_shell_candidates(str(tmp_path / "pw"), "darwin", kw["home"]) == \
        [custom[1000], default[1300]]


def test_playwright_browsers_zero_is_ignored(tmp_path, wc):
    kw, root = _platform_where(tmp_path, "linux")
    shells = _shell_tree(root, "linux", [1169])
    assert wc.playwright_roots("0", "linux", kw["home"]) == [root]
    assert wc.find_chrome(browsers_dir="0", platform="linux", which=_which_only(),
                          **kw) == str(shells[1169])


def test_playwright_roots_per_platform(wc):
    assert wc.playwright_roots(None, "win32", local="L") == [Path("L") / "ms-playwright"]
    assert wc.playwright_roots(None, "win32", home="H") == [
        Path("H") / "AppData" / "Local" / "ms-playwright"]
    assert wc.playwright_roots(None, "darwin", home="H") == [
        Path("H") / "Library" / "Caches" / "ms-playwright"]
    assert wc.playwright_roots(None, "linux", home="H") == [Path("H") / ".cache" / "ms-playwright"]
    assert wc.playwright_roots("P", "linux", home="H") == [Path("P"), Path("H") / ".cache" / "ms-playwright"]


def test_browser_settings_flag_then_config_file(tmp_path, wc):
    exe = _exe(tmp_path / "chrome-bin")
    assert wc.browser_settings(str(exe), None, config={}) == (str(exe), None)
    got = wc.browser_settings(None, None, home=tmp_path,
                              config={"chrome": "~/chrome-bin", "playwright_browsers": "~/pw"})
    assert got == (str(tmp_path / "chrome-bin"), str(tmp_path / "pw"))


def test_browser_settings_chrome_that_is_not_a_file_is_an_error(tmp_path, wc):
    with pytest.raises(wc.CaptureError, match="--chrome"):
        wc.browser_settings(str(tmp_path / "missing"), None, config={})
    with pytest.raises(wc.CaptureError, match="config.json"):
        wc.browser_settings(None, None, config={"chrome": str(tmp_path / "missing")},
                            home=tmp_path)


def test_browser_settings_broken_config_file(tmp_path, wc):
    cfg = tmp_path / ".config" / "tvr-skills-rv" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[]")
    with pytest.raises(wc.CaptureError, match="config file problem"):
        wc.browser_settings(None, None, home=tmp_path)


# ---------------------------------------------------------------------------
# chrome_args for a headless shell
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exe", ["/pw/chrome-headless-shell", r"C:\pw\chrome-headless-shell.exe",
                                 "headless_shell"])
def test_chrome_args_headless_shell_gets_plain_headless(wc, exe):
    assert wc.is_headless_shell(exe)
    args = wc.chrome_args(exe, "http://h/", "shot.png", 375, 900)
    assert "--headless" in args and "--headless=new" not in args
    assert "--screenshot=shot.png" in args and args[-1] == "http://h/"


def test_chrome_args_full_browser_and_command_prefix(wc):
    assert not wc.is_headless_shell("/usr/bin/google-chrome")
    args = wc.chrome_args(["python", "fake.py"], "http://h/", "shot.png", 10, 10)
    assert args[:3] == ["python", "fake.py", "--headless=new"]


# ---------------------------------------------------------------------------
# run_chrome_command / chrome_screenshot with a fake browser
# ---------------------------------------------------------------------------

def test_alive_helper_tracks_a_real_process():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _alive(p.pid)
    finally:
        p.kill()
        p.wait(timeout=10)
    assert _gone(p.pid)


def test_runner_stops_browser_that_never_exits_and_its_children(tmp_path, wc, fake):
    prefix, pidfile = fake("hang", pidfile=True)
    out = tmp_path / "shot.png"
    t = time.monotonic()
    r = _run(wc, prefix, out, tmp_path / "log.txt", exit_grace=0.2, timeout=20)
    assert time.monotonic() - t < 3
    assert r["ok"] and r["stopped"] and not r["exited"] and not r["timed_out"]
    assert r["error"] is None and "kept running" in r["note"]
    assert "bytes written to file" in r["output"]
    with Image.open(out) as im:
        assert im.size == (40, 30)
    parent, child, _ = _pids(pidfile)
    assert _gone(parent) and _gone(child)


def test_runner_accepts_stable_file_without_the_written_line(tmp_path, wc, fake):
    prefix, pidfile = fake("silent-hang", pidfile=True)
    r = _run(wc, prefix, tmp_path / "shot.png", tmp_path / "log.txt", exit_grace=0.1,
             poll=0.05, timeout=20)
    assert r["ok"] and r["stopped"] and not r["timed_out"]
    parent, child, _ = _pids(pidfile)
    assert _gone(parent) and _gone(child)


def test_runner_browser_that_exits_by_itself(tmp_path, wc, fake):
    prefix, _ = fake("exit")
    stopped = []
    r = _run(wc, prefix, tmp_path / "shot.png", tmp_path / "log.txt", timeout=20,
             killer=stopped.append)
    assert r["ok"] and r["exited"] and not r["stopped"] and r["returncode"] == 0
    assert r["note"] is None and stopped == []


def test_runner_browser_that_fails_reports_its_output(tmp_path, wc, fake):
    prefix, _ = fake("fail")
    r = _run(wc, prefix, tmp_path / "shot.png", tmp_path / "log.txt", timeout=20)
    assert not r["ok"] and r["exited"] and r["returncode"] == 3
    assert "status 3" in r["error"] and "could not load the page" in r["error"]


def test_runner_times_out_without_a_screenshot(tmp_path, wc, fake):
    """Injected clock and sleep: the timeout passes without waiting for it."""
    prefix, pidfile = fake("never", pidfile=True)
    out = tmp_path / "shot.png"
    out.write_bytes(b"stale")                    # an old capture must not count as done
    ticks = iter(range(0, 10000, 5))
    killed = []

    def killer(proc):
        killed.append(proc.pid)
        wc.kill_process_tree(proc)
    t = time.monotonic()
    r = _run(wc, prefix, out, tmp_path / "log.txt", timeout=30, poll=0.05,
             clock=lambda: next(ticks), sleep=lambda s: time.sleep(0.05), killer=killer)
    assert time.monotonic() - t < 3
    assert not r["ok"] and r["timed_out"] and r["stopped"]
    assert "timed out after 30 s" in r["error"]
    assert not out.exists()
    assert len(killed) == 1 and _gone(killed[0])
    if pidfile.is_file():                        # the fake may not have got that far
        assert _gone(_pids(pidfile)[1])


def test_runner_timeout_with_a_complete_file_is_ok(tmp_path, wc, fake):
    prefix, _ = fake("silent-hang")
    r = _run(wc, prefix, tmp_path / "shot.png", tmp_path / "log.txt", timeout=2.0,
             exit_grace=30, poll=0.05)
    assert r["ok"] and r["timed_out"] and r["stopped"]
    assert "still running after 2 s" in r["note"]


def test_runner_missing_executable(tmp_path, wc):
    r = wc.run_chrome_command([str(tmp_path / "no-such-browser")], tmp_path / "s.png",
                              str(tmp_path / "log.txt"), timeout=5)
    assert not r["ok"] and "could not start" in r["error"]


def test_chrome_screenshot_removes_its_profile(tmp_path, wc, fake):
    prefix, pidfile = fake("hang", pidfile=True)
    r = wc.chrome_screenshot(prefix, "file:///page.html", tmp_path / "shot.png", 40, 30,
                             extra_args=["--allow-file-access-from-files"], exit_grace=0.1,
                             timeout=20)
    assert r["ok"] and r["stopped"]
    _, _, profile = _pids(pidfile)
    assert profile.startswith(r["profile"])
    assert Path(r["profile"]).name.startswith("rv-review-chrome-")
    assert not os.path.exists(r["profile"])


def test_remove_tree_retries_then_gives_up_quietly(tmp_path, wc, monkeypatch):
    d = tmp_path / "prof"
    (d / "x").mkdir(parents=True)
    calls = []
    real = wc.shutil.rmtree

    def flaky(path, ignore_errors=False):
        calls.append(ignore_errors)
        if len(calls) < 3:
            raise PermissionError("locked")
        real(path, ignore_errors=ignore_errors)
    monkeypatch.setattr(wc.shutil, "rmtree", flaky)
    assert wc.remove_tree(str(d), sleep=lambda s: None)
    assert calls == [False, False, False] and not d.exists()
    assert wc.remove_tree(str(d), sleep=lambda s: None)          # already gone


def test_run_chrome_cli_notes_and_failures(tmp_path, wc, fake):
    prefix, _ = fake("hang")
    jobs = wc.plan(["http://localhost:3000/"], tmp_path / "caps", breakpoints="m=40x30")
    shoot = functools.partial(wc.chrome_screenshot, exit_grace=0.1, timeout=20)
    done, failed, notes = wc.run_chrome_cli(jobs, {"scale": 1.0}, chrome=prefix, shoot=shoot)
    assert done == [jobs[0]["out"]] and failed == []
    assert any("stopped" in n for n in notes)
    bad = functools.partial(wc.chrome_screenshot, timeout=20)
    done, failed, _ = wc.run_chrome_cli(jobs, {"scale": 1.0}, chrome=fake("fail")[0], shoot=bad)
    assert done == [] and "status 3" in failed[0]["error"]


def test_rasterize_chrome_backend_with_a_browser_that_never_exits(tmp_path, rz, fake):
    svg = tmp_path / "icon.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30"/>',
                   encoding="utf-8")
    prefix, pidfile = fake("hang", pidfile=True)
    t = time.monotonic()
    res = rz.rasterize(svg, tmp_path / "out" / "icon.png", size=(48, 32), backend="chrome",
                       found={"chrome": prefix})
    assert time.monotonic() - t < 3
    assert res["backend"] == "chrome" and res["size"] == [48, 32]
    assert "kept running" in res["note"]
    with Image.open(res["out"]) as im:
        assert im.size == (48, 32)
    parent, child, _ = _pids(pidfile)
    assert _gone(parent) and _gone(child)


# ---------------------------------------------------------------------------
# CLI: which browser was picked, and help
# ---------------------------------------------------------------------------

def _cli(script, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True,
                          text=True, timeout=60)


def test_list_backends_shows_the_chrome_flag_picked(tmp_path):
    exe = _exe(tmp_path / "chrome-headless-shell")
    for script, key in (("web_capture.py", "chrome-cli"), ("rasterize.py", "chrome")):
        r = _cli(script, "--list-backends", "--chrome", str(exe))
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout.strip().splitlines()[-1])["backends"][key] == str(exe)


@pytest.mark.parametrize("script", ["web_capture.py", "rasterize.py"])
def test_list_backends_with_a_missing_chrome_is_an_error(tmp_path, script):
    r = _cli(script, "--list-backends", "--chrome", str(tmp_path / "missing"))
    assert r.returncode == 2
    assert "not a file" in json.loads(r.stdout.strip().splitlines()[-1])["error"]


@pytest.mark.parametrize("script", ["web_capture.py", "rasterize.py"])
def test_help_mentions_chrome_flag_and_headless_shell(script):
    r = _cli(script, "--help")
    assert r.returncode == 0
    assert "--chrome" in r.stdout and "chrome-headless-shell" in r.stdout
    assert "config.json" in r.stdout


def test_browser_settings_expand_a_leading_tilde_in_flags(tmp_path, wc):
    exe = _exe(tmp_path / "bin" / "chrome")
    got = wc.browser_settings("~/bin/chrome", "~/pw", config={}, home=tmp_path)
    assert got == (str(exe), str(tmp_path / "pw"))
