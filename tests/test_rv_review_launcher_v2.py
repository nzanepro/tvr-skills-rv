"""Tests for the log, review-property and notes parts of rv-review/scripts/rv_review.py.

Covers what tests/test_rv_review.py does not: parse_log, own_log_path, app_log_path,
LogWatch, items_from_tokens, item_ranges, item_at_frame, review_props_commands,
annotation_commands, items_from_rv, _paint_target, build_notes, read_notes, envelope, the
"difference-inverted" compare mode of post_commands, and the JSON error line of the CLI.

Nothing here launches RV, rvpush or rvio. The py-exec strings the script would send to RV
are compiled, and run against a small fake "rv" module that only records properties.
read_notes() runs with read_state() and _eval() monkeypatched to return canned RV answers,
and LogWatch() reads temp files with own_log_path() / app_log_path() monkeypatched. The CLI
tests only use argument errors that stop before RV is looked up.
"""
import json
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RV_REVIEW_SCRIPT = REPO_ROOT / "rv-review" / "scripts" / "rv_review.py"

WIN, MAC, LINUX = "win32", "darwin", "linux"


class _FakeCommands:
    """Just enough of rv.commands to run the property-setting py-exec strings."""
    StringType, IntType, FloatType = "string", "int", "float"

    def __init__(self):
        self.props = {}      # path -> (type, width, values)

    def propertyExists(self, p):
        return p in self.props

    def newProperty(self, p, typ, width):
        assert p not in self.props, f"property {p} created twice"
        self.props[p] = (typ, width, [])

    def _set(self, typ, p, values, allow_resize):
        assert p in self.props, f"property {p} set before it was created"
        assert self.props[p][0] == typ, f"property {p} set with the wrong type"
        assert allow_resize is True
        self.props[p] = (typ, self.props[p][1], list(values))

    def setStringProperty(self, p, v, r):
        self._set("string", p, v, r)

    def setIntProperty(self, p, v, r):
        self._set("int", p, v, r)

    def setFloatProperty(self, p, v, r):
        self._set("float", p, v, r)


def _run_in_fake_rv(cmds):
    """Compile and run py-exec strings against a fake rv; returns the recorded properties."""
    fake = _FakeCommands()
    rv = types.SimpleNamespace(commands=fake)
    for i, cmd in enumerate(cmds):
        code = compile(cmd, f"<cmd {i}>", "exec")
        exec(code, {"rv": rv})
    return fake.props


def _state(frame_start=1, frame_end=30, source_starts=(1, 11, 21), view_type="RVSequenceGroup",
           marks=()):
    """A read-back state as parse_state() returns it (the fields the code under test reads)."""
    return {"frame": frame_start, "frameStart": frame_start, "frameEnd": frame_end,
            "marks": list(marks), "sourceStarts": list(source_starts),
            "sources": len(source_starts), "viewNode": "defaultSequence",
            "viewNodeType": view_type, "stereo": "off", "fps": 24.0,
            "frames": frame_end - frame_start + 1}


def _src(n):
    return f"sourceGroup{n:06d}_source"


def _review_vals(label="", title="", group="", view="", meta=""):
    """The per-source review values as SOURCE_REVIEW_EXPR returns them (string lists)."""
    return [[label] if label else [], [title] if title else [], [group] if group else [],
            [view] if view else [], [meta] if meta else []]


# ---------------------------------------------------------------------------
# parse_log
# ---------------------------------------------------------------------------

SAMPLE_LOG = """\
INFO: loading media
ERROR: cannot open file a.exr
WARNING: missing frame 12
[2026-01-01 10:00:00.000] [OpenRV] [error] decoder failed
[2026-01-01 10:00:00.001] [OpenRV] [warning] colour space guessed
[2026-01-01 10:00:00.002] [OpenRV] [info] session loaded
plain stdout noise, not a log line
ERROR: cannot open file a.exr
[2026-01-01 10:00:01.000] [OpenRV] [error] decoder failed
CRITICAL: out of memory
WARN: slow disk
ERROR:
  WARNING: indented warning
"""


def test_parse_log_classifies_and_deduplicates(rr):
    errors, warnings = rr.parse_log(SAMPLE_LOG)
    assert errors == ["cannot open file a.exr", "decoder failed", "out of memory"]
    assert warnings == ["missing frame 12", "colour space guessed", "slow disk",
                        "indented warning"]


def test_parse_log_drops_info_and_noise(rr):
    errors, warnings = rr.parse_log(SAMPLE_LOG)
    everything = errors + warnings
    assert not any("loading media" in m or "session loaded" in m or "noise" in m
                   for m in everything)


