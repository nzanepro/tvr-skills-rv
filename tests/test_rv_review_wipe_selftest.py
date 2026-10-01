"""Tests for the wipe layout support in rv-review/scripts/rv_review.py and rv_session.py:
parse_state / check_state / expected_layout's composite, wipe and wipeBox fields, the
stack visibleBox post-command, window_title_command, expected_frame / _direction_ok, the
--selftest key-binding check, and the wipe stencil.visibleBox rv_session.py writes.

Nothing here launches RV, rvpush or rvio. The py-exec / py-eval-return strings the script
would send to RV are compiled and, where useful, run (or eval'd) against small fake "rv"
objects that only record or answer what they are told; --selftest is driven by
monkeypatching rr._eval / rr._rvpush with a tiny frame-tracking RV simulator.
"""
import json
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RV_REVIEW_SCRIPT = REPO_ROOT / "rv-review" / "scripts" / "rv_review.py"


# ---------------------------------------------------------------------------
# parse_state: composite / wipe / wipeBox
# ---------------------------------------------------------------------------

def test_parse_state_wipe_stack(rr):
    text = ("(1, 1, 1, [], [1, 2, 3], 2, 'defaultStack', 'RVStackGroup', ['off'], 1.0, "
            "['over'], True, [[0.0, 0.5, 0.0, 1.0], [0.0, 1.0, 0.0, 1.0]])")
    st = rr.parse_state(text)
    assert st["composite"] == "over"
    assert st["wipe"] is True
    assert st["wipeBox"] == [0.0, 0.5, 0.0, 1.0]
    assert "wipeBoxes" not in st


def test_parse_state_difference_stack(rr):
    text = ("(1, 1, 1, [], [1, 2, 3], 2, 'defaultStack', 'RVStackGroup', ['off'], 24.0, "
            "['difference'], False, [[0.0, 1.0, 0.0, 1.0], [0.0, 1.0, 0.0, 1.0]])")
    st = rr.parse_state(text)
    assert st["composite"] == "difference"
    assert st["wipe"] is False
    assert st["wipeBox"] == [0.0, 1.0, 0.0, 1.0]


def test_parse_state_sequence_view_ignores_stack_looking_fields(rr):
    # even a tuple that "looks like" a stack readout is ignored once viewNodeType says
    # sequence: composite / wipe / wipeBox always collapse to None off a stack
    text = ("(5, 1, 30, [], [1, 11, 21, 31], 3, 'defaultSequence', 'RVSequenceGroup', ['off'], "
            "24.0, ['over'], True, [[0.0, 0.5, 0.0, 1.0], [0.0, 1.0, 0.0, 1.0]])")
    st = rr.parse_state(text)
    assert st["composite"] is None
    assert st["wipe"] is None
    assert st["wipeBox"] is None


def test_parse_state_old_ten_element_tuple_has_none_wipe_fields(rr):
    text = "(1, 1, 32, [13, 1], [1, 13, 21, 33], 3, 'defaultSequence', 'RVSequenceGroup', ['off'], 24.0)"
    st = rr.parse_state(text)
    assert st["frames"] == 32
    assert st["composite"] is None
    assert st["wipe"] is None
    assert st["wipeBox"] is None


@pytest.mark.parametrize("n", [11, 12])
def test_parse_state_wrong_tuple_length_returns_none(rr, n):
    assert rr.parse_state(repr(tuple(range(n)))) is None


def test_parse_state_stack_empty_composite_and_boxes_are_none(rr):
    text = ("(1, 1, 1, [], [1], 1, 'defaultStack', 'RVStackGroup', ['off'], 24.0, [], True, [])")
    st = rr.parse_state(text)
    assert st["composite"] is None
    assert st["wipeBox"] is None


