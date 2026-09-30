"""Tests for the review-set and capture helpers in rv-review/scripts:

    review_set.py   variants_from, collect / build, frames.json loadable by review_manifest, CLI
    rasterize.py    svg_size, target_size, detect, choose, command, flatten, CLI --list-backends
    web_capture.py  parse_breakpoints, page_name, plan, chrome_args, find_chrome, pick_backend,
                    tile, _fix_size, CLI --dry-run
    app_capture.py  iOS / Android / Electron variant naming and command building, parse_display,
                    android_demo_mode, find_adb, CLI --dry-run and "tool missing" errors

Safety: nothing here launches a browser (Chrome, Edge, Playwright), RV / rvpush, node, adb or
xcrun, and nothing opens a window. Only pure functions are called; backend and tool discovery
is exercised with injected fake `which` / `has_module` / `find_chrome` callables and synthetic
folders; CLI runs use --dry-run, --list-backends (lookups only) or error paths that exit
before any tool is started, and every subprocess.run has a timeout.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "rv-review" / "scripts"
REVIEW_SET_SCRIPT = SCRIPTS / "review_set.py"
RASTERIZE_SCRIPT = SCRIPTS / "rasterize.py"
WEB_CAPTURE_SCRIPT = SCRIPTS / "web_capture.py"
APP_CAPTURE_SCRIPT = SCRIPTS / "app_capture.py"


def _run(script, *args, cwd=None):
    """Run a script with this Python; always with a timeout so a mistake cannot hang."""
    return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True,
                          text=True, timeout=30, cwd=cwd)


def _last_json(stdout):
    return json.loads(stdout.strip().splitlines()[-1])


def _png(path, size, color=(200, 50, 50, 255)):
    """Write a solid RGBA PNG of size (w, h)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)
    return path


def _files_under(folder):
    return sorted(p for p in Path(folder).rglob("*") if p.is_file())


# ===========================================================================================
# review_set.py
# ===========================================================================================

# ---------------------------------------------------------------------------
# variants_from
# ---------------------------------------------------------------------------

def test_variants_from_explicit_folders_keep_given_order(tmp_path, rset):
    a, b = tmp_path / "light", tmp_path / "dark"
    a.mkdir()
    b.mkdir()
    assert rset.variants_from([b, a]) == [("dark", b), ("light", a)]


def test_variants_from_root_walks_subfolders_in_natural_order(tmp_path, rset):
    for name in ("v10", "v2", "v1", "_raster", ".hidden"):
        (tmp_path / name).mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    out = rset.variants_from([tmp_path])
    assert [n for n, _ in out] == ["v1", "v2", "v10"]
    assert all(p.parent == tmp_path for _, p in out)


def test_variants_from_order_picks_and_orders_variants(tmp_path, rset):
    for name in ("en-US", "de-DE", "ar-SA"):
        (tmp_path / name).mkdir()
    out = rset.variants_from([tmp_path], order="ar-SA, en-US")
    assert [n for n, _ in out] == ["ar-SA", "en-US"]


def test_variants_from_unknown_order_name_raises(tmp_path, rset):
    (tmp_path / "light").mkdir()
    (tmp_path / "dark").mkdir()
    with pytest.raises(rset.cd.CompareError) as e:
        rset.variants_from([tmp_path], order="light,sepia")
    assert "sepia" in str(e.value) and "not variants" in str(e.value)


def test_variants_from_missing_folder_raises(tmp_path, rset):
    with pytest.raises(rset.cd.CompareError, match="not a folder"):
        rset.variants_from([tmp_path / "nope"])


def test_variants_from_root_without_subfolders_raises(tmp_path, rset):
    with pytest.raises(rset.cd.CompareError, match="no sub-folders"):
        rset.variants_from([tmp_path])


def test_variants_from_duplicate_folder_names_raise(tmp_path, rset):
    a, b = tmp_path / "x" / "shots", tmp_path / "y" / "shots"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    with pytest.raises(rset.cd.CompareError, match="same folder name"):
        rset.variants_from([a, b])


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _two_variant_tree(root):
    """light: home 40x30, settings 20x20; dark: home 50x20 (settings missing)."""
    _png(root / "light" / "home.png", (40, 30))
    _png(root / "light" / "settings.png", (20, 20), (20, 120, 220, 255))
    _png(root / "dark" / "home.png", (50, 20), (30, 30, 30, 255))
    return [("light", root / "light"), ("dark", root / "dark")]


def test_build_one_group_per_screen_frames_in_variant_order(tmp_path, rset):
    variants = _two_variant_tree(tmp_path / "caps")
    res = rset.build(variants, tmp_path / "review")
    assert res["ok"] and res["variants"] == ["light", "dark"]
    assert res["screens"] == 2 and res["frames"] == 4
    data = json.loads(Path(res["frames_json"]).read_text(encoding="utf-8"))
    assert [g["label"] for g in data["groups"]] == ["home.png", "settings.png"]
    assert [(it["group"], it["meta"]["variant"]) for it in data["items"]] == [
        ("s1", "light"), ("s1", "dark"), ("s2", "light"), ("s2", "dark")]
    assert [it["label"] for it in data["items"]] == ["light", "dark", "light", "dark"]
    assert all(Path(f).is_file() for f in data["frames"])


def test_build_missing_variant_gets_placeholder(tmp_path, rset):
    variants = _two_variant_tree(tmp_path / "caps")
    res = rset.build(variants, tmp_path / "review")
    # the gap is reported by name, and the frame keeps its slot in the order
    assert res["missing"] == {"settings.png": ["dark"]}
    data = json.loads(Path(res["frames_json"]).read_text(encoding="utf-8"))
    placeholder = data["items"][3]
    assert placeholder["meta"] == {"screen": "settings.png", "variant": "dark", "source": None}
    assert data["groups"][1]["meta"]["variants"]["dark"] is None
    assert Path(placeholder["path"]).is_file()