@pytest.mark.parametrize("text", ["", None, "INFO: fine\nDEBUG: detail\n",
                                  "[2026-01-01 10:00:00.000] [OpenRV] [info] ok"])
def test_parse_log_nothing_to_report(rr, text):
    assert rr.parse_log(text) == ([], [])


# ---------------------------------------------------------------------------
# own_log_path / app_log_path
# ---------------------------------------------------------------------------

def test_own_log_path_sanitises_tag_into_temp_dir(rr):
    p = rr.own_log_path("my tag/x")
    assert p.parent == Path(tempfile.gettempdir())
    assert p.name == "rv-review-my_tag_x.log"


def test_own_log_path_default_tag(rr):
    assert rr.own_log_path(rr.DEFAULT_TAG).name == "rv-review-rv-review.log"


def test_app_log_path_windows_uses_roaming_appdata(tmp_path, rr):
    got = rr.app_log_path(platform=WIN, roaming=tmp_path / "roaming")
    assert got == tmp_path / "roaming" / "ASWF" / "OpenRV" / "OpenRV.log"


def test_app_log_path_windows_default_is_under_the_home_folder(tmp_path, rr):
    got = rr.app_log_path(platform=WIN, home=tmp_path / "home")
    assert got == tmp_path / "home" / "AppData" / "Roaming" / "ASWF" / "OpenRV" / "OpenRV.log"


def test_app_log_path_macos(tmp_path, rr):
    home = tmp_path / "home"
    got = rr.app_log_path(platform=MAC, home=home)
    assert got == home / "Library" / "Logs" / "ASWF" / "OpenRV.log"


def test_app_log_path_linux(tmp_path, rr):
    home = tmp_path / "home"
    got = rr.app_log_path(platform=LINUX, home=home)
    assert got == home / ".local" / "share" / "ASWF" / "OpenRV" / "OpenRV.log"


# ---------------------------------------------------------------------------
# LogWatch
# ---------------------------------------------------------------------------

def test_logwatch_returns_only_text_appended_after_construction(tmp_path, monkeypatch, rr):
    log = tmp_path / "own.log"
    log.write_text("ERROR: from an earlier load\n", encoding="utf-8")
    monkeypatch.setattr(rr, "own_log_path", lambda tag: log)
    watch = rr.LogWatch("anything")
    assert watch.sources() == [str(log)]
    with open(log, "a", encoding="utf-8") as f:
        f.write("WARNING: new warning\n")
    text = watch.new_text()
    assert "new warning" in text
    assert "earlier load" not in text
    assert rr.parse_log(text) == ([], ["new warning"])


def test_logwatch_falls_back_to_app_log_when_own_log_missing(tmp_path, monkeypatch, rr):
    app = tmp_path / "OpenRV.log"
    app.write_text("old line\n", encoding="utf-8")
    monkeypatch.setattr(rr, "own_log_path", lambda tag: tmp_path / "missing.log")
    monkeypatch.setattr(rr, "app_log_path", lambda *a, **k: app)
    watch = rr.LogWatch("anything")
    assert watch.sources() == [str(app)]
    with open(app, "a", encoding="utf-8") as f:
        f.write("ERROR: appended\n")
    assert rr.parse_log(watch.new_text())[0] == ["appended"]


def test_logwatch_no_log_at_all_is_empty(tmp_path, monkeypatch, rr):
    monkeypatch.setattr(rr, "own_log_path", lambda tag: tmp_path / "missing.log")
    monkeypatch.setattr(rr, "app_log_path", lambda *a, **k: None)
    watch = rr.LogWatch("anything")
    assert watch.sources() == []
    assert watch.new_text() == ""


def test_logwatch_use_watches_new_path_from_its_end(tmp_path, monkeypatch, rr):
    monkeypatch.setattr(rr, "own_log_path", lambda tag: tmp_path / "missing.log")
    monkeypatch.setattr(rr, "app_log_path", lambda *a, **k: None)
    other = tmp_path / "launch.log"
    other.write_text("=== earlier launch ===\nERROR: stale\n", encoding="utf-8")
    watch = rr.LogWatch("anything")
    watch.use(other)
    with open(other, "a", encoding="utf-8") as f:
        f.write("ERROR: fresh\n")
    assert rr.parse_log(watch.new_text()) == (["fresh"], [])