def test_parse_state_wipe_box_wrong_length_is_none(rr):
    text = ("(1, 1, 1, [], [1], 1, 'defaultStack', 'RVStackGroup', ['off'], 24.0, ['over'], "
            "True, [[0.0, 0.5, 0.0]])")
    st = rr.parse_state(text)
    assert st["wipeBox"] is None
    assert st["composite"] == "over"       # unrelated field is unaffected


# ---------------------------------------------------------------------------
# check_state: composite / wipe / wipeBox
# ---------------------------------------------------------------------------

def _stack_state(**overrides):
    st = {"sources": 2, "frames": 10, "marks": [], "viewNodeType": "RVStackGroup",
          "stereo": "off", "composite": None, "wipe": None, "wipeBox": None}
    st.update(overrides)
    return st


def test_check_state_wipe_matching_is_clean(rr):
    state = _stack_state(composite="over", wipe=True, wipeBox=[0.0, 0.5, 0.0, 1.0])
    expected = {"composite": "over", "wipe": True, "wipeBox": [0.0, 0.5, 0.0, 1.0]}
    assert rr.check_state(state, expected) == []


def test_check_state_composite_mismatch(rr):
    probs = rr.check_state(_stack_state(composite="difference"), {"composite": "over"})
    assert any("composite" in p for p in probs)


def test_check_state_wipe_off_when_expected_on(rr):
    probs = rr.check_state(_stack_state(wipe=False), {"wipe": True})
    assert any("wipes off" in p for p in probs)


def test_check_state_wipe_box_full_when_wipe_expected_mentions_edge(rr):
    probs = rr.check_state(_stack_state(wipeBox=[0.0, 1.0, 0.0, 1.0]),
                            {"wipeBox": [0.0, 0.5, 0.0, 1.0]})
    assert any("wipe edge" in p for p in probs)


def test_check_state_wipe_box_none_is_a_problem(rr):
    probs = rr.check_state(_stack_state(wipeBox=None), {"wipeBox": [0.0, 0.5, 0.0, 1.0]})
    assert probs


def test_check_state_wipe_box_tolerance(rr):
    close = rr.check_state(_stack_state(wipeBox=[0.0, 0.5004, 0.0, 1.0]),
                            {"wipeBox": [0.0, 0.5, 0.0, 1.0]})
    assert close == []
    far = rr.check_state(_stack_state(wipeBox=[0.0, 0.51, 0.0, 1.0]),
                          {"wipeBox": [0.0, 0.5, 0.0, 1.0]})
    assert far != []


def test_check_state_wipe_box_full_expectation_mentions_whole_image(rr):
    probs = rr.check_state(_stack_state(wipeBox=[0.0, 0.5, 0.0, 1.0]),
                            {"wipeBox": [0.0, 1.0, 0.0, 1.0]})
    assert any("whole" in p for p in probs)


# ---------------------------------------------------------------------------
# expected_layout
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("compare,view_type", [
    ("sequence", "RVSequenceGroup"), ("tile", "RVLayoutGroup"),
    ("wipe", "RVStackGroup"), ("difference", "RVStackGroup"),
    ("difference-inverted", "RVStackGroup"), ("over", "RVStackGroup"),
    ("replace", "RVStackGroup"),
])
def test_expected_layout_view_node_type_for_every_mode(rr, compare, view_type):
    assert compare in rr.COMPARE_MODES
    assert rr.expected_layout(compare)["viewNodeType"] == view_type


def test_expected_layout_sequence_has_no_stack_keys(rr):
    assert set(rr.expected_layout("sequence")) == {"viewNodeType"}


def test_expected_layout_tile_has_no_stack_keys(rr):
    assert set(rr.expected_layout("tile")) == {"viewNodeType"}


def test_expected_layout_wipe(rr):
    exp = rr.expected_layout("wipe")
    assert exp["composite"] == "over"
    assert exp["wipe"] is True
    assert exp["wipeBox"] == list(rr.WIPE_BOX)


