"""Tests for rv-review/scripts/rv_session.py (RV .rv session files in text GTO).

Covered: node-name sanitising, GTO string escaping and property formats, the Gto
document writer, build()/write() for the sequence, stack (wipe / over / replace /
difference / difference-inverted) and tile layouts, per-item cut / fps / view /
stereo views, RVPaint text annotations and their frame mapping, the "review" meta
round trip, summarise(), structural_problems(), the media helpers (media_frames,
item_length, offline_item_ranges, image_size), paint_properties(), and the
write / check / render command line.

Nothing here opens an RV window or runs rv, rvpush or rvio: the CLI 'check' and
'render' calls pass --rv-bin pointing at an empty folder, which makes rv_tool()
find nothing (the structural checker is used and render stops before rvio).
The only real RV tool used is gtoinfo, through the rv_bin fixture, which skips
when RV / OpenRV is not installed. All media are tiny synthetic Pillow images
in tmp_path.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
RV_SESSION_SCRIPT = REPO_ROOT / "rv-review" / "scripts" / "rv_session.py"

OBJ_LINE = re.compile(r"^(\S.*?) : (\w+) \((\d+)\)$")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]+$")


def _png(path, size=(8, 4)):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (40, 80, 120)).save(path)
    return path


def _manifest(rs, base, items, **top):
    """A normalised manifest (paths resolved against base)."""
    return rs.rm.normalise({"schema_version": 1, "items": items, **top}, base=base)


def _unescape(token):
    """Python text of a double-quoted GTO string token."""
    assert token.startswith('"') and token.endswith('"'), token
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), token[1:-1])


def _string_value(line):
    m = re.match(r'^string \w+ = ("(?:\\.|[^"\\])*")$', line)
    assert m, line
    return _unescape(m.group(1))


def parse_gto(text):
    """{object: (protocol, version, {component: [property lines]})} for the layout the
    writer produces (objects at column 0, components at 4, properties at 8)."""
    objs, obj, comp = {}, None, None
    for line in text.splitlines():
        m = OBJ_LINE.match(line)
        if m:
            obj = objs.setdefault(m.group(1), (m.group(2), int(m.group(3)), {}))
            continue
        if line.startswith("        "):
            obj[2][comp].append(line.strip())
        elif line.startswith("    "):
            s = line.strip()
            if s not in ("{", "}"):
                comp = s[1:-1] if s.startswith('"') else s
                obj[2].setdefault(comp, [])
    return objs


def _props(objs, obj, comp):
    return objs[obj][2][comp]


def _three_stills(rs, tmp_path, **top):
    for name in ("shotA_before.png", "shotA_after.png", "shotB_before.png"):
        _png(tmp_path / name)
    items = [
        {"path": "shotA_before.png", "label": "before", "group": "shotA"},
        {"path": "shotA_after.png", "label": "after", "group": "shotA"},
        {"path": "shotB_before.png", "label": "shotB before", "group": "shotB"},
    ]
    groups = [{"id": "shotA", "label": "shot A"}, {"id": "shotB"}]
    return _manifest(rs, tmp_path, items, groups=groups, title="lighting pass", **top)


# ---------------------------------------------------------------------------
# sanitize_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("", "node"),
    ("v", "v_n"),
    ("my-track", "my_track"),
    ("é_track", "track"),
    ("1abc", "n_1abc"),
    ("9", "n_9"),
    ("__", "node"),
    ("-", "node"),
    ("x!", "x_n"),
    ("shot A/v2", "shot_A_v2"),
    ("_ok_", "ok"),
    ("_a1", "a1"),
    ("sourceGroup000001", "sourceGroup000001"),
])
def test_sanitize_name(rs, text, expected):
    assert rs.sanitize_name(text) == expected


def test_sanitize_name_unique_against_taken(rs):
    assert rs.sanitize_name("shotA", {"shotA"}) == "shotA_2"
    assert rs.sanitize_name("shotA", {"shotA", "shotA_2"}) == "shotA_3"
    assert rs.sanitize_name("", ["node"]) == "node_2"
    assert rs.sanitize_name("v", ("v_n",)) == "v_n_2"


def test_sanitize_name_always_valid(rs):
    taken = set()
    for text in ["", "a", "1", "--", "a b", "é", "漢字", "x.y", "a" * 3, "a", "a"]:
        n = rs.sanitize_name(text, taken)
        assert NAME_RE.match(n), n
        assert n not in taken
        taken.add(n)


# ---------------------------------------------------------------------------
# gto_string / gto_property
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("plain", '"plain"'),
    ('say "hi"', '"say \\"hi\\""'),
    ("a\\b", '"a\\\\b"'),
    ("a\nb", '"a\\nb"'),
    ("a\r\nb", '"a\\nb"'),
    ('x\\"', '"x\\\\\\""'),
    (5, '"5"'),
    ("", '""'),
])
def test_gto_string(rs, value, expected):
    assert rs.gto_string(value) == expected


def test_gto_string_round_trip(rs):
    s = 'q"uote \\back\\slash\nnew line {brace} café'
    assert _unescape(rs.gto_string(s)) == s


@pytest.mark.parametrize("args,expected", [
    (("int", "currentFrame", 1), "int currentFrame = 1"),
    (("float", "fps", 24.0), "float fps = 24"),
    (("float", "fps", 23.976), "float fps = 23.976"),
    (("int", "item", 3.0), "int item = 3"),
    (("string", "name", "shot A"), 'string name = "shot A"'),
    (("int", "marks", [1, 3]), "int marks = [ 1 3 ]"),
    (("string", "order", ["a", "b"]), 'string order = [ "a" "b" ]'),
    (("int", "marks", []), "int marks = [ ]"),
    (("string", "lhs", ()), "string lhs = [ ]"),
])
def test_gto_property_scalar_and_list(rs, args, expected):
    assert rs.gto_property(*args) == expected


def test_gto_property_width_2_rows(rs):
    assert rs.gto_property("float", "position", [(0.1, 0.25)], 2) == \
        "float[2] position = [ [ 0.1 0.25 ] ]"
    # a flat list is one row
    assert rs.gto_property("float", "position", [0.1, 0.25], 2) == \
        "float[2] position = [ [ 0.1 0.25 ] ]"


def test_gto_property_width_4_rows(rs):
    line = rs.gto_property("float", "color", [(1, 0, 0, 1), (0, 1, 0.5, 1)], 4)
    assert line == "float[4] color = [ [ 1 0 0 1 ] [ 0 1 0.5 1 ] ]"


def test_gto_property_width_string_rows_are_quoted(rs):
    assert rs.gto_property("string", "pair", [("a", 'b"')], 2) == \
        'string[2] pair = [ [ "a" "b\\"" ] ]'


# ---------------------------------------------------------------------------
# Gto document writer
# ---------------------------------------------------------------------------

def test_gto_text_exact(rs):
    g = rs.Gto()
    g.prop("rv", "RVSession", "session", "string", "viewNode", "review_sequence", version=4)
    g.prop("node_paint", "RVPaint", "frame:3", "string", "order", ["text:1:3:review"], version=3)
    g.prop("rv", "RVSession", "session", "int", "currentFrame", 1, version=4)
    assert g.text() == (
        "GTOa (4)\n"
        "\n"
        "rv : RVSession (4)\n"
        "{\n"
        "    session\n"
        "    {\n"
        '        string viewNode = "review_sequence"\n'
        "        int currentFrame = 1\n"
        "    }\n"
        "}\n"
        "\n"
        "node_paint : RVPaint (3)\n"
        "{\n"
        '    "frame:3"\n'
        "    {\n"
        '        string order = [ "text:1:3:review" ]\n'
        "    }\n"
        "}\n"
    )


def test_gto_object_version_is_fixed_by_first_prop(rs):
    g = rs.Gto()
    g.prop("a_node", "RVThing", "c", "int", "x", 1, version=2)
    g.prop("a_node", "RVThing", "c", "int", "y", 2, version=9)
    assert g.text().count("a_node : RVThing (2)") == 1
    assert "(9)" not in g.text()


# ---------------------------------------------------------------------------
# build / write: sequence with groups
# ---------------------------------------------------------------------------

def test_build_sequence_three_stills_with_groups(rs, tmp_path):
    m = _three_stills(rs, tmp_path)
    text = rs.build(m).text()
    assert text.startswith("GTOa (4)\n")
    objs = parse_gto(text)
    names = list(objs)
    assert names[:3] == ["rv", "connections", "review_sequence"]
    assert objs["rv"][:2] == ("RVSession", 4)
    assert objs["connections"][:2] == ("connection", 1)
    assert objs["review_sequence"][:2] == ("RVSequenceGroup", 1)

    session = _props(objs, "rv", "session")
    assert 'string viewNode = "review_sequence"' in session
    assert "int currentFrame = 1" in session
    assert not any("fps" in ln for ln in session)
    review = _props(objs, "rv", "review")
    assert "int schema_version = 1" in review
    assert 'string title = "lighting pass"' in review
    assert 'string layout = "sequence"' in review
    groups_line = next(ln for ln in review if ln.startswith("string groups"))
    assert json.loads(_string_value(groups_line)) == m["groups"]

    seq = objs["review_sequence"][2]
    assert seq["ui"] == ['string name = "lighting pass"']
    assert seq["session"] == ["int marks = [ 1 3 ]", "int frame = 1"]

    conn = _props(objs, "connections", "evaluation")
    assert conn == [
        'string lhs = [ "sourceGroup000000" "sourceGroup000001" "sourceGroup000002" ]',
        'string rhs = [ "review_sequence" "review_sequence" "review_sequence" ]',
    ]

    for i, (label, group) in enumerate([("before", "shotA"), ("after", "shotA"),
                                        ("shotB before", "shotB")]):
        grp, src = f"sourceGroup{i:06d}", f"sourceGroup{i:06d}_source"
        assert objs[grp][0] == "RVSourceGroup"
        assert _props(objs, grp, "ui") == [f'string name = "{label}"']
        assert objs[src][0] == "RVFileSource"
        movie = _string_value(_props(objs, src, "media")[0])
        assert movie == m["items"][i]["path"].replace("\\", "/")
        assert "\\" not in movie
        rv_review = _props(objs, src, "review")
        assert f"int item = {i}" in rv_review
        assert f'string label = "{label}"' in rv_review
        assert f'string group = "{group}"' in rv_review
        # no cut / request / paint for plain stills
        assert set(objs[src][2]) == {"media", "review"}
        assert f"{grp}_paint" not in objs


def test_build_marks_override(rs, tmp_path):
    m = _three_stills(rs, tmp_path)
    seq = parse_gto(rs.build(m, marks=[3, 2, 3]).text())["review_sequence"][2]
    assert seq["session"][0] == "int marks = [ 2 3 ]"
    seq = parse_gto(rs.build(m, marks="none").text())["review_sequence"][2]
    assert seq["session"][0] == "int marks = [ ]"


def test_build_fps_on_session_and_sequence(rs, tmp_path):
    m = _three_stills(rs, tmp_path, fps=24)
    objs = parse_gto(rs.build(m).text())
    assert "float fps = 24" in _props(objs, "rv", "session")
    assert "float fps = 24" in _props(objs, "review_sequence", "session")
    objs = parse_gto(rs.build(m, fps=12.5).text())
    assert "float fps = 12.5" in _props(objs, "rv", "session")


def test_build_rejects_unknown_layout(rs, tmp_path):
    m = _three_stills(rs, tmp_path)
    with pytest.raises(ValueError, match="layout must be one of"):
        rs.build(m, layout="sideways")


def test_build_stereo_mode(rs, tmp_path):
    m = _three_stills(rs, tmp_path, stereo="anaglyph")
    objs = parse_gto(rs.build(m).text())
    assert objs["defaultOutputGroup_stereo"][0] == "RVDisplayStereo"
    assert _props(objs, "defaultOutputGroup_stereo", "stereo") == ['string type = "anaglyph"']
    m = _three_stills(rs, tmp_path, stereo="off")
    assert "defaultOutputGroup_stereo" not in parse_gto(rs.build(m).text())


def test_write_creates_parent_folders(rs, tmp_path):
    m = _three_stills(rs, tmp_path)
    out = rs.write(m, tmp_path / "deep" / "er" / "review.rv")
    assert out == tmp_path / "deep" / "er" / "review.rv"
    assert out.read_text(encoding="utf-8") == rs.build(m).text()


# ---------------------------------------------------------------------------
# build: stack and tile layouts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("layout,op,wipes", [
    ("wipe", "over", 1),
    ("over", "over", 0),
    ("replace", "replace", 0),
    ("difference", "difference", 0),
    ("difference-inverted", "-difference", 0),
])
def test_build_stack_layouts(rs, tmp_path, layout, op, wipes):
    m = _three_stills(rs, tmp_path, layout=layout)
    objs = parse_gto(rs.build(m).text())
    assert 'string viewNode = "review_stack"' in _props(objs, "rv", "session")
    assert f'string layout = "{layout}"' in _props(objs, "rv", "review")
    assert objs["review_stack"][0] == "RVStackGroup"
    assert _props(objs, "review_stack", "ui") == [
        f'string name = "lighting pass ({layout})"', f"int wipes = {wipes}"]
    assert objs["review_stack_stack"][0] == "RVStack"
    assert _props(objs, "review_stack_stack", "composite") == [f'string type = "{op}"']
    # the stack takes the first two items; the sequence still holds all three
    lhs, rhs = _props(objs, "connections", "evaluation")
    assert lhs.endswith('"sourceGroup000002" "sourceGroup000000" "sourceGroup000001" ]')
    assert rhs == ('string rhs = [ "review_sequence" "review_sequence" "review_sequence" '
                   '"review_stack" "review_stack" ]')
    assert "review_layout" not in objs


def test_build_layout_argument_overrides_manifest(rs, tmp_path):
    m = _three_stills(rs, tmp_path, layout="wipe")
    objs = parse_gto(rs.build(m, layout="difference").text())
    assert _props(objs, "review_stack_stack", "composite") == ['string type = "difference"']


def test_build_tile_layout(rs, tmp_path):
    m = _three_stills(rs, tmp_path, layout="tile")
    objs = parse_gto(rs.build(m).text())
    assert 'string viewNode = "review_layout"' in _props(objs, "rv", "session")
    assert objs["review_layout"][0] == "RVLayoutGroup"
    assert _props(objs, "review_layout", "ui") == ['string name = "lighting pass (tile)"']
    assert _props(objs, "review_layout", "layout") == ['string mode = "packed"']
    assert "review_stack" not in objs
    _, rhs = _props(objs, "connections", "evaluation")
    assert rhs.count('"review_layout"') == 3
    assert rhs.count('"review_sequence"') == 3


# ---------------------------------------------------------------------------
# build: per-item cut, fps, view, stereo views, multi-media
# ---------------------------------------------------------------------------

def test_build_item_cut_fps_view_stereo(rs, tmp_path):
    items = [
        {"path": "plates/a.1001-1010#.png", "label": "plate", "in": 1003, "out": 1005,
         "fps": 12.5, "view": "left", "stereo_views": ["left", "right"]},
        {"path": "plates/a.1001-1010#.png", "label": "in only", "in": 1004},
        {"path": "clip.mov", "label": "movie in only", "in": 5},
        {"path": "shotA_7.png", "label": "still out only", "out": 9},
    ]
    objs = parse_gto(rs.build(_manifest(rs, tmp_path, items)).text())
    s0 = objs["sourceGroup000000_source"][2]
    assert s0["cut"] == ["int in = 1003", "int out = 1005"]
    assert s0["group"] == ["float fps = 12.5"]
    assert s0["request"] == ['string imageComponent = [ "view" "left" ]',
                             'string stereoViews = [ "left" "right" ]']
    assert 'string view = "left"' in s0["review"]
    s1 = objs["sourceGroup000001_source"][2]
    assert s1["cut"] == ["int in = 1004", "int out = 1010"]        # out from the spec range
    assert "group" not in s1 and "request" not in s1
    s2 = objs["sourceGroup000002_source"][2]
    assert s2["cut"] == ["int in = 5"]                             # movie: no known out
    s3 = objs["sourceGroup000003_source"][2]
    assert s3["cut"] == ["int in = 7", "int out = 9"]              # in from the still's name


def test_build_multi_media_source(rs, tmp_path):
    items = [{"path": ["eyes/left.png", "eyes/right.png"], "label": "stereo pair"}]
    objs = parse_gto(rs.build(_manifest(rs, tmp_path, items)).text())
    line = _props(objs, "sourceGroup000000_source", "media")[0]
    tokens = re.findall(r'"(?:\\.|[^"\\])*"', line)
    assert line.startswith("string movie = [ ")
    assert [_unescape(t) for t in tokens] == [
        str(tmp_path / "eyes" / "left.png").replace("\\", "/"),
        str(tmp_path / "eyes" / "right.png").replace("\\", "/")]


# ---------------------------------------------------------------------------
# build: annotations
# ---------------------------------------------------------------------------

def test_build_annotations_rvpaint(rs, tmp_path):
    _png(tmp_path / "x__label__3.png")
    items = [{"path": "x__label__3.png", "label": "shotA",
              "annotations": [{"frame": 1, "text": 'edge "here"'},
                              {"frame": 1, "text": "second"},
                              {"frame": 2, "text": "next"}]}]
    text = rs.build(_manifest(rs, tmp_path, items)).text()
    assert "sourceGroup000000_paint : RVPaint (3)" in text
    paint = parse_gto(text)["sourceGroup000000_paint"][2]
    assert list(paint) == ["text:1:3:review", "text:2:3:review", "text:3:4:review",
                           "paint", "frame:3", "frame:4"]
    first = paint["text:1:3:review"]
    assert first[0] == 'string text = "edge \\"here\\""'
    assert [ln.split(" = ")[0] for ln in first] == [
        "string text", "float[2] position", "float[4] color", "float size", "float scale",
        "float spacing", "float rotation", "string font"]
    color = " ".join(rs._num(c, "float") for c in rs.DEFAULT_TEXT_COLOR)
    assert f"float[4] color = [ [ {color} ] ]" in first
    assert f"float size = {rs._num(rs.DEFAULT_TEXT_SIZE, 'float')}" in first
    assert 'string font = ""' in first
    assert paint["paint"] == ["int nextId = 4", "int show = 1"]
    assert paint["frame:3"] == ['string order = [ "text:1:3:review" "text:2:3:review" ]']
    assert paint["frame:4"] == ['string order = [ "text:3:4:review" ]']
    # component names with ':' are quoted in the text
    assert '    "text:1:3:review"\n' in text
    assert '    "frame:3"\n' in text


@pytest.mark.parametrize("item,expected_comp", [
    ({"path": "x__label__3.png"}, "text:1:3:review"),
    ({"path": "plain.png"}, "text:1:1:review"),
    ({"path": "a.1001-1010#.png"}, "text:1:1001:review"),
    ({"path": "a.1001-1010#.png", "in": 1005}, "text:1:1005:review"),
    ({"path": "x__label__3.png", "in": 20}, "text:1:20:review"),
    ({"path": "clip.mov"}, "text:1:1:review"),
])
def test_annotation_frame_1_is_first_source_frame(rs, tmp_path, item, expected_comp):
    item = dict(item, label="shotA", annotations=[{"frame": 1, "text": "note"}])
    paint = parse_gto(rs.build(_manifest(rs, tmp_path, [item])).text())["sourceGroup000000_paint"]
    comps = paint[2]
    assert expected_comp in comps
    frame = expected_comp.split(":")[2]
    assert comps[f"frame:{frame}"] == [f'string order = [ "{expected_comp}" ]']


def test_annotation_source_frame_is_used_as_is(rs, tmp_path):
    item = {"path": "a.1001-1010#.png", "label": "shotA",
            "annotations": [{"source_frame": 1007, "frame": 1, "text": "abs"}]}
    comps = parse_gto(rs.build(_manifest(rs, tmp_path, [item])).text())["sourceGroup000000_paint"][2]
    assert "text:1:1007:review" in comps and "frame:1007" in comps


# ---------------------------------------------------------------------------
# paint_properties
# ---------------------------------------------------------------------------

def _default_position(rs, aspect):
    size = rs.DEFAULT_TEXT_SIZE
    return [round(-aspect / 2 + rs.TEXT_MARGIN, 4),
            round(0.5 - rs.TEXT_MARGIN - size * rs.TEXT_LINE, 4)]


def test_paint_properties_shape(rs, tmp_path):
    _png(tmp_path / "x__label__3.png", size=(8, 4))            # aspect 2
    item = {"path": str(tmp_path / "x__label__3.png"), "label": "shotA",
            "annotations": [{"frame": 1, "text": "hello"}]}
    c = "text:1:3:review"
    assert rs.paint_properties(item) == [
        (c, "string", "text", ["hello"], 1),
        (c, "float", "position", _default_position(rs, 2.0), 2),
        (c, "float", "color", [float(x) for x in rs.DEFAULT_TEXT_COLOR], 4),
        (c, "float", "size", [float(rs.DEFAULT_TEXT_SIZE)], 1),
        (c, "float", "scale", [1.0], 1),
        (c, "float", "spacing", [0.8], 1),
        (c, "float", "rotation", [0.0], 1),
        (c, "string", "font", [""], 1),
        ("paint", "int", "nextId", [2], 1),
        ("paint", "int", "show", [1], 1),
        ("frame:3", "string", "order", [c], 1),
    ]


def test_paint_properties_custom_values(rs, tmp_path):
    item = {"path": str(tmp_path / "missing.png"), "label": "shotA",
            "annotations": [{"frame": 2, "text": "t", "position": [0.12345, -0.2],
                             "size": 0.01, "color": [0, 1, 0]}]}
    props = rs.paint_properties(item)
    by_name = {name: (values, width) for _, _, name, values, width in props}
    assert props[0][0] == "text:1:2:review"
    assert by_name["position"] == ([0.1235, -0.2], 2)
    assert by_name["color"] == ([0.0, 1.0, 0.0, 1.0], 4)       # alpha padded
    assert by_name["size"] == ([0.01], 1)


def test_paint_properties_default_position_uses_16_9_without_image(rs, tmp_path):
    item = {"path": str(tmp_path / "missing.png"), "label": "shotA",
            "annotations": [{"text": "t"}]}
    pos = next(v for _, _, n, v, _ in rs.paint_properties(item) if n == "position")
    assert pos == _default_position(rs, 16 / 9)


def test_paint_properties_empty(rs):
    assert rs.paint_properties({"path": "a.png", "label": "a"}) == []
    assert rs.paint_properties({"path": "a.png", "label": "a", "annotations": []}) == []


# ---------------------------------------------------------------------------
# meta round trip and object names
# ---------------------------------------------------------------------------

TOP_META = {"shot": "shotA", "note": 'say "hi" \\ back\nslash {x}', "version": 3,
            "ratio": 0.5, "tags": ["a", "b"], "nested": {"ok": True, "none": None},
            "accent": "café"}
ITEM_META = {"version_id": 123, "path_like": "folder\\file.png", "empty": {}}


def test_meta_round_trip(rs, tmp_path):
    items = [{"path": "a.png", "label": "before", "meta": ITEM_META},
             {"path": "b.png", "label": "after"}]
    out = rs.write(_manifest(rs, tmp_path, items, meta=TOP_META), tmp_path / "review.rv")
    objs = parse_gto(out.read_text(encoding="utf-8"))

    def meta_of(obj):
        line = next(ln for ln in _props(objs, obj, "review") if ln.startswith("string meta"))
        return json.loads(_string_value(line))

    assert objs["rv"][0] == "RVSession"
    assert meta_of("rv") == TOP_META
    assert meta_of("sourceGroup000000_source") == ITEM_META
    assert meta_of("sourceGroup000001_source") == {}


NASTY_LABELS = ["my-track", 'say "hi"', "back\\slash", "new\nline", "{brace}", "café",
                "a : RVFileSource (1)"]


@pytest.mark.parametrize("layout", ["sequence", "wipe", "difference-inverted", "tile"])
def test_every_object_name_is_valid(rs, tmp_path, layout):
    items = [{"path": f"img_{i}.png", "label": lab,
              "annotations": [{"frame": 1, "text": lab}]} for i, lab in enumerate(NASTY_LABELS)]
    text = rs.build(_manifest(rs, tmp_path, items, layout=layout, title='t "x"',
                              stereo="pair")).text()
    names = [OBJ_LINE.match(ln).group(1) for ln in text.splitlines() if OBJ_LINE.match(ln)]
    assert len(names) == sum(1 for ln in text.splitlines() if ln == "{")
    assert len(names) == len(set(names))
    for n in names:
        assert NAME_RE.match(n) or n == "connections", n
    assert rs.structural_problems(text) == []


# ---------------------------------------------------------------------------
# structural_problems
# ---------------------------------------------------------------------------

GOOD = ("GTOa (4)\n\nrv : RVSession (4)\n{\n    session\n    {\n"
        "        int marks = [ 1 ]\n    }\n}\n")


def test_structural_problems_clean(rs):
    assert rs.structural_problems(GOOD) == []


def test_structural_problems_bad_array_syntax(rs):
    probs = rs.structural_problems(GOOD.replace("int marks", "int[] marks"))
    assert probs == ["'type[] name' is not GTO syntax; write 'type name = [ ... ]'"]


def test_structural_problems_missing_header(rs):
    probs = rs.structural_problems(GOOD.replace("GTOa (4)", "GTO (4)"))
    assert probs == ["first line must be 'GTOa (N)' for a text GTO file"]


@pytest.mark.parametrize("text", [GOOD.rstrip()[:-1], GOOD + "}\n", "GTOa (4)\n}\n{\n"])
def test_structural_problems_unbalanced_braces(rs, text):
    assert "unbalanced braces" in rs.structural_problems(text)


def test_structural_problems_braces_inside_strings_ignored(rs):
    text = GOOD.replace("int marks = [ 1 ]", 'string name = "open { and \\" }}"')
    assert rs.structural_problems(text) == []


@pytest.mark.parametrize("name", ["v", "my-node", "1abc"])
def test_structural_problems_bad_object_name(rs, name):
    probs = rs.structural_problems(GOOD.replace("rv : RVSession", f"{name} : RVSession"))
    assert probs == [f"object name {name!r} is not [A-Za-z_][A-Za-z0-9_]+ (two characters or more)"]


def test_structural_problems_connection_protocol_exempt(rs):
    assert rs.structural_problems(GOOD.replace("rv : RVSession", "c : connection")) == []


@pytest.mark.parametrize("layout", ["sequence", "wipe", "difference", "tile"])
def test_structural_problems_accept_writer_output(rs, tmp_path, layout):
    m = _three_stills(rs, tmp_path, layout=layout, fps=24, meta=TOP_META)
    m["items"][0]["annotations"] = [{"frame": 1, "text": "brace } and quote \""}]
    out = rs.write(m, tmp_path / "review.rv")
    assert rs.structural_problems(out.read_text(encoding="utf-8")) == []


def test_structural_problems_ignore_type_brackets_inside_strings(rs, tmp_path):
    items = [{"path": "a.png", "label": "parse int[] arrays"}]
    text = rs.build(_manifest(rs, tmp_path, items)).text()
    assert rs.structural_problems(text) == []


# ---------------------------------------------------------------------------
# summarise
# ---------------------------------------------------------------------------

def test_summarise_sequence(rs, tmp_path):
    out = rs.write(_three_stills(rs, tmp_path), tmp_path / "review.rv")
    s = rs.summarise(out)
    assert s["sources"] == 3
    assert s["viewNode"] == "review_sequence"
    assert s["marks"] == [1, 3]
    assert s["objects"][:3] == [("rv", "RVSession"), ("connections", "connection"),
                                ("review_sequence", "RVSequenceGroup")]
    assert ("sourceGroup000002_source", "RVFileSource") in s["objects"]


def test_summarise_stack_view_and_empty_marks(rs, tmp_path):
    out = rs.write(_three_stills(rs, tmp_path, layout="wipe"), tmp_path / "review.rv")
    s = rs.summarise(out)
    assert s["viewNode"] == "review_stack" and s["marks"] == []
    out = rs.write(_three_stills(rs, tmp_path), tmp_path / "none.rv", marks="none")
    assert rs.summarise(out)["marks"] == []


def test_summarise_binary_gto_is_none(rs, tmp_path):
    p = tmp_path / "binary.rv"
    p.write_bytes(b"\x9f\x02\x00\x00\x04\x00\x00\x00binary")
    assert rs.summarise(p) is None


# ---------------------------------------------------------------------------
# media helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("x__label__3.png", (3, 3)),
    ("shot_v2_0017.exr", (17, 17)),
    ("plain.png", (1, 1)),
    ("a.1001-1010#.png", (1001, 1010)),
    ("a.1-24@@@@.exr", (1, 24)),
    ("a.10-20x2%04d.dpx", (10, 20)),
    ("clip.mov", (1, None)),
    ("clip.MP4", (1, None)),
    ("clip.webm", (1, None)),
])
def test_media_frames(rs, tmp_path, name, expected):
    assert rs.media_frames(str(tmp_path / name)) == expected


@pytest.mark.parametrize("spec", ["a.#.png", "a.%04d.png", "a.@@@@.png"])
def test_media_frames_sequence_from_disk(rs, tmp_path, spec):
    for f in (7, 5, 12):
        (tmp_path / f"a.{f:04d}.png").write_bytes(b"")
    (tmp_path / "b.0001.png").write_bytes(b"")
    assert rs.media_frames(str(tmp_path / spec)) == (5, 12)


def test_media_frames_sequence_nothing_on_disk(rs, tmp_path):
    assert rs.media_frames(str(tmp_path / "a.#.png")) == (1, None)


def test_media_frames_sequence_in_folder_with_brackets(rs, tmp_path):
    folder = tmp_path / "take[1]"
    folder.mkdir()
    for f in (3, 4):
        (folder / f"a.{f:04d}.png").write_bytes(b"")
    assert rs.media_frames(str(folder / "a.#.png")) == (3, 4)


@pytest.mark.parametrize("item,expected", [
    ({"path": "x__label__3.png"}, 1),
    ({"path": "a.1001-1010#.png"}, 10),
    ({"path": "a.1001-1010#.png", "in": 1003, "out": 1005}, 3),
    ({"path": "a.1001-1010#.png", "in": 1008}, 3),
    ({"path": "clip.mov"}, None),
    ({"path": "clip.mov", "in": 5}, None),
    ({"path": "clip.mov", "in": 1, "out": 48}, 48),
    ({"path": "clip.mov", "out": 48}, 48),
    ({"path": "shotA_9.png", "in": 12}, 1),             # clamped to at least one frame
])
def test_item_length(rs, item, expected):
    assert rs.item_length(item) == expected


def test_offline_item_ranges(rs):
    items = [{"path": "x_3.png"}, {"path": "a.1001-1010#.png"},
             {"path": "clip.mov", "in": 1, "out": 4}]
    assert rs.offline_item_ranges(items) == [(1, 1), (2, 11), (12, 15)]
    assert rs.offline_item_ranges(items + [{"path": "clip.mov"}]) is None
    assert rs.offline_item_ranges([]) == []


def test_movie_without_out_gives_no_marks(rs, tmp_path):
    items = [{"path": "a.mov", "label": "a", "group": "g1"},
             {"path": "b.mov", "label": "b", "group": "g2"}]
    m = _manifest(rs, tmp_path, items, groups=[{"id": "g1"}, {"id": "g2"}])
    assert rs.offline_item_ranges(m["items"]) is None
    seq = parse_gto(rs.build(m).text())["review_sequence"][2]
    assert seq["session"][0] == "int marks = [ ]"


@pytest.mark.parametrize("marks,ranges,groups,expected", [
    ("auto", [(1, 1), (2, 2), (3, 3)], False, []),              # stills: no marks
    ("auto", [(1, 10), (11, 20)], False, [1, 11]),               # long items: item starts
    ("auto", [(1, 10)], False, []),                              # a single item
    ("auto", [(1, 1), (2, 2), (3, 3)], True, [1, 3]),            # groups present
    ("groups", [(1, 1), (2, 2), (3, 3)], True, [1, 3]),
    ("none", [(1, 10), (11, 20)], False, []),
    ([5, 2, 5], None, False, [2, 5]),
    ("auto", None, True, []),                                    # unknown lengths
])
def test_resolve_marks(rs, marks, ranges, groups, expected):
    items = [{"group": "g1"}, {"group": "g1"}, {"group": "g2"}]
    manifest = {"items": items, "groups": [{"id": "g1"}, {"id": "g2"}] if groups else []}
    assert rs.resolve_marks(manifest, ranges, marks) == expected


# ---------------------------------------------------------------------------
# image_size
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext,kwargs", [
    ("png", {}),
    ("jpg", {}),
    ("jpg", {"progressive": True}),
    ("gif", {}),
    ("bmp", {}),
])
def test_image_size(rs, tmp_path, ext, kwargs):
    p = tmp_path / f"img.{ext}"
    Image.new("RGB", (8, 4), (200, 30, 30)).save(p, **kwargs)
    assert tuple(rs.image_size(p)) == (8, 4)


def test_image_size_unknown_or_missing(rs, tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("not an image")
    assert rs.image_size(txt) is None
    assert rs.image_size(tmp_path / "missing.png") is None
    trunc = tmp_path / "trunc.png"
    trunc.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    assert rs.image_size(trunc) is None


# ---------------------------------------------------------------------------
# check / render helpers
# ---------------------------------------------------------------------------

def test_check_structural_when_no_rv(rs, tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "rv_tool", lambda name, rv_bin=None: None)
    out = rs.write(_three_stills(rs, tmp_path), tmp_path / "review.rv")
    assert rs.check(out) == ([], "structural")
    bad = tmp_path / "bad.rv"
    bad.write_text(GOOD.replace("int marks", "int[] marks"), encoding="utf-8")
    probs, checker = rs.check(bad)
    assert checker == "structural" and len(probs) == 1


def test_check_missing_file(rs, tmp_path):
    missing = tmp_path / "nope.rv"
    assert rs.check(missing) == ([f"session not found: {missing}"], None)


def test_render_args(rs):
    assert rs.render_args("rvio", "s.rv", "out.mov", ["-t", "1-5"]) == \
        ["rvio", "s.rv", "-o", "out.mov", "-t", "1-5"]


@pytest.mark.parametrize("text,expected", [
    ("auto", "auto"), (" GROUPS ", "groups"), ("none", "none"),
    ("1,4,7", [1, 4, 7]), ("3,", [3]),
])
def test_marks_arg(rs, text, expected):
    assert rs._marks_arg(text) == expected


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

def _run(*args, cwd):
    return subprocess.run([sys.executable, str(RV_SESSION_SCRIPT), *map(str, args)],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


def _result(proc):
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _write_manifest(tmp_path, data):
    p = tmp_path / "review.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_cli_write_then_check(tmp_path):
    _png(tmp_path / "shotA_before.png")
    _png(tmp_path / "shotA_after.png")
    manifest = _write_manifest(tmp_path, {
        "schema_version": 1, "title": "cli", "items": [
            {"path": "shotA_before.png", "label": "before",
             "annotations": [{"frame": 1, "text": "look"}]},
            {"path": "shotA_after.png", "label": "after"}]})
    no_rv = tmp_path / "no_rv"
    no_rv.mkdir()
    w = _run("write", manifest, "-o", "out/review.rv", "--layout", "wipe", "--fps", "24",
             cwd=tmp_path)
    assert w.returncode == 0, w.stdout + w.stderr
    res = _result(w)
    assert res["ok"] is True and res["items"] == 2 and res["problems"] == []
    assert Path(res["session"]) == (tmp_path / "out" / "review.rv").resolve()
    text = (tmp_path / "out" / "review.rv").read_text(encoding="utf-8")
    assert 'string viewNode = "review_stack"' in text and "float fps = 24" in text

    c = _run("check", "out/review.rv", "--rv-bin", no_rv, cwd=tmp_path)
    assert c.returncode == 0, c.stdout + c.stderr
    res = _result(c)
    assert res == {"ok": True, "problems": [], "checker": "structural",
                   "session": str((tmp_path / "out" / "review.rv").resolve())}


def test_cli_check_default_rv_lookup(tmp_path):
    """Without --rv-bin: gtoinfo if RV is installed, else structural -- clean either way."""
    manifest = _write_manifest(tmp_path, {"schema_version": 1,
                                          "items": [{"path": "a.png"}, {"path": "b.png"}]})
    assert _run("write", manifest, "-o", "review.rv", cwd=tmp_path).returncode == 0
    c = _run("check", "review.rv", cwd=tmp_path)
    assert c.returncode == 0, c.stdout + c.stderr
    assert _result(c)["checker"] in ("gtoinfo", "structural")


def test_cli_write_frames_json_marks_groups(tmp_path):
    frames = _write_manifest(tmp_path, {
        "frames": ["f1.png", "f2.png", "f3.png"],
        "views": [{"frame": 1, "sheet": "sheetA.png"}, {"frame": 3, "sheet": "sheetB.png"}]})
    w = _run("write", frames, "-o", "review.rv", cwd=tmp_path)
    assert w.returncode == 0, w.stdout + w.stderr
    text = (tmp_path / "review.rv").read_text(encoding="utf-8")
    assert "int marks = [ 1 3 ]" in text
    w = _run("write", frames, "-o", "review.rv", "--marks", "2", cwd=tmp_path)
    assert "int marks = [ 2 ]" in (tmp_path / "review.rv").read_text(encoding="utf-8")


def test_cli_check_broken_file(tmp_path):
    bad = tmp_path / "bad.rv"
    bad.write_text(GOOD.replace("int marks", "int[] marks"), encoding="utf-8")
    no_rv = tmp_path / "no_rv"
    no_rv.mkdir()
    c = _run("check", bad, "--rv-bin", no_rv, cwd=tmp_path)
    assert c.returncode == 1
    res = _result(c)
    assert res["ok"] is False and res["checker"] == "structural"
    assert res["problems"] == ["'type[] name' is not GTO syntax; write 'type name = [ ... ]'"]


def test_cli_render_without_rvio_stops(tmp_path):
    manifest = _write_manifest(tmp_path, {"schema_version": 1, "items": [{"path": "a.png"}]})
    assert _run("write", manifest, "-o", "review.rv", cwd=tmp_path).returncode == 0
    no_rv = tmp_path / "no_rv"
    no_rv.mkdir()
    r = _run("render", "review.rv", "-o", "out/review.mov", "--rv-bin", no_rv, cwd=tmp_path)
    assert r.returncode == 1
    assert _result(r)["problems"] == ["rvio not found next to rv; pass --rv-bin <RV bin folder>"]
    assert not (tmp_path / "out").exists()


def test_cli_write_bad_manifest(tmp_path):
    w = _run("write", "missing.json", "-o", "review.rv", cwd=tmp_path)
    assert w.returncode == 1
    res = _result(w)
    assert res["ok"] is False
    assert res["problems"][0].startswith("manifest not found:")
    assert not (tmp_path / "review.rv").exists()
    manifest = _write_manifest(tmp_path, {"schema_version": 1, "items": []})
    res = _result(_run("write", manifest, "-o", "review.rv", cwd=tmp_path))
    assert "items: required, a non-empty list" in res["problems"]


def test_cli_bad_layout_is_argument_error(tmp_path):
    manifest = _write_manifest(tmp_path, {"schema_version": 1, "items": [{"path": "a.png"}]})
    assert _run("write", manifest, "-o", "r.rv", "--layout", "sideways",
                cwd=tmp_path).returncode == 2


# ---------------------------------------------------------------------------
# gtoinfo (real RV tool; skipped without RV / OpenRV)
# ---------------------------------------------------------------------------

def _gtoinfo(rv_bin):
    exe = rv_bin / ("gtoinfo.exe" if os.name == "nt" else "gtoinfo")
    if not exe.is_file():
        pytest.skip("gtoinfo not found next to rv")
    return exe


@pytest.mark.parametrize("layout", ["sequence", "wipe", "difference-inverted", "tile"])
def test_gtoinfo_accepts_written_session(rs, tmp_path, rv_bin, layout):
    gi = _gtoinfo(rv_bin)
    _png(tmp_path / "x__label__3.png")
    items = [
        {"path": "x__label__3.png", "label": 'shot "A"\nbefore', "fps": 12.5,
         "meta": ITEM_META,
         "annotations": [{"frame": 1, "text": 'edge "here"\\ {x}\nsecond line'},
                         {"frame": 1, "text": "same frame", "color": [0, 1, 0]}]},
        {"path": "plates/a.1001-1010#.png", "label": "after", "in": 1003, "out": 1005,
         "view": "left", "stereo_views": ["left", "right"]},
        {"path": ["eyes/left.png", "eyes/right.png"], "label": "pair"},
    ]
    m = _manifest(rs, tmp_path, items, layout=layout, meta=TOP_META, fps=24, stereo="pair",
                  title="café review")
    out = rs.write(m, tmp_path / "review.rv")
    assert rs.gtoinfo_problems(out, gi) == []


def test_gtoinfo_accepts_label_with_type_brackets(rs, tmp_path, rv_bin):
    """A label containing int[] stays valid GTO (the structural check ignores string values)."""
    gi = _gtoinfo(rv_bin)
    out = rs.write(_manifest(rs, tmp_path, [{"path": "a.png", "label": "parse int[] arrays"}]),
                   tmp_path / "review.rv")
    assert rs.gtoinfo_problems(out, gi) == []


def test_gtoinfo_reports_broken_file(rs, tmp_path, rv_bin):
    gi = _gtoinfo(rv_bin)
    bad = tmp_path / "bad.rv"
    bad.write_text(GOOD.replace("int marks", "int[] marks"), encoding="utf-8")
    assert rs.structural_problems(bad.read_text(encoding="utf-8"))
    probs = rs.gtoinfo_problems(bad, gi)
    assert probs
    assert all(p.startswith(("ERROR", "Error")) for p in probs)