def test_logwatch_truncated_file_is_read_from_the_start(tmp_path, monkeypatch, rr):
    log = tmp_path / "own.log"
    log.write_text("x" * 200 + "\n", encoding="utf-8")
    monkeypatch.setattr(rr, "own_log_path", lambda tag: log)
    watch = rr.LogWatch("anything")
    log.write_text("ERROR: after rotation\n", encoding="utf-8")   # smaller than before
    assert rr.parse_log(watch.new_text())[0] == ["after rotation"]


# ---------------------------------------------------------------------------
# items_from_tokens
# ---------------------------------------------------------------------------

def test_items_from_tokens_plain_files_label_is_file_name(rr):
    items = rr.items_from_tokens(["renders/before.png", "renders/after.png"])
    assert [it["index"] for it in items] == [0, 1]
    assert [it["label"] for it in items] == ["before.png", "after.png"]
    assert items[0]["path"] == "renders/before.png"
    assert items[0]["meta"] == {} and items[0]["title"] == ""


def test_items_from_tokens_in_out_and_fps(rr):
    items = rr.items_from_tokens(["[", "a.mov", "-in", "10", "-out", "20", "-fps", "12", "]"])
    assert len(items) == 1
    it = items[0]
    assert it["path"] == "a.mov"
    assert it["label"] == "a.mov"
    assert (it["in"], it["out"], it["fps"]) == (10, 20, 12.0)


def test_items_from_tokens_stereo_group_keeps_both_files(rr):
    items = rr.items_from_tokens(["[", "left.exr", "right.exr", "]"])
    assert items[0]["path"] == ["left.exr", "right.exr"]
    assert items[0]["label"] == "left.exr"


def test_items_from_tokens_select_view_records_view(rr):
    items = rr.items_from_tokens(["[", "views.exr", "-select", "view", "left", "]"])
    assert items[0]["view"] == "left"


def test_items_from_tokens_select_view_is_not_a_file(rr):
    items = rr.items_from_tokens(["[", "views.exr", "-select", "view", "left", "]"])
    assert items[0]["path"] == "views.exr"


def test_resolve_sources_select_view_from_manifest_tokens(tmp_path, rr):
    media = tmp_path / "views.exr"
    media.write_bytes(b"")
    tokens = rr.rm.rv_tokens({"path": "views.exr", "view": "left"})
    assert tokens == ["[", "views.exr", "-select", "view", "left", "]"]
    out = rr.resolve_sources(tokens, cwd=tmp_path)
    assert out == ["[", str(media.resolve()), "-select", "view", "left", "]"]


# ---------------------------------------------------------------------------
# item_ranges / item_at_frame
# ---------------------------------------------------------------------------

def test_item_ranges_sequence_uses_edl_starts(rr):
    ranges = rr.item_ranges(_state(1, 30, (1, 11, 21)), [0, 1, 2], 3)
    assert ranges == [(1, 10), (11, 20), (21, 30)]


def test_item_ranges_stack_every_source_covers_whole_range(rr):
    ranges = rr.item_ranges(_state(1, 30, (1, 11, 21)), [0, 1, 2], 3, layout="wipe")
    assert ranges == [(1, 30), (1, 30), (1, 30)]


def test_item_ranges_view_expansion_merges_sources_of_one_item(rr):
    # item 0 was expanded into two sources (left and right view), item 1 is one source
    ranges = rr.item_ranges(_state(1, 3, (1, 2, 3)), [0, 0, 1], 2)
    assert ranges == [(1, 2), (3, 3)]


def test_item_ranges_without_state_is_all_none(rr):
    assert rr.item_ranges(None, [0, 1], 2) == [None, None]


def test_item_ranges_skips_unmapped_and_out_of_range_sources(rr):
    ranges = rr.item_ranges(_state(1, 30, (1, 11, 21)), [None, 0, 5], 2)
    assert ranges == [(11, 20), None]


@pytest.mark.parametrize("frame,label", [(1, "a"), (10, "a"), (11, "b"), (20, "b"), (21, None),
                                         (0, None)])
def test_item_at_frame(rr, frame, label):
    items = [{"label": "a", "frames": [1, 10]}, {"label": "none", "frames": None},
             {"label": "b", "frames": [11, 20]}]
    hit = rr.item_at_frame(items, frame)
    assert (hit["label"] if hit else None) == label


# ---------------------------------------------------------------------------
# review_props_commands
# ---------------------------------------------------------------------------

def _items(n, meta=None, **extra):
    return [dict({"index": i, "label": f"v{i}", "title": f"Version {i}", "path": f"v{i}.png",
                  "meta": meta(i) if meta else {"id": i}}, **extra) for i in range(n)]