@pytest.mark.parametrize("compare,composite", [
    ("difference", "difference"), ("difference-inverted", "-difference"),
    ("over", "over"), ("replace", "replace"),
])
def test_expected_layout_non_wipe_stacks_use_full_box(rr, compare, composite):
    exp = rr.expected_layout(compare)
    assert exp["composite"] == composite
    assert exp["wipe"] is False
    assert exp["wipeBox"] == list(rr.FULL_BOX)


def test_expected_layout_latlong_overrides_every_mode(rr):
    for compare in rr.COMPARE_MODES:
        assert rr.expected_layout(compare, latlong=True) == {"viewNodeType": "LatLongViewer"}


# ---------------------------------------------------------------------------
# post_commands: the stack visibleBox statement
# ---------------------------------------------------------------------------

def test_post_commands_wipe_box_ternary_literal(rr):
    s = rr.post_commands(compare="wipe")
    assert ".stencil.visibleBox" in s
    assert "[0.0, 0.5, 0.0, 1.0] if k == 0 else [0.0, 1.0, 0.0, 1.0]" in s


def test_post_commands_difference_box_is_full_for_both(rr):
    s = rr.post_commands(compare="difference")
    assert "[0.0, 1.0, 0.0, 1.0] if k == 0 else [0.0, 1.0, 0.0, 1.0]" in s


@pytest.mark.parametrize("compare", ["sequence", "tile"])
def test_post_commands_no_visible_box_for_non_stack_layouts(rr, compare):
    assert "visibleBox" not in rr.post_commands(compare=compare)


def test_post_commands_visible_box_comes_after_set_view_node(rr):
    s = rr.post_commands(compare="wipe")
    assert s.index("setViewNode('defaultStack')") < s.index("visibleBox")


def test_post_commands_all_compare_modes_compile(rr):
    for compare in rr.COMPARE_MODES:
        compile(rr.post_commands(compare=compare), "<post>", "exec")


class _FakeStackCommands:
    """Just enough of rv.commands to run the stack's visibleBox statement."""

    def __init__(self, connections, existing_props=(), fallback_nodes=(), current_frame=1):
        self._connections = connections
        self._existing = set(existing_props)
        self._fallback = list(fallback_nodes)
        self._frame = current_frame
        self.meta_evaluate_calls = 0
        self.set_props = []          # [(path, value, resize)]

    def nodeConnections(self, node, expand):
        assert node == "defaultStack"
        assert expand is False
        return self._connections

    def propertyExists(self, name):
        return name in self._existing

    def setFloatProperty(self, path, value, resize):
        self.set_props.append((path, list(value), resize))

    def metaEvaluateClosestByType(self, frame, typ):
        self.meta_evaluate_calls += 1
        assert typ == "RVTransform2D"
        return [{"node": n} for n in self._fallback]

    def frame(self):
        return self._frame


def _visible_box_statement(post_cmd_string):
    hits = [p for p in post_cmd_string.split("; ") if "visibleBox" in p]
    assert len(hits) == 1
    return hits[0]


def test_post_commands_wipe_visible_box_uses_existing_transform_names(rr):
    stmt = _visible_box_statement(rr.post_commands(compare="wipe"))
    compile(stmt, "<visiblebox>", "exec")
    fake = _FakeStackCommands(
        connections=(["sourceGroup000000", "sourceGroup000001"], ["viewGroup"]),
        existing_props={"defaultStack_t_sourceGroup000000.stencil.visibleBox",
                         "defaultStack_t_sourceGroup000001.stencil.visibleBox"})
    exec(compile(stmt, "<visiblebox>", "exec"), {"rv": types.SimpleNamespace(commands=fake)})
    assert fake.meta_evaluate_calls == 0
    assert fake.set_props == [
        ("defaultStack_t_sourceGroup000000.stencil.visibleBox", [0.0, 0.5, 0.0, 1.0], True),
        ("defaultStack_t_sourceGroup000001.stencil.visibleBox", [0.0, 1.0, 0.0, 1.0], True),
    ]