def test_build_pads_every_frame_of_a_screen_to_the_largest_variant(tmp_path, rset):
    variants = _two_variant_tree(tmp_path / "caps")
    res = rset.build(variants, tmp_path / "review")
    data = json.loads(Path(res["frames_json"]).read_text(encoding="utf-8"))
    band = rset.sp.band_height((50, 30))                  # small frames: scale 1
    sizes = [Image.open(it["path"]).size for it in data["items"]]
    assert sizes[0] == sizes[1] == (50, 30 + band)       # home: max(40, 50) x max(30, 20)
    assert sizes[2] == sizes[3] == (20, 20 + band)       # settings: only light exists


def test_build_labels_skip_incomplete_and_empty(tmp_path, rset):
    variants = _two_variant_tree(tmp_path / "caps")
    res = rset.build(variants, tmp_path / "r1", labels=["Light", "Dark"], skip_incomplete=True)
    assert res["screens"] == 1 and res["frames"] == 2
    data = json.loads(Path(res["frames_json"]).read_text(encoding="utf-8"))
    assert [it["label"] for it in data["items"]] == ["Light", "Dark"]
    (tmp_path / "e1").mkdir()
    (tmp_path / "e2").mkdir()
    with pytest.raises(rset.cd.CompareError, match="no images"):
        rset.build([("e1", tmp_path / "e1"), ("e2", tmp_path / "e2")], tmp_path / "r2")


def test_build_frames_json_loads_with_review_manifest(tmp_path, rset, rm):
    variants = _two_variant_tree(tmp_path / "caps")
    res = rset.build(variants, tmp_path / "review", title="Set")
    m = rm.load(res["frames_json"])
    assert m["title"] == "Set" and m["marks"] == "groups"
    assert [g["id"] for g in m["groups"]] == ["s1", "s2"]
    assert len(m["items"]) == 4
    assert all(Path(it["path"]).is_file() for it in m["items"])


# ---------------------------------------------------------------------------
# review_set CLI
# ---------------------------------------------------------------------------

def test_review_set_cli_success(tmp_path):
    _two_variant_tree(tmp_path / "caps")
    r = _run(REVIEW_SET_SCRIPT, tmp_path / "caps", "--order", "dark,light",
             "--out", tmp_path / "review")
    assert r.returncode == 0, r.stderr
    res = _last_json(r.stdout)
    assert res["ok"] and res["variants"] == ["dark", "light"] and res["frames"] == 4
    assert Path(res["frames_json"]).is_file()


@pytest.mark.parametrize("extra, needle", [
    (["--order", "light,sepia"], "sepia"),
    (["--labels", "only-one"], "--labels"),
])
def test_review_set_cli_bad_arguments_exit_2(tmp_path, extra, needle):
    _two_variant_tree(tmp_path / "caps")
    r = _run(REVIEW_SET_SCRIPT, tmp_path / "caps", *extra, "--out", tmp_path / "review")
    assert r.returncode == 2
    res = _last_json(r.stdout)
    assert res["ok"] is False and needle in res["error"]


def test_review_set_cli_missing_folder_exit_2(tmp_path):
    r = _run(REVIEW_SET_SCRIPT, tmp_path / "nope", "--out", tmp_path / "review")
    assert r.returncode == 2
    assert "not a folder" in _last_json(r.stdout)["error"]


# ===========================================================================================
# rasterize.py
# ===========================================================================================

def _svg(path, attrs):
    path = Path(path)
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}></svg>', encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# svg_size / target_size
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("attrs, expected", [
    ('width="100px" height="50px"', (100.0, 50.0)),
    ('width="100" height="50"', (100.0, 50.0)),
    ('width="72pt" height="36pt"', (96.0, 48.0)),
    ('width="25.4mm" height="12.7mm"', (96.0, 48.0)),
    ('width="1in" height="0.5in"', (96.0, 48.0)),
    ('viewBox="0 0 200 100"', (200.0, 100.0)),
    ('viewBox="0,0,200,100"', (200.0, 100.0)),
    ('width="400" viewBox="0 0 200 100"', (400.0, 200.0)),
    ('height="50" viewBox="0 0 200 100"', (100.0, 50.0)),
    ('width="50%" height="50%" viewBox="0 0 200 100"', (200.0, 100.0)),
])
def test_svg_size(tmp_path, rz, attrs, expected):
    assert rz.svg_size(_svg(tmp_path / "a.svg", attrs)) == pytest.approx(expected)


def test_svg_size_centimetres(tmp_path, rz):
    assert rz.svg_size(_svg(tmp_path / "a.svg", 'width="2.54cm" height="1.27cm"')) == \
        pytest.approx((96.0, 48.0))


@pytest.mark.parametrize("attrs", ['width="50%" height="50%"', "", 'width="10em" height="5em"'])
def test_svg_size_without_usable_size_is_none(tmp_path, rz, attrs):
    assert rz.svg_size(_svg(tmp_path / "a.svg", attrs)) is None


def test_svg_size_malformed_or_missing_file_is_none(tmp_path, rz):
    bad = tmp_path / "bad.svg"
    bad.write_text("<svg width='10'", encoding="utf-8")
    assert rz.svg_size(bad) is None
    assert rz.svg_size(tmp_path / "missing.svg") is None


def test_target_size(tmp_path, rz):
    s = _svg(tmp_path / "a.svg", 'viewBox="0 0 200 100"')
    assert rz.target_size(s) == (200, 100)
    assert rz.target_size(s, width=50) == (50, 25)
    assert rz.target_size(s, height=50) == (100, 50)
    assert rz.target_size(s, width=64, height=64) == (64, 64)
    assert rz.target_size(s, scale=2.5) == (500, 250)