def test_review_props_commands_compile_and_store_every_item(rr):
    items = _items(2, group="g1")
    cmds = rr.review_props_commands([_src(0), _src(1)], [0, 1], items)
    assert cmds
    for c in cmds:
        compile(c, "<test>", "exec")
    props = _run_in_fake_rv(cmds)
    for k, it in enumerate(items):
        base = f"{_src(k)}.review."
        assert props[base + "item"] == ("int", 1, [it["index"]])
        assert props[base + "label"][2] == [it["label"]]
        assert props[base + "title"][2] == [it["title"]]
        assert props[base + "group"][2] == ["g1"]
        assert props[base + "view"][2] == [""]
        assert json.loads(props[base + "meta"][2][0]) == it["meta"]


def test_review_props_commands_embed_meta_json_text(rr):
    items = _items(3, meta=lambda i: {"shot": f"sh{i:03d}", "take": i})
    cmds = rr.review_props_commands([_src(0), _src(1), _src(2)], [0, 1, 2], items)
    text = "\n".join(cmds)
    for it in items:
        assert json.dumps(it["meta"], sort_keys=True, separators=(",", ":")) in text


def test_review_props_commands_skip_unmapped_sources(rr):
    items = _items(1)
    props = _run_in_fake_rv(rr.review_props_commands([_src(0), _src(1)], [None, 0], items))
    assert not any(p.startswith(_src(0)) for p in props)
    assert props[f"{_src(1)}.review.item"][2] == [0]


def test_review_props_commands_view_expansion_writes_item_on_each_source(rr):
    items = _items(1)
    props = _run_in_fake_rv(rr.review_props_commands([_src(0), _src(1)], [0, 0], items))
    assert props[f"{_src(0)}.review.item"][2] == [0]
    assert props[f"{_src(1)}.review.item"][2] == [0]


def test_review_props_commands_session_node(rr):
    manifest = {"title": "Look dev", "layout": "sequence", "meta": {"round": 2},
                "groups": [{"id": "g1", "label": "Group 1"}]}
    props = _run_in_fake_rv(rr.review_props_commands([_src(0)], [0], _items(1),
                                                     session_node="rv_session", manifest=manifest))
    assert props["rv_session.review.schema_version"][2] == [rr.rm.SCHEMA_VERSION]
    assert props["rv_session.review.title"][2] == ["Look dev"]
    assert props["rv_session.review.layout"][2] == ["sequence"]
    assert json.loads(props["rv_session.review.meta"][2][0]) == {"round": 2}
    assert json.loads(props["rv_session.review.groups"][2][0]) == manifest["groups"]


def test_review_props_commands_chunk_long_command_lists(rr):
    big = "x" * 500
    items = _items(20, meta=lambda i: {"note": big, "i": i})
    names = [_src(i) for i in range(20)]
    cmds = rr.review_props_commands(names, list(range(20)), items, chunk=6000)
    assert len(cmds) > 1
    # every chunk holds at most one property beyond the threshold
    assert all(len(c) < 6000 + 3 * len(big) + 1000 for c in cmds)
    props = _run_in_fake_rv(cmds)
    for i in range(20):
        assert json.loads(props[f"{_src(i)}.review.meta"][2][0]) == {"note": big, "i": i}


def test_review_props_commands_small_input_is_one_command(rr):
    assert len(rr.review_props_commands([_src(0)], [0], _items(1))) == 1


def test_review_props_commands_are_ascii_and_round_trip_unicode(rr):
    meta = {"note": "caf\u00e9 \u2014 \u65e5\u672c \U0001F3AC", "who": "\u00c5sa"}
    items = [{"index": 0, "label": "\u00e9t\u00e9.png", "title": "\u00fcber", "path": "a.png",
              "meta": meta}]
    cmds = rr.review_props_commands([_src(0)], [0], items, session_node="rv_session",
                                    manifest={"title": "r\u00e9vision", "meta": meta})
    for c in cmds:
        c.encode("ascii")                                  # raises on any non-ASCII
        assert all(ord(ch) < 128 for ch in c)
    props = _run_in_fake_rv(cmds)
    assert props[f"{_src(0)}.review.label"][2] == ["\u00e9t\u00e9.png"]
    assert props[f"{_src(0)}.review.title"][2] == ["\u00fcber"]
    assert json.loads(props[f"{_src(0)}.review.meta"][2][0]) == meta
    assert props["rv_session.review.title"][2] == ["r\u00e9vision"]


# ---------------------------------------------------------------------------
# annotation_commands
# ---------------------------------------------------------------------------

