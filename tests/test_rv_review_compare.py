"""Tests for rv-review/scripts/compare_dirs.py.

Every image is a small synthetic PNG built with numpy + Pillow in tmp_path; the test tool
layouts (Playwright, jest-image-snapshot, Unity, Unreal, Flutter) are recreated on disk with
neutral names. No browser, RV or SVG rasteriser is started: the SVG path runs against a
stand-in rasterize module.
"""
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPARE_SCRIPT = REPO_ROOT / "rv-review" / "scripts" / "compare_dirs.py"

GREY = (128, 128, 128, 255)
WHITE = (255, 255, 255, 255)


def write_png(path, arr):
    """Write a uint8 (H, W, 3|4) or uint16 (H, W) array as a PNG, creating folders."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(arr)).save(path)
    return path


def solid(color=GREY, size=(32, 24)):
    """An RGBA array of one colour; size is (w, h)."""
    w, h = size
    a = np.empty((h, w, 4), np.uint8)
    a[:] = color
    return a


def write_solid(path, color=GREY, size=(32, 24)):
    return write_png(path, solid(color, size))


def noisy(seed, size=(32, 24)):
    w, h = size
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
    a[..., 3] = 255
    return a


def run_main(cd, capsys, *args):
    """cd.main(args) in-process: (exit code, the JSON line it printed)."""
    code = cd.main([str(a) for a in args])
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return code, json.loads(lines[-1])


def read_report(out):
    return json.loads((Path(out) / "compare_report.json").read_text(encoding="utf-8"))


def read_frames_json(out):
    return json.loads((Path(out) / "frames.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# pair_dirs / list_images
# ---------------------------------------------------------------------------

def test_pair_dirs_exact_pairs_added_and_removed(cd, tmp_path):
    base, cand = tmp_path / "base", tmp_path / "cand"
    for rel in ("home.png", "sub/card-1.png", "gone.png"):
        write_solid(base / rel)
    for rel in ("home.png", "sub/card-1.png", "fresh.png"):
        write_solid(cand / rel)
    pairs = {p["key"]: p for p in cd.pair_dirs(base, cand)}
    assert set(pairs) == {"home.png", "sub/card-1.png", "gone.png", "fresh.png"}
    assert pairs["home.png"]["baseline"] == str(base / "home.png")
    assert pairs["home.png"]["candidate"] == str(cand / "home.png")
    assert Path(pairs["sub/card-1.png"]["candidate"]) == cand / "sub" / "card-1.png"
    assert pairs["gone.png"]["candidate"] is None and pairs["gone.png"]["baseline"]
    assert pairs["fresh.png"]["baseline"] is None and pairs["fresh.png"]["candidate"]


def test_pair_dirs_stem_match_pairs_across_extensions(cd, tmp_path):
    design, build = tmp_path / "design", tmp_path / "build"
    design.mkdir()
    (design / "home.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    write_solid(build / "home.png")
    exact = cd.pair_dirs(design, build)
    assert len(exact) == 2 and all(p["baseline"] is None or p["candidate"] is None for p in exact)
    stem = cd.pair_dirs(design, build, match="stem")
    assert len(stem) == 1
    assert stem[0]["key"] == "home"
    assert stem[0]["baseline"].endswith("home.svg") and stem[0]["candidate"].endswith("home.png")


def test_pair_dirs_include_exclude_and_extension_filters(cd, tmp_path):
    base, cand = tmp_path / "base", tmp_path / "cand"
    for root in (base, cand):
        write_solid(root / "screens" / "home.png")
        write_solid(root / "screens" / "settings.png")
        write_solid(root / "icons" / "star.png")
        (root / "notes.txt").write_text("not an image", encoding="utf-8")
        write_solid(root / "node_modules" / "ignored.png")
    keys = [p["key"] for p in cd.pair_dirs(base, cand)]
    assert "notes.txt" not in keys and not any("node_modules" in k for k in keys)
    assert [p["key"] for p in cd.pair_dirs(base, cand, include=("screens/*",))] == \
        ["screens/home.png", "screens/settings.png"]
    assert [p["key"] for p in cd.pair_dirs(base, cand, exclude=("screens/*",))] == ["icons/star.png"]
    assert cd.pair_dirs(base, cand, exts=(".jpg",)) == []


def test_pair_dirs_sorts_keys_naturally(cd, tmp_path):
    base, cand = tmp_path / "base", tmp_path / "cand"
    for n in (10, 2, 1):
        write_solid(base / f"shot{n}.png")
        write_solid(cand / f"shot{n}.png")
    assert [p["key"] for p in cd.pair_dirs(base, cand)] == ["shot1.png", "shot2.png", "shot10.png"]


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def test_metrics_identical_images_are_all_zero(cd):
    a = noisy(1)
    m, mag = cd.metrics(a, a.copy())
    assert m == {"mean_abs": 0.0, "max_abs": 0.0, "changed_pixels": 0,
                 "changed_fraction": 0.0, "bbox": None}
    assert mag.shape == a.shape[:2] and not mag.any()


def test_metrics_one_level_change(cd):
    a = solid(GREY, (20, 20))
    b = a.copy()
    b[5, 7, 0] += 1                       # one level, one channel, one pixel at x=7, y=5
    m, mag = cd.metrics(a, b)
    assert m["changed_pixels"] == 1
    assert m["changed_fraction"] == round(1 / 400, 6)
    assert m["max_abs"] == round(1 / 255, 6)
    assert m["mean_abs"] == round(1 / (400 * 4 * 255), 6)
    assert m["bbox"] == [7, 5, 8, 6]
    assert mag[5, 7] == 1.0


def test_metrics_threshold_suppresses_small_changes(cd):
    a = solid(GREY, (20, 20))
    b = a.copy()
    b[0, 0, :3] += 2                      # two levels
    b[10, 10, :3] += 9                    # nine levels
    m, _ = cd.metrics(a, b, threshold=2)
    assert m["changed_pixels"] == 1 and m["bbox"] == [10, 10, 11, 11]
    m, _ = cd.metrics(a, b, threshold=9)
    assert m["changed_pixels"] == 0 and m["bbox"] is None
    assert m["max_abs"] == round(9 / 255, 6)          # still measured, just not counted


def test_metrics_colour_under_zero_alpha_does_not_count(cd):
    a = solid((255, 0, 0, 0), (16, 16))
    b = solid((0, 255, 90, 0), (16, 16))
    m, _ = cd.metrics(a, b)
    assert m["changed_pixels"] == 0 and m["max_abs"] == 0.0 and m["mean_abs"] == 0.0


def test_metrics_colour_is_premultiplied_by_alpha(cd):
    a = solid((0, 0, 0, 51), (8, 8))                # alpha 51 = 0.2
    b = solid((255, 0, 0, 51), (8, 8))
    m, _ = cd.metrics(a, b)
    assert m["changed_pixels"] == 64
    assert m["max_abs"] == pytest.approx(0.2, abs=1e-4)


def test_metrics_bbox_surrounds_changed_region(cd):
    a = solid(GREY, (40, 30))
    b = a.copy()
    b[4:9, 12:20] = (0, 0, 0, 255)
    b[20, 3] = (255, 255, 255, 255)
    m, _ = cd.metrics(a, b)
    assert m["bbox"] == [3, 4, 20, 21]
    assert m["changed_pixels"] == 5 * 8 + 1


# ---------------------------------------------------------------------------
# pad_to
# ---------------------------------------------------------------------------

def test_pad_to_top_left(cd):
    a = solid(WHITE, (10, 6))
    out, valid = cd.pad_to(a, (14, 9))
    assert out.shape == (9, 14, 4) and valid.shape == (9, 14)
    assert (out[:6, :10] == 255).all() and valid[:6, :10].all()
    assert valid.sum() == 60
    assert (out[6:, :] == 0).all() and (out[:, 10:] == 0).all()     # transparent padding


def test_pad_to_center(cd):
    a = solid(WHITE, (10, 6))
    out, valid = cd.pad_to(a, (14, 10), anchor="center")
    ys, xs = np.nonzero(valid)
    assert (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1) == (2, 2, 12, 8)
    assert (out[2:8, 2:12] == 255).all() and out[0, 0, 3] == 0


def test_pad_to_same_size_is_unchanged(cd):
    a = noisy(3, (12, 8))
    out, valid = cd.pad_to(a, (12, 8))
    assert np.array_equal(out, a) and valid.all()


def test_padding_counts_as_changed(cd):
    small = solid(GREY, (10, 10))
    big = solid(GREY, (12, 10))
    a, va = cd.pad_to(small, (12, 10))
    b, vb = cd.pad_to(big, (12, 10))
    m, mag = cd.metrics(a, b, va, vb)
    assert m["changed_pixels"] == 2 * 10
    assert m["bbox"] == [10, 0, 12, 10]
    assert m["max_abs"] == 1.0 and (mag[:, 10:] == 255).all() and not mag[:, :10].any()


# ---------------------------------------------------------------------------
# diff_image / ramp
# ---------------------------------------------------------------------------

def _diff(cd, a, b, threshold=0, **kw):
    m, mag = cd.metrics(a, b, threshold=threshold)
    return cd.diff_image(a, b, mag, threshold, **kw)


def _is_grey(px):
    return int(px[0]) == int(px[1]) == int(px[2])


def test_ramp_endpoints(cd):
    assert tuple(cd.ramp(np.array(0.0))) == (0, 0, 0)
    assert tuple(cd.ramp(np.array(1.0))) == (255, 255, 150)
    assert tuple(cd.ramp(np.array(cd.HEAT_FLOOR))) == (70, 20, 160)


def test_diff_image_is_rgb_same_size_with_dimmed_grey_background(cd):
    a = solid(WHITE, (24, 16))
    b = a.copy()
    b[3:6, 3:6, :3] = 0
    out = _diff(cd, a, b)
    assert out.shape == (16, 24, 3) and out.dtype == np.uint8
    # unchanged pixels: the candidate's luminance times context (0.3), grey
    assert _is_grey(out[10, 20]) and int(out[10, 20, 0]) == int(255 * cd.DEFAULT_CONTEXT)
    assert not _is_grey(out[4, 4])


def test_diff_image_context_zero_is_black_behind_changes(cd):
    a = solid(WHITE, (8, 8))
    b = a.copy()
    b[0, 0, :3] = 0
    out = _diff(cd, a, b, context=0.0)
    assert tuple(out[7, 7]) == (0, 0, 0)


def test_diff_image_one_level_change_is_visibly_coloured(cd):
    a = solid(GREY, (16, 16))
    b = a.copy()
    b[8, 8, 1] += 1
    out = _diff(cd, a, b)
    px = out[8, 8].astype(int)
    assert px.max() - px.min() > 50                   # clearly coloured, not grey
    v = cd.HEAT_FLOOR + (1 - cd.HEAT_FLOOR) * (1 / 255) * cd.DEFAULT_GAIN
    assert np.abs(px - cd.ramp(np.array(v)).astype(int)).max() <= 1


def test_diff_image_heat_floor_with_zero_gain(cd):
    a = solid(GREY, (8, 8))
    b = a.copy()
    b[2, 2, 2] += 1
    out = _diff(cd, a, b, gain=0.0)
    assert tuple(out[2, 2]) == (70, 20, 160)          # HEAT_FLOOR on the ramp, whatever the size


def test_diff_image_threshold_leaves_small_changes_grey(cd):
    a = solid(GREY, (8, 8))
    b = a.copy()
    b[1, 1, :3] += 2
    b[6, 6, :3] += 40
    out = _diff(cd, a, b, threshold=4)
    assert _is_grey(out[1, 1]) and not _is_grey(out[6, 6])


def test_diff_image_signed_colours_brighter_and_darker_apart(cd):
    a = solid(GREY, (8, 8))
    b = a.copy()
    b[1, 1, :3] = 200                                 # candidate brighter
    b[5, 5, :3] = 40                                  # candidate darker
    out = _diff(cd, a, b, mode="signed")
    up, down = out[1, 1].astype(int), out[5, 5].astype(int)
    assert up[0] == 0 and up[1] > 100 and up[2] > 100            # cyan
    assert down[1] == 0 and down[0] > 100 and down[2] > 100      # magenta
    # the abs mode colours both the same way (by size only)
    ab = _diff(cd, a, b)
    assert _is_grey(ab[3, 3]) and not _is_grey(ab[1, 1]) and not _is_grey(ab[5, 5])


def test_diff_image_colours_padding(cd):
    a, va = cd.pad_to(solid(GREY, (10, 8)), (12, 8))
    b, vb = cd.pad_to(solid(GREY, (12, 8)), (12, 8))
    _, mag = cd.metrics(a, b, va, vb)
    out = cd.diff_image(a, b, mag, valid_b=vb)
    assert all(not _is_grey(out[y, x]) for y in range(8) for x in (10, 11))
    assert _is_grey(out[4, 4])


# ---------------------------------------------------------------------------
# load_rgba
# ---------------------------------------------------------------------------

def test_load_rgba_rgb_png_gets_opaque_alpha(cd, tmp_path):
    p = write_png(tmp_path / "rgb.png", noisy(4)[..., :3])
    a = cd.load_rgba(p)
    assert a.shape == (24, 32, 4) and a.dtype == np.uint8 and (a[..., 3] == 255).all()


def test_load_rgba_16_bit_is_scaled_not_clipped(cd, tmp_path):
    levels = np.array([0, 1, 64, 128, 200, 255], np.uint16)
    arr = np.tile((levels * 257)[None, :], (4, 1))    # 16-bit values of those 8-bit levels
    p = write_png(tmp_path / "grey16.png", arr)
    with Image.open(p) as im:
        assert im.mode.startswith("I")
    a = cd.load_rgba(p)
    assert a.shape == (4, 6, 4) and a.dtype == np.uint8
    assert list(a[0, :, 0]) == list(levels)
    assert np.array_equal(a[..., 0], a[..., 1]) and np.array_equal(a[..., 0], a[..., 2])
    assert (a[..., 3] == 255).all()


def test_load_rgba_dark_16_bit_image_is_scaled_too(cd, tmp_path):
    arr = np.array([[0, 100, 200, 255]], np.uint16)    # all nearly black in 16-bit terms
    p = write_png(tmp_path / "dark16.png", arr)
    a = cd.load_rgba(p)
    assert list(a[0, :, 0]) == [0, 0, 0, 0]


def test_load_rgba_fine_keeps_16_bit_levels_and_skips_8_bit(cd, tmp_path):
    p16 = write_png(tmp_path / "g16.png", np.array([[0, 100, 65535]], np.uint16))
    fine = cd.load_rgba_fine(p16)
    assert fine.dtype == np.float32 and fine.shape == (1, 3, 4)
    assert fine[0, 1, 0] == pytest.approx(100 / 257) and fine[0, 2, 0] == pytest.approx(255)
    assert cd.load_rgba_fine(write_png(tmp_path / "rgb.png", solid()[..., :3])) is None


def test_compare_pair_measures_16_bit_pairs_on_every_level(cd, tmp_path):
    base = np.full((40, 50), 30000, np.uint16)
    cand = base.copy()
    cand[:, :25] += 100                   # +100 / 65535: well under one 8-bit level
    r = cd.compare_pair({"key": "g.png", "baseline": str(write_png(tmp_path / "a" / "g.png", base)),
                         "candidate": str(write_png(tmp_path / "b" / "g.png", cand))})
    assert r["measured_depth"] == "full" and r["status"] == "changed"
    assert r["changed_fraction"] == pytest.approx(0.5)
    assert r["bbox"] == [0, 0, 25, 40]
    assert r["max_abs"] == pytest.approx(100 / 65535, abs=1e-5)


def test_write_frames_draw_nothing_over_the_images(cd, tmp_path):
    a = solid(GREY, (120, 80))
    b = a.copy()
    b[:10, :10] = WHITE                    # change in the top-left corner, where labels used to sit
    r = cd.compare_pair({"key": "icon.png", "baseline": str(write_png(tmp_path / "a" / "i.png", a)),
                         "candidate": str(write_png(tmp_path / "b" / "i.png", b))})
    cd.write_frames([r], tmp_path / "out")
    band = cd.sp.band_height((120, 80))
    for role, src in (("baseline", a), ("candidate", b)):
        frame = np.asarray(Image.open(r["frames"][role]).convert("RGB"))
        assert frame.shape == (80 + band, 120, 3)
        assert np.array_equal(frame[band:], src[..., :3])
    diff = np.asarray(Image.open(r["frames"]["diff"]).convert("RGB"))[band:]
    assert (diff[:10, :10] != diff[20, 20]).any(axis=2).all()   # the change shows, unlabelled


def test_compare_pair_mixed_depth_is_measured_at_8_bits(cd, tmp_path):
    base = np.full((10, 10), 257 * 128, np.uint16)
    cand = solid((128, 128, 128, 255), (10, 10))
    r = cd.compare_pair({"key": "g.png", "baseline": str(write_png(tmp_path / "a" / "g.png", base)),
                         "candidate": str(write_png(tmp_path / "b" / "g.png", cand))})
    assert r["measured_depth"] == "8-bit" and r["status"] == "identical"


# ---------------------------------------------------------------------------
# sort_results
# ---------------------------------------------------------------------------

def test_sort_results_groups_and_ranks(cd):
    results = [
        {"key": "same", "status": "identical", "mean_abs": 0.0, "changed_fraction": 0.0},
        {"key": "new", "status": "added"},
        {"key": "tol", "status": "within_tolerance", "mean_abs": 0.001, "changed_fraction": 0.0001},
        {"key": "small", "status": "changed", "mean_abs": 0.01, "changed_fraction": 0.9},
        {"key": "old", "status": "removed"},
        {"key": "big", "status": "changed", "mean_abs": 0.3, "changed_fraction": 0.1},
        {"key": "tie-b", "status": "changed", "mean_abs": 0.05, "changed_fraction": 0.2},
        {"key": "tie-a", "status": "changed", "mean_abs": 0.05, "changed_fraction": 0.2},
        {"key": "tie-wide", "status": "changed", "mean_abs": 0.05, "changed_fraction": 0.5},
    ]
    order = [r["key"] for r in cd.sort_results(results)]
    assert order == ["big", "tie-wide", "tie-a", "tie-b", "small", "new", "old", "tol", "same"]
    by_name = [r["key"] for r in cd.sort_results(results, order="name")]
    assert by_name == sorted(by_name, key=cd.natural_key)


# ---------------------------------------------------------------------------
# compare_pair
# ---------------------------------------------------------------------------

def _pair(key, b, c):
    return {"key": key, "baseline": str(b) if b else None, "candidate": str(c) if c else None}


def test_compare_pair_statuses(cd, tmp_path):
    base = write_png(tmp_path / "a.png", noisy(5))
    same = write_png(tmp_path / "same.png", noisy(5))
    changed_arr = noisy(5)
    changed_arr[0, 0, :3] = 255 - changed_arr[0, 0, :3]
    changed = write_png(tmp_path / "changed.png", changed_arr)
    assert cd.compare_pair(_pair("s", base, same))["status"] == "identical"
    r = cd.compare_pair(_pair("c", base, changed))
    assert r["status"] == "changed" and r["changed_pixels"] == 1 and r["size_mismatch"] is False
    assert r["size_baseline"] == r["size_candidate"] == [32, 24]
    assert cd.compare_pair(_pair("m", base, changed), min_changed=0.01)["status"] == "within_tolerance"
    assert cd.compare_pair(_pair("n", None, changed))["status"] == "added"
    assert cd.compare_pair(_pair("o", base, None))["status"] == "removed"


def test_compare_pair_identical_pixels_with_different_bytes(cd, tmp_path):
    a = solid((10, 20, 30, 0), (16, 16))
    b = solid((200, 100, 0, 0), (16, 16))           # other colour, but fully transparent
    pa, pb = write_png(tmp_path / "a.png", a), write_png(tmp_path / "b.png", b)
    assert pa.read_bytes() != pb.read_bytes()
    assert cd.compare_pair(_pair("t", pa, pb))["status"] == "identical"


def test_compare_pair_threshold_makes_small_change_within_tolerance(cd, tmp_path):
    a = solid(GREY, (16, 16))
    b = a.copy()
    b[3, 3, :3] += 2
    pa, pb = write_png(tmp_path / "a.png", a), write_png(tmp_path / "b.png", b)
    assert cd.compare_pair(_pair("x", pa, pb))["status"] == "changed"
    assert cd.compare_pair(_pair("x", pa, pb), threshold=2)["status"] == "within_tolerance"


def test_compare_pair_size_mismatch_and_resize(cd, tmp_path):
    pa = write_solid(tmp_path / "a.png", GREY, (16, 12))
    pb = write_solid(tmp_path / "b.png", GREY, (32, 24))
    r = cd.compare_pair(_pair("k", pa, pb))
    assert r["status"] == "changed" and r["size_mismatch"] is True
    assert r["size_baseline"] == [16, 12] and r["size_candidate"] == [32, 24]
    assert r["_arrays"][0].shape[:2] == r["_arrays"][1].shape[:2] == (24, 32)
    r = cd.compare_pair(_pair("k", pa, pb), resize="candidate")
    assert r["resized"] == "candidate" and r["size_mismatch"] is False
    assert r["status"] == "identical"
    r = cd.compare_pair(_pair("k", pa, pb), resize="baseline")
    assert r["resized"] == "baseline" and r["_arrays"][0].shape[:2] == (24, 32)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key, expected", [
    ("home.png", "home"), ("a/b/card-1.PNG", "a/b/card-1"), ("x.jpeg", "x"),
    ("icon.svg", "icon"), ("shot.v2", "shot.v2"),
])
def test_strip_ext(cd, key, expected):
    assert cd.strip_ext(key) == expected


@pytest.mark.parametrize("text, expected", [
    ("screens/home", "screens-home"), ("a b__c", "a-b-c"), ("///", "pair"), ("card-1", "card-1"),
])
def test_safe_name(cd, text, expected):
    assert cd.safe_name(text) == expected


def test_safe_name_keeps_the_end_of_long_names(cd):
    assert cd.safe_name("x" * 100 + "tail", limit=10) == "xxxxxxtail"


# ---------------------------------------------------------------------------
# end to end: two folders
# ---------------------------------------------------------------------------

def _load_compare():
    spec = importlib.util.spec_from_file_location("compare_dirs", COMPARE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def e2e(tmp_path_factory):
    """One default run over a folder pair holding every status; shared by the tests below."""
    root = tmp_path_factory.mktemp("e2e")
    base, cand, out = root / "baseline", root / "candidate", root / "out"
    write_png(base / "same.png", noisy(10))
    write_png(cand / "same.png", noisy(10))
    moved = solid(WHITE)
    moved[4:12, 4:12] = (0, 0, 0, 255)
    write_png(base / "shift.png", moved)
    write_png(cand / "shift.png", np.roll(moved, 6, axis=1))
    tiny = solid(GREY)
    write_png(base / "tiny.png", tiny)
    tiny2 = tiny.copy()
    tiny2[2, 2, 0] += 1
    write_png(cand / "tiny.png", tiny2)
    write_solid(base / "size.png", GREY, (32, 24))
    write_solid(cand / "size.png", GREY, (40, 24))
    write_solid(base / "old.png")
    write_solid(cand / "new.png")
    cd = _load_compare()
    code = cd.main([str(base), str(cand), "--out", str(out)])
    return {"cd": cd, "code": code, "out": out, "base": base, "cand": cand}


def test_run_exit_code_1_when_changes(e2e):
    assert e2e["code"] == 1


def test_run_report_json(e2e):
    rep = read_report(e2e["out"])
    assert rep["schema"] == "rv-review.compare" and rep["schema_version"] == 1
    assert rep["adapter"] == "dirs" and rep["labels"] == ["baseline", "candidate"]
    assert rep["counts"] == {"changed": 3, "added": 1, "removed": 1,
                             "within_tolerance": 0, "identical": 1}
    status = {p["key"]: p["status"] for p in rep["pairs"]}
    assert status == {"same.png": "identical", "shift.png": "changed", "tiny.png": "changed",
                      "size.png": "changed", "old.png": "removed", "new.png": "added"}
    pairs = {p["key"]: p for p in rep["pairs"]}
    assert pairs["size.png"]["size_mismatch"] is True
    assert pairs["shift.png"]["size_mismatch"] is False
    assert pairs["size.png"]["size_baseline"] == [32, 24]
    assert pairs["size.png"]["size_candidate"] == [40, 24]
    # the report lists changed pairs most changed first
    assert [p["key"] for p in rep["pairs"]][-1] == "same.png"
    assert [p["key"] for p in rep["pairs"] if p["status"] == "changed"][-1] == "tiny.png"
    assert "frames" not in pairs["same.png"]
    assert set(pairs["shift.png"]["frames"]) == {"baseline", "candidate", "diff"}
    assert all(not k.startswith("_") for p in rep["pairs"] for k in p)


def test_run_report_markdown(e2e):
    md = (e2e["out"] / "compare_report.md").read_text(encoding="utf-8")
    assert md.startswith("# Compare report")
    assert "| # | pair | status |" in md
    for key in ("shift.png", "tiny.png", "size.png", "old.png", "new.png", "same.png"):
        assert f"`{key}`" in md
    assert "mismatch" in md


def test_run_frames_json_loads_as_manifest(e2e, rm):
    m = rm.load(e2e["out"] / "frames.json")
    assert m["layout"] == "sequence" and m["marks"] == "groups"
    # most changed first by mean_abs: the 8-column padding outweighs the shifted square
    assert [g["label"] for g in m["groups"]] == ["size.png", "shift.png", "tiny.png",
                                                 "new.png", "old.png"]
    by_group = {}
    for it in m["items"]:
        by_group.setdefault(it["group"], []).append(it)
        assert Path(it["path"]).is_file()
    for g in m["groups"]:
        roles = [it["meta"]["role"] for it in by_group[g["id"]]]
        labels = [it["label"] for it in by_group[g["id"]]]
        if g["label"] in ("new.png", "old.png"):
            assert roles == ["baseline", "candidate"] and labels == ["baseline", "candidate"]
        else:
            assert roles == ["baseline", "candidate", "diff"]
            assert labels[:2] == ["baseline", "candidate"] and labels[2].startswith("diff x8")
        assert all(it["meta"]["key"] == g["label"] for it in by_group[g["id"]])


def test_run_frames_json_legacy_section_matches_groups(e2e, rm):
    data = read_frames_json(e2e["out"])
    assert data["frames"] == [it["path"] for it in data["items"]]
    assert len(data["views"]) == len(data["groups"])
    legacy = rm.from_frames_json({"frames": data["frames"], "views": data["views"]})
    assert [it["path"] for it in legacy["items"]] == data["frames"]
    assert [it["label"] for it in legacy["items"]] == [it["label"] for it in data["items"]]
    starts = [v["frame"] for v in data["views"]]
    first_of_group = []
    for i, it in enumerate(data["items"], 1):
        if not first_of_group or data["items"][i - 2]["group"] != it["group"]:
            first_of_group.append(i)
    assert starts == first_of_group
    assert [v["sheet"] for v in data["views"]] == [g["label"] for g in data["groups"]]


def test_run_frames_of_a_pair_share_one_size(e2e):
    data = read_frames_json(e2e["out"])
    sizes = {}
    for it in data["items"]:
        with Image.open(it["path"]) as im:
            sizes.setdefault(it["group"], set()).add(im.size)
    assert all(len(s) == 1 for s in sizes.values())
    size_gid = next(g["id"] for g in data["groups"] if g["label"] == "size.png")
    (w, _), = sizes[size_gid]
    assert w == 40                                    # padded to the larger side
    assert data["size"] == [max(w for s in sizes.values() for w, _ in s),
                            max(h for s in sizes.values() for _, h in s)]


def test_run_frame_names_carry_running_index(e2e):
    frames = read_frames_json(e2e["out"])["frames"]
    assert len(frames) == 3 * 3 + 2 * 2
    for n, f in enumerate(frames, 1):
        name = Path(f).name
        assert name.endswith(f"__{n}.png")
        assert "__" in name[:-len(f"__{n}.png")]      # <pair>__<role>__<N>.png


def test_run_summary_line(e2e, capsys, tmp_path):
    cd = e2e["cd"]
    code, summary = run_main(cd, capsys, e2e["base"], e2e["cand"], "--out", tmp_path / "o")
    assert code == 1 and summary["ok"] is True and summary["exit_code"] == 1
    assert summary["pairs_in_review"] == 5 and summary["frames"] == 13
    assert summary["most_changed"] == ["size.png", "shift.png", "tiny.png"]
    assert Path(summary["frames_json"]).is_file() and Path(summary["report"]).is_file()


# ---------------------------------------------------------------------------
# end to end: options
# ---------------------------------------------------------------------------

@pytest.fixture()
def two_files(tmp_path):
    a = solid(WHITE, (40, 32))
    b = a.copy()
    b[8:16, 8:16, :3] = 0
    return write_png(tmp_path / "design" / "home.png", a), write_png(tmp_path / "build" / "home.png", b)


def test_two_files_labels_and_overlay(cd, capsys, tmp_path, two_files):
    out = tmp_path / "out"
    code, summary = run_main(cd, capsys, *two_files, "--labels", "design,build", "--overlay",
                             "--out", out)
    assert code == 1 and summary["pairs_in_review"] == 1
    data = read_frames_json(out)
    assert len(data["groups"]) == 1 and data["groups"][0]["label"] == "home"
    labels = [it["label"] for it in data["items"]]
    roles = [it["meta"]["role"] for it in data["items"]]
    assert labels[:2] == ["design", "build"] and labels[3] == "overlay 50%"
    assert roles == ["baseline", "candidate", "diff", "overlay"]
    rep = read_report(out)
    assert rep["labels"] == ["design", "build"] and len(rep["pairs"]) == 1


def test_two_files_with_different_names_get_a_vs_key(cd, capsys, tmp_path):
    a = write_solid(tmp_path / "home.png", WHITE)
    b = write_solid(tmp_path / "home-dark.png", GREY)
    code, _ = run_main(cd, capsys, a, b, "--out", tmp_path / "out")
    assert code == 1
    assert read_report(tmp_path / "out")["pairs"][0]["key"] == "home vs home-dark"


def test_no_diff_drops_diff_frame(cd, capsys, tmp_path, two_files):
    out = tmp_path / "out"
    run_main(cd, capsys, *two_files, "--no-diff", "--out", out)
    assert [it["meta"]["role"] for it in read_frames_json(out)["items"]] == ["baseline", "candidate"]


def test_all_includes_identical_pairs_and_exit_0(cd, capsys, tmp_path):
    base, cand = tmp_path / "b", tmp_path / "c"
    write_png(base / "home.png", noisy(7))
    write_png(cand / "home.png", noisy(7))
    code, summary = run_main(cd, capsys, base, cand, "--out", tmp_path / "o1")
    assert code == 0 and summary["exit_code"] == 0 and summary["frames"] == 0
    assert summary["frames_json"] is None
    assert read_report(tmp_path / "o1")["counts"]["identical"] == 1
    code, summary = run_main(cd, capsys, base, cand, "--all", "--out", tmp_path / "o2")
    assert code == 0 and summary["pairs_in_review"] == 1 and summary["frames"] == 3
    assert [g["label"] for g in read_frames_json(tmp_path / "o2")["groups"]] == ["home.png"]


def test_within_tolerance_exits_0(cd, capsys, tmp_path):
    base, cand = tmp_path / "b", tmp_path / "c"
    a = solid(GREY)
    b = a.copy()
    b[0, 0, :3] += 3
    write_png(base / "home.png", a)
    write_png(cand / "home.png", b)
    code, summary = run_main(cd, capsys, base, cand, "--threshold", "4", "--out", tmp_path / "o")
    assert code == 0 and summary["counts"]["within_tolerance"] == 1


def test_resize_candidate_matches_a_2x_export(cd, capsys, tmp_path):
    base = write_solid(tmp_path / "shot.png", (40, 120, 200, 255), (20, 16))
    cand = write_solid(tmp_path / "design" / "shot.png", (40, 120, 200, 255), (40, 32))
    code, _ = run_main(cd, capsys, base, cand, "--out", tmp_path / "o1")
    assert code == 1 and read_report(tmp_path / "o1")["pairs"][0]["size_mismatch"] is True
    code, _ = run_main(cd, capsys, base, cand, "--resize", "candidate", "--all",
                       "--out", tmp_path / "o2")
    p = read_report(tmp_path / "o2")["pairs"][0]
    assert code == 0 and p["resized"] == "candidate" and p["size_mismatch"] is False
    assert p["status"] == "identical"
    for f in read_frames_json(tmp_path / "o2")["frames"]:
        with Image.open(f) as im:
            assert im.width == 20


def test_skip_added_removed_and_max_pairs(cd, capsys, tmp_path):
    base, cand = tmp_path / "b", tmp_path / "c"
    for n in (1, 2, 3):
        write_solid(base / f"s{n}.png", GREY)
        write_solid(cand / f"s{n}.png", (0, 0, 0, 255) if n == 2 else WHITE)
    write_solid(base / "old.png")
    write_solid(cand / "new.png")
    code, s = run_main(cd, capsys, base, cand, "--skip-added-removed", "--out", tmp_path / "o1")
    assert code == 1 and s["pairs_in_review"] == 3            # added / removed still count
    assert [g["label"] for g in read_frames_json(tmp_path / "o1")["groups"]][0] == "s2.png"
    code, s = run_main(cd, capsys, base, cand, "--max-pairs", "1", "--out", tmp_path / "o2")
    assert s["pairs_in_review"] == 1 and len(read_report(tmp_path / "o2")["pairs"]) == 5
    code, s = run_main(cd, capsys, base, cand, "--order", "name", "--out", tmp_path / "o3")
    assert [g["label"] for g in read_frames_json(tmp_path / "o3")["groups"]] == \
        ["new.png", "old.png", "s1.png", "s2.png", "s3.png"]


@pytest.mark.parametrize("make_args, needle", [
    (lambda d: [d / "missing", d / "b"], "not found"),
    (lambda d: [d / "b", d / "b" / "x.png"], "both be folders or both be files"),
    (lambda d: [d / "b"], "BASELINE and a CANDIDATE"),
    (lambda d: [d / "b", d / "b", "--labels", "one,two,three"], "--labels takes two names"),
    (lambda d: [d / "empty", d / "empty"], "no images found"),
    (lambda d: ["--adapter", "playwright", d / "b", d / "b"], "takes one folder"),
    (lambda d: ["--adapter", "flutter", d / "b" / "x.png"], "not a folder"),
])
def test_errors_exit_2_with_json(cd, capsys, tmp_path, make_args, needle):
    write_solid(tmp_path / "b" / "x.png")
    (tmp_path / "empty").mkdir()
    code, summary = run_main(cd, capsys, *make_args(tmp_path), "--out", tmp_path / "out")
    assert code == 2
    assert summary["ok"] is False and summary["exit_code"] == 2 and needle in summary["error"]


# ---------------------------------------------------------------------------
# CLI, via subprocess (sys.executable only)
# ---------------------------------------------------------------------------

def _cli(*args):
    r = subprocess.run([sys.executable, str(COMPARE_SCRIPT), *[str(a) for a in args]],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, json.loads(r.stdout.strip().splitlines()[-1]), r.stderr


def test_cli_exit_codes(tmp_path):
    base, same, changed = tmp_path / "base", tmp_path / "same", tmp_path / "changed"
    write_solid(base / "home.png", GREY)
    write_solid(same / "home.png", GREY)
    write_solid(changed / "home.png", WHITE)
    code, s, _ = _cli(base, same, "--out", tmp_path / "o0")
    assert code == 0 and s["exit_code"] == 0
    code, s, _ = _cli(base, changed, "--out", tmp_path / "o1")
    assert code == 1 and s["exit_code"] == 1 and s["counts"]["changed"] == 1
    code, s, err = _cli(tmp_path / "nope", changed, "--out", tmp_path / "o2")
    assert code == 2 and s["ok"] is False and err.startswith("compare_dirs:")


# ---------------------------------------------------------------------------
# adapters
# ---------------------------------------------------------------------------

def _playwright_tree(root):
    d = root / "test-results" / "spec-name-chromium"
    write_solid(d / "home-expected.png", GREY)
    write_solid(d / "home-actual.png", WHITE)
    write_solid(d / "home-diff.png", (255, 0, 0, 255))
    write_solid(d / "menu-actual.png", WHITE)        # a new snapshot: no expected yet
    return d


def test_adapter_playwright(cd, tmp_path):
    d = _playwright_tree(tmp_path)
    pairs = cd.adapter_playwright(tmp_path)
    assert [p["key"] for p in pairs] == ["test-results/spec-name-chromium/home",
                                         "test-results/spec-name-chromium/menu"]
    home, menu = pairs
    assert home["baseline"] == str(d / "home-expected.png")
    assert home["candidate"] == str(d / "home-actual.png")
    assert home["tool_diff"] == str(d / "home-diff.png")
    assert menu["baseline"] is None and menu["tool_diff"] is None
    assert cd.compare_pair(menu)["status"] == "added"


def test_adapter_playwright_end_to_end_with_tool_diff_and_include(cd, capsys, tmp_path):
    _playwright_tree(tmp_path / "proj")
    out = tmp_path / "out"
    code, s = run_main(cd, capsys, "--adapter", "playwright", tmp_path / "proj", "--tool-diff",
                       "--out", out)
    assert code == 1 and s["counts"]["changed"] == 1 and s["counts"]["added"] == 1
    items = read_frames_json(out)["items"]
    assert [it["meta"]["role"] for it in items if it["meta"]["key"].endswith("home")] == \
        ["baseline", "candidate", "diff", "tool_diff"]
    code, s = run_main(cd, capsys, "--adapter", "playwright", tmp_path / "proj",
                       "--include", "*/home", "--out", tmp_path / "out2")
    assert s["pairs_in_review"] == 1 and s["counts"]["added"] == 0


def _composite(base, received, vertical=False):
    diff = np.zeros_like(base)
    diff[..., 0], diff[..., 3] = 255, 255
    return np.concatenate([base, diff, received], axis=0 if vertical else 1)


def test_adapter_jest_received_composite_and_passing(cd, tmp_path):
    snaps = tmp_path / "src" / "__image_snapshots__"
    base1, base2 = solid(GREY, (20, 16)), noisy(8, (20, 16))
    write_png(snaps / "card-1-snap.png", base1)
    write_png(snaps / "__received_output__" / "card-1-snap-received.png", solid(WHITE, (20, 16)))
    write_png(snaps / "card-2-snap.png", base2)
    received2 = noisy(9, (20, 16))
    write_png(snaps / "__diff_output__" / "card-2-snap-diff.png", _composite(base2, received2))
    write_png(snaps / "card-3-snap.png", base1)                  # passing: no output at all
    pairs = cd.adapter_jest(tmp_path, tmp_path / "work")
    assert [p["key"] for p in pairs] == ["src/card-1-snap", "src/card-2-snap"]
    one, two = pairs
    assert one["candidate"].endswith("card-1-snap-received.png") and one["tool_diff"] is None
    assert two["tool_diff"].endswith("card-2-snap-diff.png") and two["note"] is None
    assert Path(two["candidate"]).parent == tmp_path / "work" / "_extracted"
    assert np.array_equal(cd.load_rgba(two["candidate"]), received2)


def test_split_composite_vertical_and_wrong_size(cd, tmp_path):
    base, rec = noisy(11, (12, 10)), noisy(12, (12, 10))
    v = write_png(tmp_path / "v-diff.png", _composite(base, rec, vertical=True))
    out = cd.split_composite(v, (12, 10), tmp_path / "x")
    assert np.array_equal(cd.load_rgba(out), rec)
    assert cd.split_composite(v, (13, 10), tmp_path / "x") is None


def test_adapter_jest_unsplittable_composite_gets_a_note(cd, tmp_path):
    snaps = tmp_path / "__image_snapshots__"
    write_png(snaps / "card-4-snap.png", solid(GREY, (20, 16)))
    write_png(snaps / "__diff_output__" / "card-4-snap-diff.png", solid(GREY, (50, 16)))
    (p,) = cd.adapter_jest(tmp_path, tmp_path / "work")
    assert p["key"] == "card-4-snap" and p["candidate"] is None
    assert "storeReceivedOnFailure" in p["note"]


def test_adapter_jest_same_id_in_two_folders_stays_apart(cd, tmp_path):
    for name in ("pkg-a", "pkg-b"):
        write_solid(tmp_path / name / "__image_snapshots__" / "index-test-js-1-snap.png")
    write_solid(tmp_path / "pkg-b" / "__image_snapshots__" / "__received_output__"
                / "index-test-js-1-snap-received.png", WHITE)
    pairs = cd.adapter_jest(tmp_path, tmp_path / "work")
    assert [p["key"] for p in pairs] == ["pkg-b/index-test-js-1-snap"]


def test_adapter_jest_end_to_end(cd, capsys, tmp_path):
    snaps = tmp_path / "proj" / "__image_snapshots__"
    base = solid(GREY, (20, 16))
    write_png(snaps / "card-2-snap.png", base)
    write_png(snaps / "__diff_output__" / "card-2-snap-diff.png",
              _composite(base, solid(WHITE, (20, 16))))
    out = tmp_path / "out"
    code, s = run_main(cd, capsys, "--adapter", "jest", tmp_path / "proj", "--out", out)
    assert code == 1 and s["counts"]["changed"] == 1
    assert (out / "_extracted" / "card-2-snap-diff-received-from-composite.png").is_file()


UNITY_SUB = ("Linear", "WindowsEditor", "Direct3D11", "None")


def test_adapter_unity(cd, tmp_path):
    assets = tmp_path / "Assets"
    ref = assets / "ReferenceImages" / Path(*UNITY_SUB)
    act = assets / "ActualImages" / Path(*UNITY_SUB)
    write_solid(ref / "Scene1.png", GREY)
    write_solid(act / "Scene1.png", WHITE)
    write_solid(act / "Scene1.diff.png", (255, 0, 0, 255))      # the tool diff, not a pair
    # Scene2 ran on another API: no mirrored reference, found by file name instead; the
    # reference sharing more trailing folders wins over a decoy
    write_solid(ref / "Scene2.png", GREY)
    write_solid(assets / "ReferenceImages" / "Gamma" / "OSX" / "Metal" / "Other" / "Scene2.png")
    write_solid(assets / "ActualImages" / "Linear" / "WindowsEditor" / "Vulkan" / "None"
                / "Scene2.png", WHITE)
    write_solid(act / "Scene3.png")                              # no reference anywhere
    pairs = {p["key"]: p for p in cd.adapter_unity(tmp_path)}
    assert set(pairs) == {"Linear/WindowsEditor/Direct3D11/None/Scene1",
                          "Linear/WindowsEditor/Vulkan/None/Scene2",
                          "Linear/WindowsEditor/Direct3D11/None/Scene3"}
    s1 = pairs["Linear/WindowsEditor/Direct3D11/None/Scene1"]
    assert s1["baseline"] == str(ref / "Scene1.png") and s1["candidate"] == str(act / "Scene1.png")
    assert s1["tool_diff"] == str(act / "Scene1.diff.png")
    assert pairs["Linear/WindowsEditor/Vulkan/None/Scene2"]["baseline"] == str(ref / "Scene2.png")
    assert pairs["Linear/WindowsEditor/Direct3D11/None/Scene3"]["baseline"] is None
    # the Assets folder itself works as ROOT too
    assert len(cd.adapter_unity(assets)) == 3


def test_adapter_unity_without_actual_images_is_an_error(cd, capsys, tmp_path):
    (tmp_path / "Assets" / "ReferenceImages").mkdir(parents=True)
    with pytest.raises(cd.CompareError, match="ActualImages"):
        cd.adapter_unity(tmp_path)
    code, s = run_main(cd, capsys, "--adapter", "unity", tmp_path, "--out", tmp_path / "out")
    assert code == 2 and "ActualImages" in s["error"]


def test_adapter_unreal(cd, capsys, tmp_path):
    rep = tmp_path / "report"
    write_solid(rep / "a" / "approved.png", GREY)
    write_solid(rep / "a" / "incoming.png", WHITE)
    write_solid(rep / "a" / "delta.png", (255, 0, 0, 255))
    data = {"comparisons": [
        {"name": "Main.Lobby.Screenshot",
         "files": {"approved": "a/approved.png", "unapproved": "a/incoming.png",
                   "difference": "a/delta.png"}},
        {"name": "Main.Menu.Screenshot",
         "files": {"approved": "a/approved.png", "unapproved": "a/missing.png"}},
    ]}
    (rep / "index.json").write_text(json.dumps(data), encoding="utf-8")
    pairs = {p["key"]: p for p in cd.adapter_unreal(tmp_path)}
    assert set(pairs) == {"Main.Lobby.Screenshot", "Main.Menu.Screenshot"}
    lobby = pairs["Main.Lobby.Screenshot"]
    assert lobby["baseline"] == str(rep / "a" / "approved.png")
    assert lobby["candidate"] == str(rep / "a" / "incoming.png")
    assert lobby["tool_diff"] == str(rep / "a" / "delta.png") and lobby["note"] is None
    menu = pairs["Main.Menu.Screenshot"]
    assert menu["candidate"] is None and "not on disk" in menu["note"]
    code, s = run_main(cd, capsys, "--adapter", "unreal", tmp_path, "--out", tmp_path / "out")
    assert code == 1 and s["counts"]["changed"] == 1 and s["counts"]["removed"] == 1


def test_adapter_unreal_without_report_is_an_error(cd, tmp_path):
    write_solid(tmp_path / "shot.png")
    with pytest.raises(cd.CompareError, match="ReportExportPath"):
        cd.adapter_unreal(tmp_path)


def test_adapter_flutter(cd, capsys, tmp_path):
    fail = tmp_path / "test" / "failures"
    write_solid(fail / "button_masterImage.png", GREY)
    write_solid(fail / "button_testImage.png", WHITE)
    write_solid(fail / "button_isolatedDiff.png", (255, 0, 0, 255))
    write_solid(fail / "button_maskedDiff.png", (255, 0, 0, 255))
    write_solid(tmp_path / "test" / "goldens" / "other_masterImage.png")   # not in failures/
    (p,) = cd.adapter_flutter(tmp_path)
    assert p["key"] == "test/button"
    assert p["baseline"] == str(fail / "button_masterImage.png")
    assert p["candidate"] == str(fail / "button_testImage.png")
    assert p["tool_diff"] == str(fail / "button_isolatedDiff.png")
    code, s = run_main(cd, capsys, "--adapter", "flutter", tmp_path, "--out", tmp_path / "out")
    assert code == 1 and s["counts"]["changed"] == 1


def test_adapter_flutter_accepts_the_failures_folder_as_root(cd, tmp_path):
    fail = tmp_path / "failures"
    write_solid(fail / "button_masterImage.png", GREY)
    write_solid(fail / "button_testImage.png", WHITE)
    (p,) = cd.adapter_flutter(fail)
    assert p["key"].endswith("button")


# ---------------------------------------------------------------------------
# SVG inputs (a stand-in rasterize module; no real backend is needed)
# ---------------------------------------------------------------------------

SVG = ("<svg xmlns='http://www.w3.org/2000/svg' width='24' height='20'>"
       "<rect width='24' height='20' fill='#808080'/></svg>")


def _fake_rasterize(color, calls):
    def rasterize(svg, out, size=None, scale=1.0, background="transparent", backend=None):
        calls.append({"svg": str(svg), "size": size})
        w, h = size or (24, 20)
        write_solid(out, color, (w, h))
        return {"out": str(Path(out).resolve()), "backend": "fake", "size": [w, h]}
    return types.SimpleNamespace(rasterize=rasterize, svg_size=lambda p: (24, 20),
                                 RasterizeError=RuntimeError)


def test_svg_design_against_png_build(cd, capsys, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "rasterize", _fake_rasterize(GREY, calls))
    design, build = tmp_path / "design", tmp_path / "build"
    design.mkdir()
    (design / "home.svg").write_text(SVG, encoding="utf-8")
    write_solid(build / "home.png", GREY, (30, 18))
    code, s = run_main(cd, capsys, design, build, "--match", "stem", "--all",
                       "--out", tmp_path / "out")
    assert code == 0 and s["counts"]["identical"] == 1
    assert calls == [{"svg": str(design / "home.svg"), "size": (30, 18)}]  # the bitmap's size
    p = read_report(tmp_path / "out")["pairs"][0]
    assert p["key"] == "home" and p["rasterized"] == {"baseline": "fake"}


def test_svg_changed_both_sides(cd, capsys, tmp_path, monkeypatch):
    colors = iter([GREY, WHITE])
    calls = []

    def rasterize(svg, out, size=None, **kw):
        calls.append(size)
        write_solid(out, next(colors), size)
        return {"out": str(out), "backend": "fake", "size": list(size)}
    monkeypatch.setitem(sys.modules, "rasterize", types.SimpleNamespace(
        rasterize=rasterize, svg_size=lambda p: (24, 20)))
    a, b = tmp_path / "a" / "icon.svg", tmp_path / "b" / "icon.svg"
    for p in (a, b):
        p.parent.mkdir()
        p.write_text(SVG, encoding="utf-8")
    code, s = run_main(cd, capsys, a, b, "--svg-scale", "2", "--out", tmp_path / "out")
    assert code == 1 and s["counts"]["changed"] == 1
    assert calls == [(48, 40), (48, 40)]


def test_svg_without_any_rasteriser_exits_2(cd, rz, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(rz, "detect", lambda *a, **k: {b: None for b in rz.BACKENDS})
    monkeypatch.setitem(sys.modules, "rasterize", rz)
    a, b = tmp_path / "a" / "icon.svg", tmp_path / "b" / "icon.svg"
    for p in (a, b):
        p.parent.mkdir()
        p.write_text(SVG, encoding="utf-8")
    code, s = run_main(cd, capsys, a, b, "--out", tmp_path / "out")
    assert code == 2 and s["ok"] is False and s["exit_code"] == 2
    assert "rasteriser" in s["error"].lower() or "rasterizer" in s["error"].lower()