def test_target_size_unknown_size_uses_browser_default(tmp_path, rz):
    s = _svg(tmp_path / "a.svg", "")
    assert rz.target_size(s) == (300, 150)
    assert rz.target_size(s, scale=2) == (600, 300)


# ---------------------------------------------------------------------------
# detect / choose
# ---------------------------------------------------------------------------

EXPECTED_ORDER = ("resvg", "rsvg-convert", "cairosvg", "inkscape", "playwright", "chrome")


def _detect(rz, present):
    """detect() with fakes: only the backends named in present are found."""
    return rz.detect(which=lambda n: f"fake/{n}" if n in present else None,
                     has_module=lambda n: n in present,
                     find_chrome=lambda: "fake/chrome" if "chrome" in present else None,
                     platform="linux")


def test_backend_order_constant(rz):
    assert rz.BACKENDS == EXPECTED_ORDER


def test_detect_reports_every_backend_in_order(rz):
    found = _detect(rz, set(EXPECTED_ORDER))
    assert tuple(found) == EXPECTED_ORDER
    assert found["resvg"] == "fake/resvg" and found["inkscape"] == "fake/inkscape"
    assert found["cairosvg"] == found["playwright"] == "python package"
    assert found["chrome"] == "fake/chrome"
    assert all(v is None for v in _detect(rz, set()).values())


@pytest.mark.parametrize("i", range(len(EXPECTED_ORDER)))
def test_choose_prefers_earlier_backends(rz, i):
    found = _detect(rz, set(EXPECTED_ORDER[i:]))
    assert rz.choose(found) == EXPECTED_ORDER[i]


def test_choose_nothing_found_names_install_options(rz):
    with pytest.raises(rz.RasterizeError) as e:
        rz.choose(_detect(rz, set()))
    msg = str(e.value)
    for option in ("resvg", "rsvg-convert", "cairosvg", "Inkscape", "playwright", "Chrome"):
        assert option in msg


def test_choose_forced_backend_missing(rz):
    found = _detect(rz, {"chrome"})
    with pytest.raises(rz.RasterizeError) as e:
        rz.choose(found, "resvg")
    assert "resvg not found" in str(e.value) and "chrome" in str(e.value)
    assert rz.choose(found, "chrome") == "chrome"
    with pytest.raises(rz.RasterizeError, match="unknown backend"):
        rz.choose(found, "gimp")


def test_rasterize_errors_before_running_anything(tmp_path, rz):
    with pytest.raises(rz.RasterizeError, match="SVG not found"):
        rz.rasterize(tmp_path / "missing.svg", tmp_path / "o.png", found={})
    s = _svg(tmp_path / "a.svg", 'viewBox="0 0 10 10"')
    with pytest.raises(rz.RasterizeError, match="no SVG rasteriser"):
        rz.rasterize(s, tmp_path / "o.png", found=_detect(rz, set()))
    assert not (tmp_path / "o.png").exists()


# ---------------------------------------------------------------------------
# command
# ---------------------------------------------------------------------------

def test_command_resvg(rz):
    assert rz.command("resvg", "resvg", "in.svg", "out.png", 40, 20) == \
        ["resvg", "--width", "40", "--height", "20", "in.svg", "out.png"]


def test_command_rsvg_convert(rz):
    cmd = rz.command("rsvg-convert", "rsvg-convert", "in.svg", "out.png", 40, 20)
    assert cmd[0] == "rsvg-convert" and cmd[-1] == "in.svg"
    assert cmd[cmd.index("--width") + 1] == "40" and cmd[cmd.index("--height") + 1] == "20"
    assert cmd[cmd.index("-o") + 1] == "out.png"
    assert "--keep-aspect-ratio" in cmd


def test_command_inkscape(rz):
    cmd = rz.command("inkscape", "inkscape", "in.svg", "out.png", 40, 20)
    assert cmd[:2] == ["inkscape", "in.svg"]
    assert cmd[2:] == ["--export-type=png", "--export-filename=out.png",
                       "--export-width=40", "--export-height=20"]


def test_command_unknown_backend(rz):
    with pytest.raises(ValueError):
        rz.command("chrome", "chrome", "in.svg", "out.png", 1, 1)


# ---------------------------------------------------------------------------
# flatten (colour and checker backgrounds)
# ---------------------------------------------------------------------------

def _rgba_strip(path):
    """4 x 1 RGBA: opaque red, transparent, half-alpha black, opaque blue."""
    arr = np.array([[[255, 0, 0, 255], [0, 0, 0, 0], [0, 0, 0, 128], [0, 0, 255, 255]]],
                   np.uint8)
    Image.fromarray(arr).save(path)                     # uint8 H x W x 4 -> RGBA
    return path


def test_flatten_onto_white(tmp_path, rz):
    p = _rgba_strip(tmp_path / "a.png")
    rz.flatten(p, "#ffffff")
    with Image.open(p) as im:
        assert im.mode == "RGB"
        px = [im.getpixel((x, 0)) for x in range(4)]
    assert px[0] == (255, 0, 0) and px[1] == (255, 255, 255) and px[3] == (0, 0, 255)
    assert all(abs(c - 127) <= 1 for c in px[2])


def test_flatten_transparent_leaves_file_alone(tmp_path, rz):
    p = _rgba_strip(tmp_path / "a.png")
    before = p.read_bytes()
    rz.flatten(p, "transparent")
    assert p.read_bytes() == before


def test_flatten_bad_background(tmp_path, rz):
    p = _rgba_strip(tmp_path / "a.png")
    with pytest.raises(rz.RasterizeError, match="#rrggbb"):
        rz.flatten(p, "#fff")