def test_annotation_commands_draw_into_paint_node(rr):
    items = [{"index": 0, "label": "a", "path": "frame_0001.png",
              "annotations": [{"frame": 1, "text": "check the edge"}]},
             {"index": 1, "label": "b", "path": "frame_0002.png"}]
    cmds = rr.annotation_commands([_src(0), _src(1)], [0, 1], items)
    assert len(cmds) == 1                                  # item 1 has no annotations
    compile(cmds[0], "<test>", "exec")
    assert "sourceGroup000000_paint.text:1:1:review.text" in cmds[0]
    assert "sourceGroup000000_paint.frame:1.order" in cmds[0]
    props = _run_in_fake_rv(cmds)
    assert props["sourceGroup000000_paint.text:1:1:review.text"] == ("string", 1, ["check the edge"])
    assert props["sourceGroup000000_paint.frame:1.order"][2] == ["text:1:1:review"]
    assert props["sourceGroup000000_paint.text:1:1:review.position"][:2] == ("float", 2)
    assert props["sourceGroup000000_paint.text:1:1:review.color"][:2] == ("float", 4)
    assert props["sourceGroup000000_paint.paint.nextId"] == ("int", 1, [2])
    assert not any(p.startswith("sourceGroup000001") for p in props)


def test_annotation_commands_uses_items_source_frame(rr):
    # a still named ..._0012 sits on source frame 12, so its first annotation is text:1:12
    items = [{"index": 0, "label": "a", "path": "frame_0012.png",
              "annotations": [{"frame": 1, "text": "one"}, {"frame": 1, "text": "two"}]}]
    props = _run_in_fake_rv(rr.annotation_commands([_src(3)], [0], items))
    assert props["sourceGroup000003_paint.frame:12.order"][2] == \
        ["text:1:12:review", "text:2:12:review"]
    assert props["sourceGroup000003_paint.text:2:12:review.text"][2] == ["two"]


def test_annotation_commands_ascii_only(rr):
    items = [{"index": 0, "label": "a", "path": "frame_0001.png",
              "annotations": [{"frame": 1, "text": "tr\u00e8s bien \u2713"}]}]
    cmds = rr.annotation_commands([_src(0)], [0], items)
    cmds[0].encode("ascii")
    props = _run_in_fake_rv(cmds)
    assert props["sourceGroup000000_paint.text:1:1:review.text"][2] == ["tr\u00e8s bien \u2713"]


def test_annotation_commands_nothing_to_draw(rr):
    assert rr.annotation_commands([_src(0)], [None], _items(1)) == []
    assert rr.annotation_commands([_src(0)], [0], _items(1)) == []


# ---------------------------------------------------------------------------
# items_from_rv
# ---------------------------------------------------------------------------

def test_items_from_rv_with_review_props_groups_sources_by_item(rr):
    info = [
        (_src(0), ["media/v1.exr"], _review_vals("v1", "First", "g1", "left", '{"id":1}'), [0]),
        (_src(1), ["media/v1.exr"], _review_vals("v1", "First", "g1", "right", '{"id":1}'), [0]),
        (_src(2), ["media/v2.exr"], _review_vals("v2", meta='{"id":2}'), [1]),
    ]
    items, source_items = rr.items_from_rv(info, _state(1, 3, (1, 2, 3)))
    assert source_items == [0, 0, 1]
    assert [it["label"] for it in items] == ["v1", "v2"]
    assert items[0]["_sources"] == [_src(0), _src(1)]
    assert items[0]["meta"] == {"id": 1}
    assert items[0]["group"] == "g1"
    assert items[0]["view"] == "left"
    assert items[0]["title"] == "First"
    assert items[0]["frames"] == [1, 2]
    assert items[1]["frames"] == [3, 3]
    assert "group" not in items[1]


def test_items_from_rv_without_review_props_one_item_per_source(rr):
    info = [(_src(0), ["plates/a.png"], _review_vals(), []),
            (_src(1), ["plates/l.exr", "plates/r.exr"], _review_vals(), [])]
    items, source_items = rr.items_from_rv(info, _state(1, 2, (1, 2)))
    assert source_items == [0, 1]
    assert items[0]["label"] == "a.png"
    assert items[0]["path"] == "plates/a.png"
    assert items[0]["meta"] == {}
    assert items[1]["path"] == ["plates/l.exr", "plates/r.exr"]
    assert [it["frames"] for it in items] == [[1, 1], [2, 2]]


def test_items_from_rv_bad_meta_json_becomes_empty_dict(rr):
    info = [(_src(0), ["a.png"], _review_vals("a", meta="not json"), [0]),
            (_src(1), ["b.png"], _review_vals("b", meta="[1, 2]"), [1])]
    items, _ = rr.items_from_rv(info, None)
    assert [it["meta"] for it in items] == [{}, {}]
    assert [it["frames"] for it in items] == [None, None]