def test_post_commands_wipe_visible_box_falls_back_to_meta_evaluate(rr):
    stmt = _visible_box_statement(rr.post_commands(compare="wipe"))
    fake = _FakeStackCommands(
        connections=(["sourceGroup000000", "sourceGroup000001"], ["viewGroup"]),
        existing_props=(), fallback_nodes=["a_t", "b_t"])
    exec(compile(stmt, "<visiblebox>", "exec"), {"rv": types.SimpleNamespace(commands=fake)})
    assert fake.meta_evaluate_calls == 1
    assert [p for p, _, _ in fake.set_props] == \
        ["a_t.stencil.visibleBox", "b_t.stencil.visibleBox"]
    assert [v for _, v, _ in fake.set_props] == [[0.0, 0.5, 0.0, 1.0], [0.0, 1.0, 0.0, 1.0]]


def test_post_commands_difference_visible_box_is_full_box_for_both_inputs(rr):
    stmt = _visible_box_statement(rr.post_commands(compare="difference"))
    fake = _FakeStackCommands(
        connections=(["sourceGroup000000", "sourceGroup000001"], []),
        existing_props={"defaultStack_t_sourceGroup000000.stencil.visibleBox",
                         "defaultStack_t_sourceGroup000001.stencil.visibleBox"})
    exec(compile(stmt, "<visiblebox>", "exec"), {"rv": types.SimpleNamespace(commands=fake)})
    assert [v for _, v, _ in fake.set_props] == [[0.0, 1.0, 0.0, 1.0], [0.0, 1.0, 0.0, 1.0]]


# ---------------------------------------------------------------------------
# STATE_EXPR eval'd against a fake rv module
# ---------------------------------------------------------------------------

class _FakeStateCommands:
    CheckedMenuState = 2

    def __init__(self, view_node, node_type, string_props, float_props, edl, marks,
                 frame_start, frame_end, frame, fps_value, connections, property_exists):
        self._view_node = view_node
        self._node_type = node_type
        self._string_props = string_props
        self._float_props = float_props
        self._edl = list(edl)
        self._marks = list(marks)
        self._frame_start = frame_start
        self._frame_end = frame_end
        self._frame = frame
        self._fps = fps_value
        self._connections = connections
        self._property_exists = property_exists

    def frame(self):
        return self._frame

    def frameStart(self):
        return self._frame_start

    def frameEnd(self):
        return self._frame_end

    def markedFrames(self):
        return list(self._marks)

    def getIntProperty(self, name):
        return list(self._edl)

    def nodesOfType(self, typ):
        assert typ == "RVFileSource"
        return ["sourceGroup000000_source", "sourceGroup000001_source"]

    def viewNode(self):
        return self._view_node

    def nodeType(self, node):
        return self._node_type

    def getStringProperty(self, name):
        return list(self._string_props.get(name, []))

    def fps(self):
        return self._fps

    def propertyExists(self, name):
        return self._property_exists

    def getFloatProperty(self, name):
        return list(self._float_props[name])

    def nodeConnections(self, node, expand):
        return self._connections


class _FakeRuntime:
    def __init__(self, wipe_shown):
        self._wipe_shown = wipe_shown

    def eval(self, expr, modules):
        return self._wipe_shown