def test_flatten_checker(tmp_path, rz):
    p = tmp_path / "c.png"
    arr = np.zeros((16, 16, 4), np.uint8)
    arr[15, 15] = (10, 200, 10, 255)
    Image.fromarray(arr).save(p)                        # uint8 H x W x 4 -> RGBA
    rz.flatten(p, "checker")
    with Image.open(p) as im:
        assert im.mode == "RGB"
        dark, light = rz.CHECKER
        n = rz.CHECK_PX
        assert im.getpixel((0, 0)) == dark
        assert im.getpixel((n, 0)) == light
        assert im.getpixel((0, n)) == light
        assert im.getpixel((n, n - 1)) == light
        assert im.getpixel((15, 15)) == (10, 200, 10)


# ---------------------------------------------------------------------------
# rasterize CLI (lookups only; nothing is rendered)
# ---------------------------------------------------------------------------

def test_rasterize_cli_list_backends(tmp_path):
    r = _run(RASTERIZE_SCRIPT, "--list-backends", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    res = _last_json(r.stdout)
    assert res["ok"] is True
    assert list(res["backends"]) == list(EXPECTED_ORDER)
    assert res["first"] is None or res["first"] in EXPECTED_ORDER


def test_rasterize_cli_without_inputs_exit_2(tmp_path):
    r = _run(RASTERIZE_SCRIPT, cwd=tmp_path)
    assert r.returncode == 2
    assert "--out" in _last_json(r.stdout)["error"]


# ===========================================================================================
# web_capture.py
# ===========================================================================================

# ---------------------------------------------------------------------------
# parse_breakpoints
# ---------------------------------------------------------------------------

def test_parse_breakpoints_default(wc):
    assert wc.parse_breakpoints(wc.DEFAULT_BREAKPOINTS) == [
        ("mobile", 375, None), ("tablet", 768, None), ("desktop", 1440, None)]


@pytest.mark.parametrize("text, expected", [
    ("tablet=768x1024", [("tablet", 768, 1024)]),
    (" a=320 , b_2=640x480 ", [("a", 320, None), ("b_2", 640, 480)]),
    ("1024", [("w1024", 1024, None)]),
    ("1024x600", [("w1024x600", 1024, 600)]),
])
def test_parse_breakpoints_forms(wc, text, expected):
    assert wc.parse_breakpoints(text) == expected


@pytest.mark.parametrize("text", ["mobile=abc", "bad name=375", "=375", "mobile=375x",
                                  "mobile=-5", "", " , "])
def test_parse_breakpoints_malformed(wc, text):
    with pytest.raises(wc.CaptureError):
        wc.parse_breakpoints(text)


# ---------------------------------------------------------------------------
# page_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("page, name", [
    ("http://localhost:3000/", "index"),
    ("http://localhost:3000", "index"),
    ("http://localhost:3000/pricing", "pricing"),
    ("http://localhost:3000/pricing/", "pricing"),
    ("http://localhost:3000/pricing.html", "pricing"),
    ("http://localhost:3000/index.html", "index"),
    ("http://localhost:3000/docs/index.html", "docs"),
    ("http://localhost:3000/docs/setup", "docs-setup"),
    ("http://localhost:3000/pricing?plan=pro", "pricing_plan-pro"),
    ("http://localhost:3000/pricing#faq", "pricing_faq"),
    ("site/index.html", "index"),
    ("site/about.html", "about"),
    ("file:///site/contact.html", "contact"),
])
def test_page_name(wc, page, name):
    assert wc.page_name(page) == name


def test_page_name_file_ending_in_index(wc):
    assert wc.page_name("http://localhost:3000/myindex.html") == "myindex"


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

PAGES = ["http://localhost:3000/", "http://localhost:3000/pricing"]


def _rel(job, root):
    return Path(job["out"]).relative_to(Path(root).resolve()).as_posix()


def test_plan_one_browser(tmp_path, wc):
    jobs = wc.plan(PAGES, tmp_path, "after")
    assert len(jobs) == 2 * 3 * 1
    assert [_rel(j, tmp_path) for j in jobs[:3]] == [
        "after/index__mobile.png", "after/index__tablet.png", "after/index__desktop.png"]
    assert jobs[0]["width"] == 375 and jobs[0]["height"] == wc.DEFAULT_HEIGHT
    assert jobs[0]["url"] == PAGES[0] and jobs[0]["local"] is False


def test_plan_several_browsers_get_their_own_folders(tmp_path, wc):
    jobs = wc.plan(PAGES, tmp_path, "after", "m=375,d=1440x1000", ("chromium", "firefox", "webkit"))
    assert len(jobs) == 2 * 2 * 3
    folders = {_rel(j, tmp_path).split("/")[0] for j in jobs}
    assert folders == {"after-chromium", "after-firefox", "after-webkit"}
    assert {j["height"] for j in jobs if j["breakpoint"] == "d"} == {1000}


def test_plan_group_by_breakpoint_and_height(tmp_path, wc):
    jobs = wc.plan(PAGES[:1], tmp_path, "v1", "mobile=375", height=700, group_by="breakpoint")
    assert [_rel(j, tmp_path) for j in jobs] == ["v1/mobile/index.png"]
    assert jobs[0]["height"] == 700


def test_plan_names_override(tmp_path, wc):
    jobs = wc.plan(PAGES, tmp_path, "after", "mobile=375", names=["home", "plans"])
    assert [_rel(j, tmp_path) for j in jobs] == ["after/home__mobile.png",
                                                  "after/plans__mobile.png"]


