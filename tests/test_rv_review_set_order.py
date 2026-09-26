"""Tests for the review order in rv-review/scripts/review_set.py: known variant pairs
(before / after ...), breakpoints by width, and screens named <page>__<breakpoint>.

Only pure functions and build() on synthetic PNGs in tmp_path; nothing opens a window.
"""
import json
from pathlib import Path

import pytest
from PIL import Image


def _png(path, size=(40, 30), color=(200, 50, 50, 255)):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)
    return path


@pytest.mark.parametrize("names, expected", [
    (["after", "before"], ["before", "after"]),
    (["candidate", "baseline"], ["baseline", "candidate"]),
    (["New", "old"], ["old", "New"]),
    (["actual", "expected"], ["expected", "actual"]),
    (["v10", "v2", "v1"], ["v1", "v2", "v10"]),
    (["desktop", "mobile", "tablet"], ["mobile", "tablet", "desktop"]),
    (["xl", "sm", "md", "lg"], ["sm", "md", "lg", "xl"]),
    (["w1440", "w375", "w768"], ["w375", "w768", "w1440"]),
    (["light", "dark"], ["dark", "light"]),                 # no known order: natural
    (["after", "before", "draft"], ["after", "before", "draft"]),  # not all in one set
])
def test_order_names(rset, names, expected):
    assert rset.order_names(names) == expected


@pytest.mark.parametrize("name, width", [
    ("mobile", 375), ("Tablet", 768), ("desktop", 1440), ("2xl", 1536), ("w375", 375),
    ("1440", 1440), ("768x1024", 768), ("bp-1280", 1280),
    ("v2", None), ("t01", None), ("ios-light-l", None), ("home", None),
])
def test_breakpoint_width(rset, name, width):
    assert rset.breakpoint_width(name) == width


def test_screens_sort_breakpoints_by_width_within_each_page(rset):
    screens = ["index__desktop.png", "index__mobile.png", "index__tablet.png",
               "about__mobile__t02.png", "about__mobile__t01.png", "about__desktop.png",
               "home.png", "checkout__w1440.png", "checkout__w375.png", "settings__dark.png",
               "settings__light.png"]
    assert sorted(screens, key=rset.screen_key) == [
        "about__mobile__t01.png", "about__mobile__t02.png", "about__desktop.png",
        "checkout__w375.png", "checkout__w1440.png", "home.png",
        "index__mobile.png", "index__tablet.png", "index__desktop.png",
        "settings__dark.png", "settings__light.png"]


def test_root_with_before_after_puts_before_first(tmp_path, rset):
    for v in ("after", "before"):
        _png(tmp_path / "caps" / v / "index__mobile.png")
    out = rset.variants_from([tmp_path / "caps"])
    assert [n for n, _ in out] == ["before", "after"]


def test_order_option_still_wins(tmp_path, rset):
    for v in ("after", "before"):
        (tmp_path / v).mkdir()
    out = rset.variants_from([tmp_path], order="after,before")
    assert [n for n, _ in out] == ["after", "before"]


def test_explicit_folders_keep_the_given_order(tmp_path, rset):
    a, b = tmp_path / "after", tmp_path / "before"
    a.mkdir()
    b.mkdir()
    assert [n for n, _ in rset.variants_from([a, b])] == ["after", "before"]


def test_build_orders_web_breakpoint_screens_by_width(tmp_path, rset):
    for v in ("before", "after"):
        for bp, w in (("desktop", 144), ("mobile", 38), ("tablet", 77)):
            _png(tmp_path / "caps" / v / f"index__{bp}.png", (w, 60))
    res = rset.build(rset.variants_from([tmp_path / "caps"]), tmp_path / "review")
    assert res["variants"] == ["before", "after"]
    data = json.loads(Path(res["frames_json"]).read_text(encoding="utf-8"))
    assert [g["label"] for g in data["groups"]] == [
        "index__mobile.png", "index__tablet.png", "index__desktop.png"]
    assert [it["label"] for it in data["items"][:2]] == ["before", "after"]