def test_items_from_rv_stack_layout(rr):
    info = [(_src(0), ["a.png"], _review_vals("a"), [0]),
            (_src(1), ["b.png"], _review_vals("b"), [1])]
    items, _ = rr.items_from_rv(info, _state(1, 5, (1, 3)), layout="stack")
    assert [it["frames"] for it in items] == [[1, 5], [1, 5]]


# ---------------------------------------------------------------------------
# _paint_target / build_notes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("node,expected", [
    ("sourceGroup000001_paint", ("sourceGroup000001_source", "source")),
    ("defaultStack_p_sourceGroup000002", ("sourceGroup000002_source", "source")),
    ("defaultStack_paint", (None, "global")),
    ("defaultLayout_paint", (None, "global")),
])
def test_paint_target(rr, node, expected):
    assert rr._paint_target(node) == expected


def _note_items():
    return [{"index": 0, "label": "a", "frames": [1, 10], "_sources": [_src(0)]},
            {"index": 1, "label": "b", "frames": [11, 20], "_sources": [_src(1)]}]


def test_build_notes_source_paint_maps_to_its_item(rr):
    paint = [("sourceGroup000001_paint", [
        ("sourceGroup000001_paint.frame:1.order", ["text:1:1:review", "pen:2:1:user_1"]),
        ("sourceGroup000001_paint.text:1:1:review.text", ["fix the seam"]),
    ])]
    notes = rr.build_notes(_note_items(), [(11, [_src(1)], 1)], paint)
    assert notes[0] == []
    assert len(notes[1]) == 1
    n = notes[1][0]
    assert n["frame"] == 11
    assert n["item_frame"] == 1
    assert n["source_frame"] == 1
    assert n["texts"] == ["fix the seam"]
    assert n["strokes"] == 1
    assert n["image"] is None


def test_build_notes_source_paint_without_annotated_frame(rr):
    paint = [("sourceGroup000000_paint", [
        ("sourceGroup000000_paint.frame:4.order", ["pen:1:4:user_1", "pen:2:4:user_1"]),
    ])]
    notes = rr.build_notes(_note_items(), [], paint)
    assert notes[0] == [{"frame": None, "item_frame": None, "source_frame": 4, "texts": [],
                         "strokes": 2, "image": None}]


def test_build_notes_global_paint_maps_by_global_frame(rr):
    paint = [("defaultStack_paint", [
        ("defaultStack_paint.frame:15.order", ["text:1:15:review", "text:2:15:review"]),
        ("defaultStack_paint.text:1:15:review.text", ["too dark"]),
        ("defaultStack_paint.text:2:15:review.text", [""]),
        ("defaultStack_paint.frame:99.order", ["text:3:99:review"]),       # no item there
    ])]
    notes = rr.build_notes(_note_items(), [], paint)
    assert notes[0] == []
    assert notes[1] == [{"frame": 15, "item_frame": 5, "source_frame": None,
                         "texts": ["too dark"], "strokes": 0, "image": None}]


def test_build_notes_annotated_frames_only_and_sorting(rr):
    annotated = [(12, [_src(1)], 2), (3, [_src(0)], 3), (1, [_src(0)], 1),
                 (5, ["someOther_source"], 5)]
    notes = rr.build_notes(_note_items(), annotated, [])
    assert [(n["frame"], n["item_frame"], n["source_frame"]) for n in notes[0]] == \
        [(1, 1, 1), (3, 3, 3)]
    assert [(n["frame"], n["source_frame"]) for n in notes[1]] == [(12, 2)]


def test_build_notes_texts_merge_without_duplicates(rr):
    paint = [("sourceGroup000000_paint", [
        ("sourceGroup000000_paint.frame:2.order", ["text:1:2:review"]),
        ("sourceGroup000000_paint.text:1:2:review.text", ["same note"]),
    ]), ("defaultStack_paint", [
        ("defaultStack_paint.frame:2.order", ["text:1:2:review", "pen:2:2:user_1"]),
        ("defaultStack_paint.text:1:2:review.text", ["same note"]),
    ])]
    notes = rr.build_notes(_note_items(), [(2, [_src(0)], 2)], paint)
    assert len(notes[0]) == 1
    assert notes[0][0]["texts"] == ["same note"]
    assert notes[0][0]["strokes"] == 1


# ---------------------------------------------------------------------------
# read_notes (read_state and _eval faked)
# ---------------------------------------------------------------------------