def test_state_expr_eval_stack_view_reads_wipe_fields(rr):
    string_props = {"@RVDisplayStereo.stereo.type": ["off"],
                     "defaultStack_stack.composite.type": ["over"]}
    float_props = {
        "defaultStack_t_sourceGroup000000.stencil.visibleBox": [0.0, 0.5, 0.0, 1.0],
        "defaultStack_t_sourceGroup000001.stencil.visibleBox": [0.0, 1.0, 0.0, 1.0],
    }
    commands = _FakeStateCommands(
        view_node="defaultStack", node_type="RVStackGroup", string_props=string_props,
        float_props=float_props, edl=[1, 6, 11], marks=[1, 6], frame_start=1, frame_end=10,
        frame=5, fps_value=24.0,
        connections=(["sourceGroup000000", "sourceGroup000001"], ["viewGroup"]),
        property_exists=True)
    fake_rv = types.SimpleNamespace(commands=commands, runtime=_FakeRuntime("2"))
    result = eval(rr.STATE_EXPR, {"rv": fake_rv})
    st = rr.parse_state(repr(result))
    assert st["composite"] == "over"
    assert st["wipe"] is True
    assert st["wipeBox"] == [0.0, 0.5, 0.0, 1.0]


def test_state_expr_eval_sequence_view_has_no_wipe_fields(rr):
    string_props = {"@RVDisplayStereo.stereo.type": ["off"]}
    commands = _FakeStateCommands(
        view_node="defaultSequence", node_type="RVSequenceGroup", string_props=string_props,
        float_props={}, edl=[1, 11, 21], marks=[], frame_start=1, frame_end=20, frame=1,
        fps_value=24.0, connections=([], []), property_exists=False)
    fake_rv = types.SimpleNamespace(commands=commands, runtime=_FakeRuntime("-1"))
    result = eval(rr.STATE_EXPR, {"rv": fake_rv})
    st = rr.parse_state(repr(result))
    assert st["viewNodeType"] == "RVSequenceGroup"
    assert st["composite"] is None
    assert st["wipe"] is None
    assert st["wipeBox"] is None


# ---------------------------------------------------------------------------
# window_title_command
# ---------------------------------------------------------------------------

class _FakeTitleCommands:
    def __init__(self):
        self.title = None

    def setWindowTitle(self, title):
        self.title = title


def _run_title_command(cmd):
    fake = _FakeTitleCommands()
    exec(compile(cmd, "<title>", "exec"), {"rv": types.SimpleNamespace(commands=fake)})
    return fake.title


def test_window_title_command_basic_label(rr):
    cmd = rr.window_title_command("still_a.png", "wipe")
    cmd.encode("ascii")
    assert _run_title_command(cmd) == "still_a.png -- wipe"


def test_window_title_command_unicode_label_is_ascii_escaped_but_round_trips(rr):
    label = "café v2"
    cmd = rr.window_title_command(label, "wipe")
    cmd.encode("ascii")            # raises ValueError if the command string is not pure ASCII
    assert _run_title_command(cmd) == "café v2 -- wipe"


def test_window_title_command_none_label_defaults_to_review(rr):
    cmd = rr.window_title_command(None, "wipe")
    assert _run_title_command(cmd) == "review -- wipe"


# ---------------------------------------------------------------------------
# expected_frame / _direction_ok
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,frame,want", [
    ("Right", 1, 2), ("Right", 5, 1), ("Left", 1, 5), ("Left", 3, 2),
])
def test_expected_frame_right_left_wrap_at_ends(rr, key, frame, want):
    assert rr.expected_frame(key, frame, 1, 5, [1, 4]) == want


@pytest.mark.parametrize("frame,want", [(1, 4), (2, 4), (4, 5), (5, 5)])
def test_expected_frame_alt_right_next_mark_or_last_frame(rr, frame, want):
    assert rr.expected_frame("Alt+Right", frame, 1, 5, [1, 4]) == want


@pytest.mark.parametrize("frame,want", [(5, 4), (4, 1), (3, 1), (1, 1)])
def test_expected_frame_alt_left_previous_mark_or_first_frame(rr, frame, want):
    assert rr.expected_frame("Alt+Left", frame, 1, 5, [1, 4]) == want


def test_expected_frame_single_mark(rr):
    assert rr.expected_frame("Alt+Left", 1, 1, 5, [3]) == 1
    assert rr.expected_frame("Alt+Right", 1, 1, 5, [3]) == 3