def test_plan_local_file(tmp_path, wc):
    page = tmp_path / "site" / "index.html"
    page.parent.mkdir()
    page.write_text("<html></html>", encoding="utf-8")
    jobs = wc.plan([str(page)], tmp_path / "caps", "v", "mobile=375")
    assert jobs[0]["name"] == "index" and jobs[0]["local"] is True
    assert jobs[0]["url"].startswith("file:")


@pytest.mark.parametrize("pages, names, needle", [
    (["http://h/a/index.html", "http://h/a/"], None, "two pages are named"),
    (PAGES, ["same", "same"], "two pages are named"),
    (PAGES, ["only-one"], "--names gives 1"),
    (PAGES, ["ok", "bad name"], "page name"),
    (["not-a-url-or-file.html"], None, "not a URL and not a file"),
])
def test_plan_errors(tmp_path, wc, pages, names, needle):
    with pytest.raises(wc.CaptureError, match=needle):
        wc.plan(pages, tmp_path, "v", "mobile=375", names=names)


# ---------------------------------------------------------------------------
# chrome_args
# ---------------------------------------------------------------------------

def test_chrome_args_basic(wc):
    args = wc.chrome_args("chrome-bin", "http://h/", "shot.png", 375, 900)
    assert args[0] == "chrome-bin"
    assert "--headless=new" in args
    assert "--window-size=375,900" in args
    assert "--screenshot=shot.png" in args
    assert "--force-device-scale-factor=1" in args
    assert "--default-background-color=00000000" not in args
    assert "--force-dark-mode" not in args
    assert not any(a.startswith("--user-data-dir=") for a in args)
    assert args[-1] == "http://h/"


def test_chrome_args_options(wc):
    args = wc.chrome_args("chrome-bin", "http://h/", "shot.png", 768, 1024, scale=2.0,
                          user_data_dir="profile", color_scheme="dark", transparent=True)
    assert "--window-size=768,1024" in args
    assert "--force-device-scale-factor=2" in args
    assert "--user-data-dir=profile" in args
    assert "--default-background-color=00000000" in args
    assert "--force-dark-mode" in args
    assert args[-1] == "http://h/"


# ---------------------------------------------------------------------------
# find_chrome (fake which, folders under tmp_path)
# ---------------------------------------------------------------------------

def _which_only(*hits):
    calls = []

    def which(name):
        calls.append(name)
        return f"fake/{name}" if name in hits else None
    which.calls = calls
    return which


def _no_home(tmp_path):
    """find_chrome keyword arguments that keep the real machine's folders out."""
    return {"home": tmp_path / "home", "local": tmp_path / "local", "program_files": []}


def test_find_chrome_flag_wins(tmp_path, wc):
    exe = tmp_path / "my-chrome"
    exe.write_bytes(b"")
    assert wc.find_chrome(str(exe), platform="linux", which=_which_only("google-chrome"),
                          **_no_home(tmp_path)) == str(exe)


def test_find_chrome_missing_file_is_skipped_by_the_lookup(tmp_path, wc):
    assert wc.find_chrome(str(tmp_path / "gone"), platform="linux",
                          which=_which_only("chromium"), **_no_home(tmp_path)) == "fake/chromium"


def test_find_chrome_which_order(tmp_path, wc):
    which = _which_only("chromium", "msedge")
    assert wc.find_chrome(platform="linux", which=which, **_no_home(tmp_path)) == "fake/chromium"
    assert which.calls[0] == "chrome-headless-shell"   # a headless shell is looked for first
    assert which.calls[1:] == list(wc.CHROME_NAMES[:which.calls.index("chromium")])


def test_find_chrome_nothing_found(tmp_path, wc):
    assert wc.find_chrome(platform="linux", which=_which_only(), **_no_home(tmp_path)) is None


def test_find_chrome_windows_install_location(tmp_path, wc):
    exe = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    kw = dict(_no_home(tmp_path), program_files=[tmp_path])
    assert wc.find_chrome(platform="win32", which=_which_only(), **kw) == str(exe)


def test_chrome_candidates_per_platform(tmp_path, wc):
    win = wc.chrome_candidates("win32", program_files=[tmp_path], local=tmp_path / "local")
    assert [p.name for p in win] == ["chrome.exe", "msedge.exe", "chrome.exe"] * 2
    assert win[3].parent.parent.parent.parent == tmp_path / "local"
    mac = wc.chrome_candidates("darwin")
    assert any("Google Chrome.app" in str(p) for p in mac)
    assert wc.chrome_candidates("linux") == []


# ---------------------------------------------------------------------------
# pick_backend
# ---------------------------------------------------------------------------

ALL_WEB = {"playwright-python": "python package", "playwright-node": "node package",
           "chrome-cli": "fake/chrome"}


@pytest.mark.parametrize("present, expected", [
    (("playwright-python", "playwright-node", "chrome-cli"), "playwright-python"),
    (("playwright-node", "chrome-cli"), "playwright-node"),
    (("chrome-cli",), "chrome-cli"),
])
def test_pick_backend_auto_order(wc, present, expected):
    found = {k: (v if k in present else None) for k, v in ALL_WEB.items()}
    assert wc.pick_backend("auto", found) == expected


def test_pick_backend_forced(wc):
    found = {"playwright-python": None, "playwright-node": None, "chrome-cli": "fake/chrome"}
    assert wc.pick_backend("chrome-cli", found) == "chrome-cli"
    with pytest.raises(wc.CaptureError) as e:
        wc.pick_backend("playwright-python", found)
    assert "not available" in str(e.value) and "chrome-cli" in str(e.value)


def test_pick_backend_none_found(wc):
    with pytest.raises(wc.CaptureError, match="Playwright"):
        wc.pick_backend("auto", {k: None for k in ALL_WEB})