def _fake_rv_answers(rr, monkeypatch, state, answers):
    """Make read_state / _eval answer from canned data, keyed by the module's expressions."""
    asked = []

    def fake_eval(rvpush, tag, expr):
        asked.append(expr)
        return answers.get(expr)

    def fail(*a, **k):
        raise AssertionError("rvpush must not run in these tests")

    monkeypatch.setattr(rr, "read_state", lambda rvpush, tag: state)
    monkeypatch.setattr(rr, "_eval", fake_eval)
    monkeypatch.setattr(rr, "_rvpush", fail)
    return asked


def test_read_notes_end_to_end(rr, monkeypatch):
    meta0 = {"shot": "sh010", "version": 3, "tags": ["a", "b"]}
    meta1 = {"shot": "sh020", "nested": {"k": [1, 2]}}
    answers = {
        rr.SOURCE_REVIEW_EXPR: [
            (_src(0), ["media/v3.exr"], _review_vals("sh010 v3", "", "g1", "", json.dumps(meta0)), [0]),
            (_src(1), ["media/v1.exr"], _review_vals("sh020 v1", "", "g1", "", json.dumps(meta1)), [1]),
        ],
        rr.SESSION_REVIEW_EXPR: [[["Dailies"], ["sequence"], ['{"round":1}'],
                                  ['[{"id":"g1","label":"Group"}]']]],
        rr.ANNOTATED_EXPR: [(3, [_src(0)], 3), (12, [_src(1)], 2)],
        rr.PAINT_EXPR: [
            ("sourceGroup000000_paint", [
                ("sourceGroup000000_paint.frame:3.order", ["text:1:3:review", "pen:2:3:user_1"]),
                ("sourceGroup000000_paint.text:1:3:review.text", ["roto edge"]),
            ]),
            ("sourceGroup000001_paint", [
                ("sourceGroup000001_paint.frame:2.order", ["text:1:2:review"]),
                ("sourceGroup000001_paint.text:1:2:review.text", ["grade warmer"]),
            ]),
        ],
    }
    asked = _fake_rv_answers(rr, monkeypatch, _state(1, 20, (1, 11), marks=(1, 11)), answers)
    body = rr.read_notes("rv", "rvpush", "review-tag")
    assert set(asked) == {rr.SOURCE_REVIEW_EXPR, rr.SESSION_REVIEW_EXPR, rr.ANNOTATED_EXPR,
                          rr.PAINT_EXPR}
    json.dumps(body)                                       # JSON-serialisable
    assert body["action"] == "notes"
    assert body["tag"] == "review-tag"
    assert body["title"] == "Dailies"
    assert body["meta"] == {"round": 1}
    assert body["groups"] == [{"id": "g1", "label": "Group", "title": "", "frames": [1, 20],
                               "meta": {}}]
    assert body["annotated_frames"] == [3, 12]
    assert body["marks"] == [1, 11]
    assert body["export"] is None
    assert body["problems"] == []
    items = body["items"]
    assert [it["meta"] for it in items] == [meta0, meta1]
    assert [it["label"] for it in items] == ["sh010 v3", "sh020 v1"]
    assert [it["frames"] for it in items] == [[1, 10], [11, 20]]
    assert [it["sources"] for it in items] == [[_src(0)], [_src(1)]]
    n0, n1 = items[0]["notes"], items[1]["notes"]
    assert [(n["frame"], n["item_frame"], n["texts"], n["strokes"]) for n in n0] == \
        [(3, 3, ["roto edge"], 1)]
    assert [(n["frame"], n["item_frame"], n["source_frame"], n["texts"]) for n in n1] == \
        [(12, 2, 2, ["grade warmer"])]


def test_read_notes_without_review_props_or_annotations(rr, monkeypatch):
    answers = {rr.SOURCE_REVIEW_EXPR: [(_src(0), ["a.png"], _review_vals(), []),
                                       (_src(1), ["b.png"], _review_vals(), [])]}
    _fake_rv_answers(rr, monkeypatch, _state(1, 2, (1, 2)), answers)
    body = rr.read_notes("rv", "rvpush", "t")
    assert body["title"] == ""
    assert body["meta"] == {} and body["groups"] == []
    assert body["annotated_frames"] == []
    assert [it["label"] for it in body["items"]] == ["a.png", "b.png"]
    assert all(it["notes"] == [] and it["meta"] == {} for it in body["items"])