def test_expected_frame_no_marks_alt_keys_are_unpredictable(rr):
    assert rr.expected_frame("Alt+Right", 2, 1, 5, []) is None
    assert rr.expected_frame("Alt+Left", 2, 1, 5, []) is None
    assert isinstance(rr.expected_frame("Right", 2, 1, 5, []), int)
    assert isinstance(rr.expected_frame("Left", 2, 1, 5, []), int)


def test_expected_frame_in_out_range_no_wrap_at_upper_edge(rr):
    # inside in/out (2-4) at frame 4: upper == out (4) which is not the sequence end (5),
    # so Right steps past it without wrapping
    assert rr.expected_frame("Right", 4, 1, 5, [], in_point=2, out_point=4) == 5


def test_expected_frame_outside_in_out_wraps_the_whole_range(rr):
    assert rr.expected_frame("Right", 5, 1, 5, [], in_point=2, out_point=4) == 1


@pytest.mark.parametrize("key,before,after,ok", [
    ("Alt+Right", 3, 4, True), ("Alt+Right", 5, 5, True), ("Alt+Right", 4, 3, False),
    ("Alt+Left", 4, 3, True), ("Alt+Left", 1, 1, True), ("Alt+Left", 3, 4, False),
])
def test_direction_ok(rr, key, before, after, ok):
    assert rr._direction_ok(key, before, after, 1, 5) == ok


# ---------------------------------------------------------------------------
# selftest: a tiny frame-tracking RV simulator drives rr._eval / rr._rvpush
# ---------------------------------------------------------------------------

class _FakeSelftestRV:
    """Keeps a current frame and answers the expressions selftest() asks for, so
    rr._eval / rr._rvpush can be monkeypatched onto it without ever running RV.

    rr: the fresh rv_review module fixture, so the simulator uses the module's own
    KEY_EVENTS / expression constants and expected_frame() rather than a copy of them.
    """

    def __init__(self, rr, start=1, end=5, in_point=None, out_point=None, marks=(), frame=None,
                 playing=False, bindings=None, key_overrides=None, restore_ignored=False):
        self._rr = rr
        self.start = start
        self.end = end
        self.in_point = start if in_point is None else in_point
        self.out_point = end if out_point is None else out_point
        self.marks = sorted(set(int(m) for m in marks))
        self.frame = start if frame is None else frame
        self.playing = playing
        if bindings is None:
            bindings = [(event, f"do {event}") for _, event in rr.KEY_EVENTS]
        self.bindings = bindings
        self.key_overrides = key_overrides or {}
        self.restore_ignored = restore_ignored
        self.event_to_key = {event: key for key, event in rr.KEY_EVENTS}

    def eval(self, rvpush, tag, expr):
        if expr == self._rr.SELFTEST_STATE_EXPR:
            return (self.frame, self.start, self.end, self.in_point, self.out_point,
                    list(self.marks), self.playing)
        if expr == self._rr.BINDINGS_EXPR:
            return list(self.bindings)
        if expr == self._rr.FRAME_EXPR:
            return self.frame
        return None

    def rvpush(self, rvpush, tag, *args):
        if len(args) >= 2 and args[0] == "py-exec":
            self._exec(args[1])
        return 0, ""

    def _exec(self, cmd):
        if cmd == "rv.commands.stop(); rv.commands.setFrame(rv.commands.frameStart())":
            self.frame = self.start
            return
        m = re.match(r"^rv\.commands\.setFrame\((-?\d+)\)$", cmd)
        if m:
            if not self.restore_ignored:
                self.frame = int(m.group(1))
            return
        m = re.match(r"^rv\.commands\.sendInternalEvent\('([^']+)', '', ''\)$", cmd)
        if m:
            key = self.event_to_key.get(m.group(1))
            if key is not None:
                self._move(key)

    def _move(self, key):
        if key in self.key_overrides:
            self.key_overrides[key](self)
            return
        want = self._rr.expected_frame(key, self.frame, self.start, self.end, self.marks,
                                        self.in_point, self.out_point)
        if want is not None:
            self.frame = want
        elif key == "Alt+Right":
            self.frame = min(self.frame + 1, self.end)
        elif key == "Alt+Left":
            self.frame = max(self.frame - 1, self.start)