def test_capture_chrome_cli_rejects_other_browsers_before_running(wc):
    jobs = [{"browser": "firefox", "out": "x.png"}]
    with pytest.raises(wc.CaptureError, match="Chromium only"):
        wc.capture(jobs, "chrome-cli", {})


# ---------------------------------------------------------------------------
# tile / _fix_size
# ---------------------------------------------------------------------------

def test_tile_splits_tall_capture(tmp_path, wc):
    arr = np.zeros((250, 30, 3), np.uint8)
    arr[:, :, 0] = (np.arange(250) % 256)[:, None]
    p = tmp_path / "home__mobile.png"
    Image.fromarray(arr).save(p)                        # uint8 H x W x 3 -> RGB
    tiles = wc.tile(p, 100)
    assert [Path(t).name for t in tiles] == ["home__mobile__t01.png", "home__mobile__t02.png",
                                             "home__mobile__t03.png"]
    assert not p.exists()
    sizes = [Image.open(t).size for t in tiles]
    assert sizes == [(30, 100), (30, 100), (30, 50)]
    for k, t in enumerate(tiles):
        got = np.asarray(Image.open(t))
        assert np.array_equal(got, arr[k * 100:(k + 1) * 100])


def test_tile_short_capture_is_kept(tmp_path, wc):
    p = _png(tmp_path / "short.png", (30, 80))
    assert wc.tile(p, 100) == [str(p)]
    assert p.is_file()


@pytest.mark.parametrize("src, dst", [((10, 12), (10, 10)), ((8, 8), (10, 10)),
                                      ((10, 10), (10, 10))])
def test_fix_size(tmp_path, wc, src, dst):
    p = _png(tmp_path / "s.png", src)
    wc._fix_size(str(p), *dst)
    with Image.open(p) as im:
        assert im.size == dst


# ---------------------------------------------------------------------------
# web_capture CLI --dry-run (plans only; no browser is looked for or started)
# ---------------------------------------------------------------------------