def test_read_notes_stack_layout_from_view_node(rr, monkeypatch):
    answers = {rr.SOURCE_REVIEW_EXPR: [(_src(0), ["a.png"], _review_vals("a"), [0]),
                                       (_src(1), ["b.png"], _review_vals("b"), [1])],
               rr.PAINT_EXPR: [("defaultStack_paint", [
                   ("defaultStack_paint.frame:4.order", ["text:1:4:review"]),
                   ("defaultStack_paint.text:1:4:review.text", ["wipe note"])])]}
    _fake_rv_answers(rr, monkeypatch, _state(1, 8, (1, 5), view_type="RVStackGroup"), answers)
    body = rr.read_notes("rv", "rvpush", "t")
    assert [it["frames"] for it in body["items"]] == [[1, 8], [1, 8]]
    # a stack shows every item on every frame; the global paint goes to the first match
    assert body["items"][0]["notes"][0]["texts"] == ["wipe note"]
    assert body["annotated_frames"] == [4]


def test_read_notes_no_rv_answering_raises(rr, monkeypatch):
    _fake_rv_answers(rr, monkeypatch, None, {})
    with pytest.raises(rr.RvError, match="no RV with tag 'gone' answered"):
        rr.read_notes("rv", "rvpush", "gone")


# ---------------------------------------------------------------------------
# envelope
# ---------------------------------------------------------------------------

def test_envelope_ok(rr):
    res = rr.envelope({"action": "launched", "sources": 2})
    assert res["schema"] == "rv-review.result"
    assert res["schema_version"] == 1
    assert res["ok"] is True
    assert res["exit_code"] == 0
    assert res["action"] == "launched" and res["sources"] == 2
    assert "error" not in res
    assert list(res)[:4] == ["schema", "schema_version", "ok", "exit_code"]


@pytest.mark.parametrize("code", [1, 2, 3])
def test_envelope_failure_codes_are_not_ok(rr, code):
    res = rr.envelope({"action": "error"}, code, "went wrong")
    assert res["ok"] is False
    assert res["exit_code"] == code
    assert res["error"] == "went wrong"


def test_envelope_ok_follows_exit_code_not_body(rr):
    assert rr.envelope({"ok": False}, rr.EXIT_OK)["ok"] is True
    assert rr.envelope({"ok": True}, rr.EXIT_MISMATCH)["ok"] is False


def test_envelope_exit_code_constants(rr):
    assert (rr.EXIT_OK, rr.EXIT_ERROR, rr.EXIT_USAGE, rr.EXIT_MISMATCH) == (0, 1, 2, 3)
    assert rr.envelope()["ok"] is True
    json.dumps(rr.envelope(None, rr.EXIT_ERROR, "x"))


# ---------------------------------------------------------------------------
# post_commands: difference-inverted
# ---------------------------------------------------------------------------

def test_post_commands_difference_inverted(rr):
    s = rr.post_commands(compare="difference-inverted")
    assert "'defaultStack_stack.composite.type', ['-difference']" in s
    assert "setViewNode('defaultStack')" in s
    compile(s, "post", "exec")


# ---------------------------------------------------------------------------
# CLI, via subprocess (sys.executable only; every case stops before RV is looked up)
# ---------------------------------------------------------------------------

def _run_cli(*args, cwd=None):
    return subprocess.run([sys.executable, str(RV_REVIEW_SCRIPT), *args], capture_output=True,
                          text=True, timeout=30, cwd=cwd)


def _one_json_line(stdout):
    lines = stdout.strip().splitlines()
    assert len(lines) == 1, stdout
    return json.loads(lines[0])


@pytest.mark.parametrize("args", [["--compare", "bogus", "a.png"], ["--no-such-flag"],
                                  ["--fps", "fast", "a.png"], ["--stereo-views", "left", "a.png"]])
def test_cli_bad_arguments_print_one_json_line_and_exit_2(tmp_path, args):
    result = _run_cli(*args, cwd=tmp_path)
    assert result.returncode == 2
    res = _one_json_line(result.stdout)
    assert res["ok"] is False
    assert res["exit_code"] == 2
    assert res["schema"] == "rv-review.result"
    assert res["action"] == "error"
    assert res["error"].startswith("rv_review.py:")
    assert "usage" in result.stderr.lower()


def test_cli_export_annotated_without_notes_exits_1(tmp_path):
    result = _run_cli("--export-annotated", str(tmp_path / "notes"), cwd=tmp_path)
    assert result.returncode == 1
    res = _one_json_line(result.stdout)
    assert res["ok"] is False
    assert res["exit_code"] == 1
    assert "--export-annotated goes with --notes" in res["error"]
    assert result.stderr.startswith("rv_review:")
    assert not (tmp_path / "notes").exists()