def _install_fake_selftest(rr, monkeypatch, sim):
    monkeypatch.setattr(rr, "_eval", lambda rvpush, tag, expr: sim.eval(rvpush, tag, expr))
    monkeypatch.setattr(rr, "_rvpush", lambda rvpush, tag, *args: sim.rvpush(rvpush, tag, *args))


def test_selftest_all_correct(rr, monkeypatch):
    sim = _FakeSelftestRV(rr, start=1, end=5, marks=[1, 4], frame=3)
    _install_fake_selftest(rr, monkeypatch, sim)
    result = rr.selftest("rvpush", "t")
    assert result["problems"] == []
    assert result["restored"] is True
    assert result["frame"] == 3
    assert [s["key"] for s in result["steps"]] == list(rr.SELFTEST_KEYS)
    assert all(s["ok"] for s in result["steps"])
    for prev, cur in zip(result["steps"], result["steps"][1:]):
        assert cur["from"] == prev["to"]
    for key, event in rr.KEY_EVENTS:
        assert result["bindings"][key] == {"event": event, "action": f"do {event}"}
    json.dumps(result)               # JSON-serialisable


def test_selftest_no_marks_alt_steps_are_unpredictable_but_ok(rr, monkeypatch):
    sim = _FakeSelftestRV(rr, start=1, end=5, marks=[], frame=1)
    _install_fake_selftest(rr, monkeypatch, sim)
    result = rr.selftest("rvpush", "t")
    alt_steps = [s for s in result["steps"] if s["key"].startswith("Alt+")]
    assert alt_steps
    assert all(s["expected"] is None and s["ok"] for s in alt_steps)
    assert result["problems"] == []


def test_selftest_missing_binding_reports_problem(rr, monkeypatch):
    events = [e for _, e in rr.KEY_EVENTS if e != "key-down--alt--right"]
    sim = _FakeSelftestRV(rr, start=1, end=5, marks=[1, 4], frame=1,
                           bindings=[(e, f"do {e}") for e in events])
    _install_fake_selftest(rr, monkeypatch, sim)
    result = rr.selftest("rvpush", "t")
    assert any("Alt+Right" in p for p in result["problems"])
    assert result["bindings"]["Alt+Right"]["action"] is None


def test_selftest_broken_binding_is_reported_and_that_step_fails(rr, monkeypatch):
    sim = _FakeSelftestRV(rr, start=1, end=5, marks=[1, 4], frame=2,
                           key_overrides={"Right": lambda s: None})
    _install_fake_selftest(rr, monkeypatch, sim)
    result = rr.selftest("rvpush", "t")
    right_steps = [s for s in result["steps"] if s["key"] == "Right"]
    assert right_steps and all(not s["ok"] for s in right_steps)
    assert any("Right" in p and "moved frame" in p for p in result["problems"])


def test_selftest_restore_failure_is_reported(rr, monkeypatch):
    sim = _FakeSelftestRV(rr, start=1, end=5, marks=[1, 4], frame=3, restore_ignored=True)
    _install_fake_selftest(rr, monkeypatch, sim)
    result = rr.selftest("rvpush", "t")
    assert result["restored"] is False
    assert any("could not go back" in p for p in result["problems"])


def test_selftest_no_rv_answering_raises(rr, monkeypatch):
    monkeypatch.setattr(rr, "_eval", lambda rvpush, tag, expr: None)
    monkeypatch.setattr(rr, "_rvpush", lambda *a, **k: (0, ""))
    with pytest.raises(rr.RvError, match="no RV with tag 'gone'"):
        rr.selftest("rvpush", "gone")