def test_web_capture_cli_dry_run(tmp_path):
    r = _run(WEB_CAPTURE_SCRIPT, *PAGES, "--out", "caps", "--version", "after", "--dry-run",
             cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    res = _last_json(r.stdout)
    assert res["ok"] is True and res["dry_run"] is True
    assert len(res["jobs"]) == 6
    assert {j["breakpoint"] for j in res["jobs"]} == {"mobile", "tablet", "desktop"}
    assert _files_under(tmp_path) == []


@pytest.mark.parametrize("extra, needle", [
    (["--breakpoints", "mobile=abc"], "breakpoint"),
    (["--browsers", "netscape"], "unknown browser"),
    (["--version", "bad/name"], "--version"),
])
def test_web_capture_cli_bad_arguments_exit_2(tmp_path, extra, needle):
    r = _run(WEB_CAPTURE_SCRIPT, PAGES[0], "--out", "caps", *extra, "--dry-run", cwd=tmp_path)
    assert r.returncode == 2
    res = _last_json(r.stdout)
    assert res["ok"] is False and needle in res["error"]


# ===========================================================================================
# app_capture.py
# ===========================================================================================

# ---------------------------------------------------------------------------
# iOS
# ---------------------------------------------------------------------------

def test_ios_variants_names(ac):
    names = [n for n, _ in ac.ios_variants(["light", "dark"], ["large", "extra-extra-large"], None)]
    assert names == ["ios-light-l", "ios-light-xxl", "ios-dark-l", "ios-dark-xxl"]
    [(name, settings)] = ac.ios_variants(["dark"], ["accessibility-medium"], ["de-DE"])
    assert name == "ios-dark-ax1-de-DE"
    assert settings == {"appearance": "dark", "content_size": "accessibility-medium",
                        "locale": "de-DE"}


def test_ios_variants_no_axes(ac):
    assert ac.ios_variants([], [], []) == [
        ("ios", {"appearance": None, "content_size": None, "locale": None})]


def test_ios_commands_order(ac):
    s = {"appearance": "dark", "content_size": "large", "locale": None}
    cmds = ac.ios_commands("booted", s, "out.png")
    assert cmds == [
        ["xcrun", "simctl", "ui", "booted", "appearance", "dark"],
        ["xcrun", "simctl", "ui", "booted", "content_size", "large"],
        ["xcrun", "simctl", "io", "booted", "screenshot", "--type=png", "out.png"],
    ]


def test_ios_commands_locale_relaunches_app(ac):
    s = {"appearance": None, "content_size": None, "locale": "de-DE"}
    cmds = ac.ios_commands("booted", s, "out.png", bundle="com.example.app")
    assert cmds[0] == ["xcrun", "simctl", "terminate", "booted", "com.example.app"]
    assert cmds[1] == ["xcrun", "simctl", "launch", "booted", "com.example.app",
                       "-AppleLanguages", "(de)", "-AppleLocale", "de_DE"]
    assert cmds[-1][2:4] == ["io", "booted"] and cmds[-1][-1] == "out.png"
    # without a bundle there is nothing to relaunch
    assert len(ac.ios_commands("booted", s, "out.png")) == 1


def test_ios_status_bar(ac):
    on, off = ac.ios_status_bar("booted", True), ac.ios_status_bar("booted", False)
    assert on[:5] == ["xcrun", "simctl", "status_bar", "booted", "override"]
    assert on[on.index("--time") + 1] == "9:41"
    assert off == ["xcrun", "simctl", "status_bar", "booted", "clear"]


# ---------------------------------------------------------------------------
# Android
# ---------------------------------------------------------------------------

def test_android_variants_names(ac):
    names = [n for n, _ in ac.android_variants(["no", "yes", "auto"], ["1.3"], None)]
    assert names == ["android-day-fs1.3", "android-night-fs1.3", "android-night-auto-fs1.3"]
    [(name, s)] = ac.android_variants(None, None, ["1080x2400@420"])
    assert name == "android-1080x2400-420"
    assert s == {"night": None, "font_scale": None, "display": "1080x2400@420"}


def test_android_commands(ac):
    s = {"night": "yes", "font_scale": "1.3", "display": "1080x2400@420"}
    assert ac.android_commands("adb", "emu-1", s) == [
        ["adb", "-s", "emu-1", "shell", "cmd", "uimode", "night", "yes"],
        ["adb", "-s", "emu-1", "shell", "settings", "put", "system", "font_scale", "1.3"],
        ["adb", "-s", "emu-1", "shell", "wm", "size", "1080x2400"],
        ["adb", "-s", "emu-1", "shell", "wm", "density", "420"],
    ]


def test_android_commands_without_serial_or_density(ac):
    s = {"night": None, "font_scale": None, "display": "720x1280"}
    assert ac.android_commands("adb", None, s) == [["adb", "shell", "wm", "size", "720x1280"]]
    assert ac.android_commands("adb", None, {"night": None, "font_scale": None,
                                             "display": None}) == []
    assert ac.android_screenshot_cmd("adb", "emu-1") == ["adb", "-s", "emu-1", "exec-out",
                                                         "screencap", "-p"]


@pytest.mark.parametrize("text, expected", [("1080x2400@420", (1080, 2400, 420)),
                                            (" 720x1280 ", (720, 1280, None))])
def test_parse_display(ac, text, expected):
    assert ac.parse_display(text) == expected


@pytest.mark.parametrize("text", ["1080", "1080x", "axb", "1080x2400@", "1080*2400", ""])
def test_parse_display_malformed(ac, text):
    with pytest.raises(ac.CaptureError, match="WIDTHxHEIGHT"):
        ac.parse_display(text)


def test_android_demo_mode_on_off(ac):
    on = ac.android_demo_mode("adb", "emu-1", True)
    assert on[0] == ["adb", "-s", "emu-1", "shell", "settings", "put", "global",
                     "sysui_demo_allowed", "1"]
    broadcast = ["adb", "-s", "emu-1", "shell", "am", "broadcast", "-a",
                 "com.android.systemui.demo", "-e", "command"]
    assert all(c[:len(broadcast)] == broadcast for c in on[1:])
    assert on[1][len(broadcast):] == ["enter"]
    assert on[2][len(broadcast):] == ["clock", "-e", "hhmm", "0941"]
    off = ac.android_demo_mode("adb", None, False)
    assert off == [["adb", "shell", "am", "broadcast", "-a", "com.android.systemui.demo",
                    "-e", "command", "exit"]]


# ---------------------------------------------------------------------------
# find_adb (fake which, folders under tmp_path)
# ---------------------------------------------------------------------------

ADB_NAME = "adb.exe" if os.name == "nt" else "adb"


def _sdk(root):
    exe = Path(root) / "platform-tools" / ADB_NAME
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    return exe


def _adb_where(tmp_path):
    return {"home": tmp_path / "home", "local": tmp_path / "local", "config": {}}


def test_find_adb_flag_wins(tmp_path, ac):
    exe = _sdk(tmp_path / "sdk")
    assert ac.find_adb(str(exe), which=lambda n: "fake/adb", **_adb_where(tmp_path)) == str(exe)


def test_find_adb_flag_that_is_not_a_file_is_an_error(tmp_path, ac):
    with pytest.raises(ac.CaptureError, match="--adb"):
        ac.find_adb(str(tmp_path / "nope"), **_adb_where(tmp_path))


def test_find_adb_config_file_value(tmp_path, ac):
    exe = _sdk(tmp_path / "home" / "sdk")
    kw = dict(_adb_where(tmp_path), config={"adb": "~/sdk/platform-tools/" + ADB_NAME})
    assert ac.find_adb(which=lambda n: "fake/adb", **kw) == str(exe)


def test_find_adb_which_before_default_folder(tmp_path, ac):
    _sdk(tmp_path / "home" / "Android" / "Sdk")
    assert ac.find_adb(which=lambda n: "fake/adb" if n == "adb" else None, platform="linux",
                       **_adb_where(tmp_path)) == "fake/adb"


@pytest.mark.parametrize("platform, parts", [
    ("linux", ("home", "Android", "Sdk")),
    ("darwin", ("home", "Library", "Android", "sdk")),
    ("win32", ("local", "Android", "Sdk")),
])
def test_find_adb_default_sdk_folder(tmp_path, ac, platform, parts):
    name = "adb.exe" if platform == "win32" else "adb"
    exe = tmp_path.joinpath(*parts, "platform-tools", name)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    none = lambda n: None  # noqa: E731
    assert ac.find_adb(which=none, platform=platform, **_adb_where(tmp_path)) == str(exe)


def test_find_adb_nothing_found(tmp_path, ac):
    assert ac.find_adb(which=lambda n: None, platform="linux", **_adb_where(tmp_path)) is None


# ---------------------------------------------------------------------------
# Electron
# ---------------------------------------------------------------------------

def test_electron_variants(tmp_path, ac):
    vs = ac.electron_variants(["1280x800", "390x844"], ["light", "dark"], tmp_path, "Home Screen")
    assert [v["name"] for v in vs] == ["electron-1280x800-light", "electron-1280x800-dark",
                                       "electron-390x844-light", "electron-390x844-dark"]
    assert (vs[0]["width"], vs[0]["height"], vs[0]["color_scheme"]) == (1280, 800, "light")
    assert Path(vs[0]["out"]) == tmp_path / "electron-1280x800-light" / "Home-Screen.png"


def test_electron_variants_defaults_and_bad_size(tmp_path, ac):
    [v] = ac.electron_variants([], [], tmp_path, "home")
    assert v["name"] == "electron" and v["width"] is None and v["color_scheme"] is None
    with pytest.raises(ac.CaptureError, match="WIDTHxHEIGHT"):
        ac.electron_variants(["1280"], [], tmp_path, "home")


# ---------------------------------------------------------------------------
# app_capture CLI (dry runs and "tool missing" errors; no tool is started)
# ---------------------------------------------------------------------------

def _main_json(ac, argv, capsys, monkeypatch, tmp_path, adb=None):
    """Run app_capture.main in this process from tmp_path, with find_adb answering `adb`, so
    whatever adb the machine has is never found or started; returns (exit code, result)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ac, "find_adb", lambda *a, **k: adb)
    code = ac.main(argv)
    return code, _last_json(capsys.readouterr().out)


def test_app_capture_cli_ios_dry_run(tmp_path):
    r = _run(APP_CAPTURE_SCRIPT, "ios", "--screen", "home", "--out", "caps",
             "--appearance", "light,dark", "--content-size", "large", "--clean-status-bar",
             "--dry-run", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    res = _last_json(r.stdout)
    assert res["ok"] and res["dry_run"] and res["platform"] == "ios"
    assert [Path(f).parent.name for f in res["files"]] == ["ios-light-l", "ios-dark-l"]
    shots = [c for c in res["commands"] if "screenshot" in c]
    assert len(shots) == 2 and all(c[:2] == ["xcrun", "simctl"] for c in res["commands"])
    assert res["commands"][-1][-1] == "clear"          # status bar put back
    assert _files_under(tmp_path) == []


def test_app_capture_cli_android_dry_run(tmp_path, ac, capsys, monkeypatch):
    code, res = _main_json(ac, ["android", "--screen", "home", "--out", "caps",
                                "--night", "no,yes", "--font-scale", "1.0,1.3",
                                "--display", "1080x2400@420", "--demo-mode", "--dry-run"],
                           capsys, monkeypatch, tmp_path)
    assert code == 0
    assert res["ok"] and res["dry_run"] and res["platform"] == "android"
    assert len(res["files"]) == 4
    assert all(c[0] == "adb" for c in res["commands"])
    shots = [c for c in res["commands"] if "screencap" in c]
    assert len(shots) == 4 and all(c[-2] == ">" for c in shots)
    assert ["adb", "shell", "wm", "size", "reset"] in res["commands"]
    assert _files_under(tmp_path) == []


def test_app_capture_cli_electron_dry_run(tmp_path):
    r = _run(APP_CAPTURE_SCRIPT, "electron", "--screen", "home", "--out", "caps",
             "--main", "main.js", "--size", "800x600", "--color-scheme", "light,dark",
             "--dry-run", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    res = _last_json(r.stdout)
    assert [Path(f).parent.name for f in res["files"]] == ["electron-800x600-light",
                                                          "electron-800x600-dark"]
    assert _files_under(tmp_path) == []


@pytest.mark.parametrize("args, needle", [
    (["ios", "--appearance", "sepia"], "unknown appearance"),
    (["ios", "--locales", "de-DE"], "--bundle"),
    (["android", "--night", "maybe"], "unknown night mode"),
    (["android", "--font-scale", "big"], "not a number"),
    (["android", "--display", "huge"], "WIDTHxHEIGHT"),
])
def test_app_capture_cli_bad_arguments_exit_2(tmp_path, args, needle, ac, capsys, monkeypatch):
    code, res = _main_json(ac, [*args, "--screen", "home", "--out", "caps", "--dry-run"],
                           capsys, monkeypatch, tmp_path)
    assert code == 2
    assert res["ok"] is False and needle in res["error"]


@pytest.mark.skipif(sys.platform == "darwin", reason="real simctl path on macOS; not exercised")
def test_app_capture_cli_ios_needs_macos(tmp_path):
    r = _run(APP_CAPTURE_SCRIPT, "ios", "--screen", "home", "--out", "caps", cwd=tmp_path)
    assert r.returncode == 2
    res = _last_json(r.stdout)
    assert res["ok"] is False and "macOS" in res["error"]
    assert _files_under(tmp_path) == []


def test_app_capture_cli_android_without_adb(tmp_path, ac, capsys, monkeypatch):
    code, res = _main_json(ac, ["android", "--screen", "home", "--out", "caps"],
                           capsys, monkeypatch, tmp_path)
    assert code == 2
    assert res["ok"] is False and "adb not found" in res["error"]
    assert _files_under(tmp_path) == []


# ---------------------------------------------------------------------------
# Node children: Playwright is resolved from the project folder, not through variables
# ---------------------------------------------------------------------------

def test_node_scripts_load_playwright_from_the_project_folder(wc, ac):
    assert "createRequire" in wc.PLAYWRIGHT_JS and "cfg.project" in wc.PLAYWRIGHT_JS
    assert "createRequire" in ac.ELECTRON_JS and "cfg.cwd" in ac.ELECTRON_JS


def test_run_playwright_node_passes_the_project_and_no_environment(tmp_path, wc, monkeypatch):
    seen = {}

    class Done:
        stdout, stderr = json.dumps({"done": ["a.png"], "failed": []}), ""

    def fake_run(args, **kw):
        seen["kw"] = kw
        seen["cfg"] = json.loads(Path(args[2]).read_text(encoding="utf-8"))
        return Done()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(wc.shutil, "which", lambda name: "node")
    monkeypatch.setattr(wc.subprocess, "run", fake_run)
    assert wc.run_playwright_node([], {"scale": 1.0}) == (["a.png"], [])
    assert seen["cfg"]["project"] == os.getcwd()
    assert "env" not in seen["kw"]