def test_selftest_single_frame_window_raises(rr, monkeypatch):
    sim = _FakeSelftestRV(rr, start=1, end=1, marks=[], frame=1)
    _install_fake_selftest(rr, monkeypatch, sim)
    with pytest.raises(rr.RvError, match="single frame"):
        rr.selftest("rvpush", "t")


# ---------------------------------------------------------------------------
# main(["--selftest", ...])
# ---------------------------------------------------------------------------

def test_main_selftest_all_ok_prints_one_json_line_and_exits_0(rr, monkeypatch, capsys):
    sim = _FakeSelftestRV(rr, start=1, end=5, marks=[1, 4], frame=1)
    _install_fake_selftest(rr, monkeypatch, sim)
    monkeypatch.setattr(rr, "find_rv", lambda *a, **k: ("rv", "rvpush"))
    code = rr.main(["--selftest", "--tag", "t"])
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    res = json.loads(lines[0])
    assert res["schema"] == "rv-review.result"
    assert res["action"] == "selftest"
    assert res["ok"] is True
    assert code == 0 == rr.EXIT_OK


def test_main_selftest_broken_binding_exits_3(rr, monkeypatch, capsys):
    sim = _FakeSelftestRV(rr, start=1, end=5, marks=[1, 4], frame=2,
                           key_overrides={"Right": lambda s: None})
    _install_fake_selftest(rr, monkeypatch, sim)
    monkeypatch.setattr(rr, "find_rv", lambda *a, **k: ("rv", "rvpush"))
    code = rr.main(["--selftest", "--tag", "t"])
    lines = capsys.readouterr().out.strip().splitlines()
    res = json.loads(lines[0])
    assert res["ok"] is False
    assert code == 3 == rr.EXIT_MISMATCH


# ---------------------------------------------------------------------------
# CLI help documents --selftest
# ---------------------------------------------------------------------------

def test_cli_help_documents_selftest():
    result = subprocess.run([sys.executable, str(RV_REVIEW_SCRIPT), "--help"],
                             capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert "--selftest" in result.stdout


# ---------------------------------------------------------------------------
# rv_session.py: the wipe stencil.visibleBox
# ---------------------------------------------------------------------------

def _rs_manifest(rs, tmp_path, items, **top):
    for it in items:
        p = tmp_path / it["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(b"")
    return rs.rm.normalise({"schema_version": 1, "items": items, **top}, base=tmp_path)


def _two_stills():
    return [{"path": "before.png", "label": "before"}, {"path": "after.png", "label": "after"}]


def test_rv_session_wipe_layout_has_visible_box_on_top_source(rs, tmp_path):
    m = _rs_manifest(rs, tmp_path, _two_stills(), layout="wipe")
    text = rs.build(m).text()
    assert "review_stack_t_sourceGroup000000 : RVTransform2D" in text
    assert "float visibleBox = [ 0 0.5 0 1 ]" in text
    assert rs.structural_problems(text) == []


@pytest.mark.parametrize("layout", ["difference", "over", "sequence", "tile"])
def test_rv_session_non_wipe_layouts_have_no_visible_box(rs, tmp_path, layout):
    m = _rs_manifest(rs, tmp_path, _two_stills(), layout=layout)
    text = rs.build(m).text()
    assert "visibleBox" not in text
    assert rs.structural_problems(text) == []


def test_rv_session_write_wipe_session_is_structurally_clean(rs, tmp_path):
    m = _rs_manifest(rs, tmp_path, _two_stills(), layout="wipe")
    out = rs.write(m, tmp_path / "wipe.rv")
    text = out.read_text(encoding="utf-8")
    assert "float visibleBox = [ 0 0.5 0 1 ]" in text
    assert rs.structural_problems(text) == []
