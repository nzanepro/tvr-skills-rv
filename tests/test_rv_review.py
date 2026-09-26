"""Tests for the pure functions of rv-review/scripts/rv_review.py.

Nothing here launches RV, calls rvpush, or talks to a network tag: review(),
_load(), _rvpush(), _launch_detached(), read_state() and main() (other than
via --help, or a --rv-bin error, through a subprocess) are never called.
Every find_rv() test passes an explicit env/root/home so the real machine's
own RV installs, PATH and registry cannot leak into the result.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RV_REVIEW_SCRIPT = REPO_ROOT / "rv-review" / "scripts" / "rv_review.py"

WIN, MAC, LINUX = "win32", "darwin", "linux"


def _make_pair(rr, folder, platform, name_index=0, executable=False):
    """Create empty rv/rvpush files for platform's exe_names in folder."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rv_names, push_names = rr.exe_names(platform)
    rv = folder / rv_names[name_index if name_index < len(rv_names) else 0]
    push = folder / push_names[0]
    rv.write_bytes(b"")
    push.write_bytes(b"")
    if executable:
        os.chmod(rv, 0o755)
        os.chmod(push, 0o755)
    return rv, push


# ---------------------------------------------------------------------------
# exe_names
# ---------------------------------------------------------------------------

def test_exe_names_windows(rr):
    assert rr.exe_names(WIN) == (("rv.exe",), ("rvpush.exe",))


def test_exe_names_macos(rr):
    assert rr.exe_names(MAC) == (("RV", "RV64", "rv"), ("rvpush",))


def test_exe_names_linux(rr):
    assert rr.exe_names(LINUX) == (("rv",), ("rvpush",))


# ---------------------------------------------------------------------------
# install_patterns
# ---------------------------------------------------------------------------

def test_install_patterns_windows_uses_both_program_files_vars(tmp_path, rr):
    pf1, pf2 = tmp_path / "pf1", tmp_path / "pf2"
    pats = rr.install_patterns(WIN, env={"ProgramFiles": str(pf1), "ProgramW6432": str(pf2)},
                               root=tmp_path)
    assert any(str(pf1) in p for p in pats)
    assert any(str(pf2) in p for p in pats)
    assert str(pf1 / "OpenRV" / "bin") in pats
    assert str(pf2 / "OpenRV" / "bin") in pats


def test_install_patterns_windows_dedups_identical_folders(tmp_path, rr):
    pf = tmp_path / "pf"
    pats = rr.install_patterns(WIN, env={"ProgramFiles": str(pf), "ProgramW6432": str(pf)},
                               root=tmp_path)
    assert len(pats) == len(set(pats))
    assert pats.count(str(pf / "OpenRV" / "bin")) == 1


def test_install_patterns_macos_includes_root_and_home_applications(tmp_path, rr):
    root, home = tmp_path / "root", tmp_path / "home"
    pats = rr.install_patterns(MAC, env={}, home=home, root=root)
    expected = [
        str(root / "Applications" / "RV*.app" / "Contents" / "MacOS"),
        str(root / "Applications" / "OpenRV*.app" / "Contents" / "MacOS"),
        str(home / "Applications" / "RV*.app" / "Contents" / "MacOS"),
        str(home / "Applications" / "OpenRV*.app" / "Contents" / "MacOS"),
    ]
    assert pats == expected
    assert all(tuple(Path(p).parts[-2:]) == ("Contents", "MacOS") for p in pats)


def test_install_patterns_linux_start_under_root(tmp_path, rr):
    pats = rr.install_patterns(LINUX, env={}, root=tmp_path)
    assert pats
    assert all(p.startswith(str(tmp_path)) for p in pats)


# ---------------------------------------------------------------------------
# find_rv: lookup order
# ---------------------------------------------------------------------------

def test_find_rv_rv_bin_beats_rv_bin_env(tmp_path, rr):
    winner = _make_pair(rr, tmp_path / "winner", LINUX)
    _make_pair(rr, tmp_path / "loser", LINUX)
    result = rr.find_rv(rv_bin=str(tmp_path / "winner"),
                        env={"RV_BIN": str(tmp_path / "loser")},
                        platform=LINUX, root=tmp_path, home=tmp_path / "home",
                        registry=lambda: None)
    assert result == winner


def test_find_rv_rv_bin_env_beats_rvpush_env(tmp_path, rr):
    winner = _make_pair(rr, tmp_path / "winner", LINUX)
    loser_rv, _ = _make_pair(rr, tmp_path / "loser", LINUX)
    result = rr.find_rv(env={"RV_BIN": str(tmp_path / "winner"),
                             "RVPUSH_RV_EXECUTABLE_PATH": str(loser_rv)},
                        platform=LINUX, root=tmp_path, home=tmp_path / "home",
                        registry=lambda: None)
    assert result == winner


@pytest.mark.parametrize("none_spelling", ["none", "None", "NONE"])
def test_find_rv_rvpush_env_ignored_when_none(tmp_path, rr, none_spelling):
    winner = _make_pair(rr, tmp_path / "winner", LINUX)
    result = rr.find_rv(env={"RVPUSH_RV_EXECUTABLE_PATH": none_spelling,
                             "RV_PATH": str(winner[0])},
                        platform=LINUX, root=tmp_path, home=tmp_path / "home",
                        registry=lambda: None)
    assert result == winner


def test_find_rv_rvpush_env_beats_rv_path_env(tmp_path, rr):
    winner_rv, winner_push = _make_pair(rr, tmp_path / "winner", LINUX)
    loser_rv, _ = _make_pair(rr, tmp_path / "loser", LINUX)
    result = rr.find_rv(env={"RVPUSH_RV_EXECUTABLE_PATH": str(winner_rv),
                             "RV_PATH": str(loser_rv)},
                        platform=LINUX, root=tmp_path, home=tmp_path / "home",
                        registry=lambda: None)
    assert result == (winner_rv, winner_push)


def test_find_rv_rv_path_env_beats_rv_app_env(tmp_path, rr):
    winner_rv, winner_push = _make_pair(rr, tmp_path / "winner", LINUX)
    loser_rv, _ = _make_pair(rr, tmp_path / "loser", LINUX)
    result = rr.find_rv(env={"RV_PATH": str(winner_rv), "RV_APP_RV": str(loser_rv)},
                        platform=LINUX, root=tmp_path, home=tmp_path / "home",
                        registry=lambda: None)
    assert result == (winner_rv, winner_push)


def test_find_rv_rv_app_env_beats_rv_home(tmp_path, rr):
    winner_rv, winner_push = _make_pair(rr, tmp_path / "winner", LINUX)
    loser_home = tmp_path / "loser_home"
    _make_pair(rr, loser_home / "bin", LINUX)
    result = rr.find_rv(env={"RV_APP_RV": str(winner_rv), "RV_HOME": str(loser_home)},
                        platform=LINUX, root=tmp_path, home=tmp_path / "home",
                        registry=lambda: None)
    assert result == (winner_rv, winner_push)


def test_find_rv_rv_home_uses_bin_subfolder(tmp_path, rr):
    home_root = tmp_path / "rvhome"
    winner = _make_pair(rr, home_root / "bin", LINUX)
    result = rr.find_rv(env={"RV_HOME": str(home_root)}, platform=LINUX, root=tmp_path,
                        home=tmp_path / "home", registry=lambda: None)
    assert result == winner


def test_find_rv_rv_home_app_bundle_uses_contents_macos(tmp_path, rr):
    home_root = tmp_path / "RV.app"
    winner = _make_pair(rr, home_root / "Contents" / "MacOS", MAC)
    result = rr.find_rv(env={"RV_HOME": str(home_root)}, platform=MAC, root=tmp_path,
                        home=tmp_path / "home", registry=lambda: None)
    assert result == winner


def test_find_rv_rv_home_beats_path(tmp_path, rr):
    winner = _make_pair(rr, tmp_path / "rvhome" / "bin", LINUX)
    loser_path_dir = tmp_path / "pathdir"
    _make_pair(rr, loser_path_dir, LINUX, executable=True)
    result = rr.find_rv(env={"RV_HOME": str(tmp_path / "rvhome"), "PATH": str(loser_path_dir)},
                        platform=LINUX, root=tmp_path, home=tmp_path / "home",
                        registry=lambda: None)
    assert result == winner


def test_find_rv_via_path_native_platform(tmp_path):
    """PATH lookup uses shutil.which, so it must run under the real sys.platform."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("rv_review", RV_REVIEW_SCRIPT)
    rr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rr)

    path_dir = tmp_path / "pathdir"
    winner = _make_pair(rr, path_dir, sys.platform, executable=True)
    result = rr.find_rv(env={"PATH": str(path_dir)}, root=tmp_path, home=tmp_path / "home",
                        registry=lambda: None)
    assert result == winner


def test_find_rv_path_beats_registry(tmp_path, rr):
    path_dir = tmp_path / "pathdir"
    winner = _make_pair(rr, path_dir, WIN, executable=True)
    registry_dir = tmp_path / "registry_install"
    _make_pair(rr, registry_dir, WIN)
    result = rr.find_rv(env={"PATH": str(path_dir), "ProgramFiles": str(tmp_path / "pf")},
                        platform=WIN, root=tmp_path, home=tmp_path / "home",
                        registry=lambda: str(registry_dir / "rv.exe"))
    assert result == winner


def test_find_rv_registry_beats_install_folders(tmp_path, rr):
    registry_dir = tmp_path / "registry_install"
    winner = _make_pair(rr, registry_dir, WIN)
    pf = tmp_path / "pf"
    _make_pair(rr, pf / "OpenRV" / "bin", WIN)
    result = rr.find_rv(env={"ProgramFiles": str(pf)}, platform=WIN, root=tmp_path,
                        home=tmp_path / "home", registry=lambda: str(registry_dir / "rv.exe"))
    assert result == winner


def test_find_rv_registry_not_consulted_on_linux(tmp_path, rr):
    registry_dir = tmp_path / "registry_install"
    _make_pair(rr, registry_dir, LINUX)
    with pytest.raises(rr.RvError):
        rr.find_rv(env={}, platform=LINUX, root=tmp_path, home=tmp_path / "home",
                   registry=lambda: str(registry_dir / "rv"))


def test_find_rv_install_folders_pick_newest_version_by_natural_sort(tmp_path, rr):
    pf = tmp_path / "pf"
    older = _make_pair(rr, pf / "OpenRV-2024.9" / "bin", WIN)
    newer = _make_pair(rr, pf / "OpenRV-2024.10" / "bin", WIN)
    result = rr.find_rv(env={"ProgramFiles": str(pf)}, platform=WIN, root=tmp_path,
                        home=tmp_path / "home", registry=lambda: None)
    assert result == newer
    assert result != older


def test_find_rv_rv_bin_as_the_executable_file_itself(tmp_path, rr):
    rv, push = _make_pair(rr, tmp_path / "install", LINUX)
    result = rr.find_rv(rv_bin=str(rv), env={}, platform=LINUX, root=tmp_path,
                        home=tmp_path / "home", registry=lambda: None)
    assert result == (rv, push)


def test_find_rv_wrong_rv_bin_raises_and_does_not_fall_through(tmp_path, rr):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    valid_path_dir = tmp_path / "pathdir"
    _make_pair(rr, valid_path_dir, LINUX, executable=True)
    with pytest.raises(rr.RvError, match="does not hold both"):
        rr.find_rv(rv_bin=str(empty_dir), env={"PATH": str(valid_path_dir)}, platform=LINUX,
                   root=tmp_path, home=tmp_path / "home", registry=lambda: None)


def test_find_rv_wrong_rv_bin_env_raises_and_does_not_fall_through(tmp_path, rr):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    valid_path_dir = tmp_path / "pathdir"
    _make_pair(rr, valid_path_dir, LINUX, executable=True)
    with pytest.raises(rr.RvError, match="does not hold both"):
        rr.find_rv(env={"RV_BIN": str(empty_dir), "PATH": str(valid_path_dir)}, platform=LINUX,
                   root=tmp_path, home=tmp_path / "home", registry=lambda: None)


def test_find_rv_nothing_found_raises(tmp_path, rr):
    with pytest.raises(rr.RvError, match="RV not found"):
        rr.find_rv(env={}, platform=LINUX, root=tmp_path, home=tmp_path / "home",
                   registry=lambda: None)


# ---------------------------------------------------------------------------
# is_sequence_spec / is_still
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("token", [
    "name.#.exr", "name.1001-1100#.exr", "name.@@@@.exr", "name.%04d.exr",
])
def test_is_sequence_spec_true(rr, token):
    assert rr.is_sequence_spec(token) is True


@pytest.mark.parametrize("token", ["shot.mov", "frame.exr", "plain_name"])
def test_is_sequence_spec_false(rr, token):
    assert rr.is_sequence_spec(token) is False


def test_is_still_movie_is_not_a_still(rr):
    assert rr.is_still("shot.mov") is False


def test_is_still_image_is_a_still(rr):
    assert rr.is_still("frame.exr") is True


def test_is_still_sequence_spec_is_not_a_still(rr):
    assert rr.is_still("name.#.exr") is False


# ---------------------------------------------------------------------------
# group_sources
# ---------------------------------------------------------------------------

def test_group_sources_plain_tokens(rr):
    assert rr.group_sources(["a.png", "b.png"]) == [["a.png"], ["b.png"]]


def test_group_sources_bracket_group(rr):
    assert rr.group_sources(["[", "left.exr", "right.exr", "]"]) == \
        [["[", "left.exr", "right.exr", "]"]]


def test_group_sources_per_source_options_inside_brackets(rr):
    tokens = ["[", "shot.mov", "-in", "10", "-out", "50", "]"]
    assert rr.group_sources(tokens) == [tokens]


def test_group_sources_mixed_plain_and_bracket(rr):
    tokens = ["before.png", "[", "left.exr", "right.exr", "]", "after.png"]
    assert rr.group_sources(tokens) == \
        [["before.png"], ["[", "left.exr", "right.exr", "]"], ["after.png"]]


def test_group_sources_nested_bracket_raises(rr):
    with pytest.raises(rr.RvError, match="nested"):
        rr.group_sources(["[", "[", "a.exr", "]", "]"])


def test_group_sources_stray_close_bracket_raises(rr):
    with pytest.raises(rr.RvError, match=r"without a matching '\['"):
        rr.group_sources(["a.exr", "]"])


def test_group_sources_unclosed_bracket_raises(rr):
    with pytest.raises(rr.RvError, match=r"without a matching '\]'"):
        rr.group_sources(["[", "a.exr"])


def test_group_sources_option_outside_brackets_raises(rr):
    with pytest.raises(rr.RvError, match="must sit inside"):
        rr.group_sources(["shot.mov", "-in", "10"])


# ---------------------------------------------------------------------------
# _group_files
# ---------------------------------------------------------------------------

def test_group_files_skips_brackets_and_option_values(rr):
    group = ["[", "a.mov", "-in", "10", "-out", "50", "]"]
    assert rr._group_files(group) == ["a.mov"]


def test_group_files_plain_group(rr):
    assert rr._group_files(["centre.png"]) == ["centre.png"]


# ---------------------------------------------------------------------------
# resolve_token / resolve_sources
# ---------------------------------------------------------------------------

def test_resolve_token_relative_file_becomes_absolute(tmp_path, rr):
    f = tmp_path / "shot.png"
    f.write_bytes(b"")
    assert rr.resolve_token("shot.png", cwd=tmp_path) == str(f.resolve())


def test_resolve_token_sequence_spec_keeps_pattern_with_absolute_folder(tmp_path, rr):
    got = rr.resolve_token("seq.#.exr", cwd=tmp_path)
    assert got == str(tmp_path.resolve() / "seq.#.exr")


def test_resolve_token_missing_file_raises(tmp_path, rr):
    with pytest.raises(rr.RvError, match="source not found"):
        rr.resolve_token("missing.png", cwd=tmp_path)


def test_resolve_token_missing_sequence_folder_raises(tmp_path, rr):
    with pytest.raises(rr.RvError, match="folder of sequence"):
        rr.resolve_token("nofolder/seq.#.exr", cwd=tmp_path)


def test_resolve_sources_makes_files_absolute(tmp_path, rr):
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    before.write_bytes(b"")
    after.write_bytes(b"")
    result = rr.resolve_sources(["before.png", "after.png"], cwd=tmp_path)
    assert result == [str(before.resolve()), str(after.resolve())]


def test_resolve_sources_brackets_and_option_values_pass_through_unchanged(tmp_path, rr):
    left = tmp_path / "left.exr"
    left.write_bytes(b"")
    tokens = ["[", "left.exr", "-in", "10", "]"]
    result = rr.resolve_sources(tokens, cwd=tmp_path)
    assert result == ["[", str(left.resolve()), "-in", "10", "]"]


# ---------------------------------------------------------------------------
# load_frames_json
# ---------------------------------------------------------------------------

def test_load_frames_json(tmp_path, rr):
    data = {
        "size": [100, 50],
        "frames": [str(tmp_path / "a.png"), str(tmp_path / "b.png")],
        "views": [{"frame": 1, "sheet": "s.png", "labels": ["before"]},
                  {"frame": 2, "sheet": "s.png", "labels": ["after"]}],
    }
    path = tmp_path / "frames.json"
    path.write_text(json.dumps(data))
    frames, marks = rr.load_frames_json(path)
    assert frames == data["frames"]
    assert marks == [1, 2]


def test_load_frames_json_missing_file_raises(tmp_path, rr):
    with pytest.raises(rr.RvError, match="frames.json not found"):
        rr.load_frames_json(tmp_path / "nope" / "frames.json")


# ---------------------------------------------------------------------------
# parse_marks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("", "auto"),
    ("auto", "auto"),
    ("none", []),
    ("1, 4,7", [1, 4, 7]),
])
def test_parse_marks(rr, text, expected):
    assert rr.parse_marks(text) == expected


def test_parse_marks_bad_input_raises(rr):
    with pytest.raises(rr.RvError):
        rr.parse_marks("not-numbers")


# ---------------------------------------------------------------------------
# auto_marks
# ---------------------------------------------------------------------------

def test_auto_marks_multi_frame_sources(rr):
    assert rr.auto_marks([1, 13, 21, 33]) == [1, 13, 21]


def test_auto_marks_all_one_frame_sources(rr):
    assert rr.auto_marks([1, 2, 3, 4]) == []


def test_auto_marks_single_source(rr):
    assert rr.auto_marks([1, 13]) == []


# ---------------------------------------------------------------------------
# expand_views
# ---------------------------------------------------------------------------

def test_expand_views_all_expands_multiview_keeps_single_view(rr):
    groups = [["a.exr"], ["b.exr"]]
    source_views = [["left", "right"], ["mono"]]
    tokens, assign = rr.expand_views(groups, source_views, "all")
    assert tokens == ["a.exr", "a.exr", "b.exr"]
    assert assign == ["left", "right", None]


def test_expand_views_named_list_keeps_order_and_skips_missing(rr):
    groups = [["a.exr"]]
    source_views = [["left", "right", "centre"]]
    tokens, assign = rr.expand_views(groups, source_views, ["right", "top", "left"])
    assert tokens == ["a.exr", "a.exr"]
    assert assign == ["right", "left"]


def test_expand_views_source_with_none_of_wanted_views_left_alone(rr):
    groups = [["a.exr"]]
    source_views = [["mono"]]
    tokens, assign = rr.expand_views(groups, source_views, ["left", "right"])
    assert tokens == ["a.exr"]
    assert assign == [None]


# ---------------------------------------------------------------------------
# post_commands
# ---------------------------------------------------------------------------

def test_post_commands_default(rr):
    s = rr.post_commands()
    assert "setViewNode('defaultSequence')" in s
    assert "'@RVDisplayStereo.stereo.type', ['off']" in s
    assert "setFPS(" not in s


def test_post_commands_wipe(rr):
    s = rr.post_commands(compare="wipe")
    assert "setViewNode('defaultStack')" in s
    assert "'defaultStack_stack.composite.type', ['over']" in s
    assert "wipeShown() !=" in s


def test_post_commands_difference(rr):
    s = rr.post_commands(compare="difference")
    assert "'defaultStack_stack.composite.type', ['difference']" in s
    assert "wipeShown() ==" in s


def test_post_commands_tile(rr):
    s = rr.post_commands(compare="tile")
    assert "setViewNode('defaultLayout')" in s


def test_post_commands_marks(rr):
    s = rr.post_commands(marks=[1, 5, 9])
    assert "markFrame(f, True) for f in [1,5,9]" in s


def test_post_commands_view_assign(rr):
    s = rr.post_commands(view_assign=["left", None, "right"])
    assert "request.imageComponent" in s
    assert "['view', v]" in s
    assert "reload()" in s


def test_post_commands_stereo_views(rr):
    s = rr.post_commands(stereo_views=("left", "right"))
    assert "request.stereoViews" in s
    assert "reload()" in s


def test_post_commands_latlong(rr):
    s = rr.post_commands(latlong=True)
    assert "LatLongViewer" in s
    assert "newNode('LatLongViewer'" in s


def test_post_commands_compiles_as_python(rr):
    s = rr.post_commands(compare="wipe", marks=[1, 5], fps=24, stereo="pair",
                         stereo_views=("left", "right"), swap_eyes=True,
                         view_assign=["left", None], latlong=True, info_strip=True)
    compile(s, "post", "exec")


# ---------------------------------------------------------------------------
# launch_args
# ---------------------------------------------------------------------------

def test_launch_args_has_tag_and_network(rr):
    args = rr.launch_args("rv", ["a.png"], "my-tag")
    assert "-network" in args
    assert "-networkTag" in args
    assert "my-tag" in args
    assert args[0] == "rv"


def test_launch_args_latlong_flags_come_before_network(rr):
    args = rr.launch_args("rv", ["a.exr"], "my-tag", latlong=True)
    assert "-flags" in args
    assert "ModeManagerPreload=lat_long_viewer" in args
    assert args.index("-flags") < args.index("-network")


# ---------------------------------------------------------------------------
# child_env
# ---------------------------------------------------------------------------

def test_child_env_disables_rvpush_launching_and_does_not_mutate_input(rr):
    original = {"PATH": "somewhere"}
    result = rr.child_env(original)
    assert result["RVPUSH_RV_EXECUTABLE_PATH"] == "none"
    assert result["PATH"] == "somewhere"
    assert "RVPUSH_RV_EXECUTABLE_PATH" not in original


# ---------------------------------------------------------------------------
# parse_state
# ---------------------------------------------------------------------------

def test_parse_state_sample(rr):
    text = "(1, 1, 32, [13, 1], [1, 13, 21, 33], 3, 'defaultSequence', 'RVSequenceGroup', ['off'], 24.0)"
    st = rr.parse_state(text)
    assert st["frames"] == 32
    assert st["marks"] == [1, 13]
    assert st["sourceStarts"] == [1, 13, 21]
    assert st["stereo"] == "off"
    assert st["sources"] == 3
    assert st["viewNodeType"] == "RVSequenceGroup"
    assert st["fps"] == 24.0


def test_parse_state_garbage_returns_none(rr):
    assert rr.parse_state("not a tuple at all") is None
    assert rr.parse_state("(1, 2, 3)") is None


# ---------------------------------------------------------------------------
# check_state
# ---------------------------------------------------------------------------

def _sample_state(**overrides):
    st = {"sources": 3, "frames": 10, "marks": [1, 5], "viewNodeType": "RVSequenceGroup",
          "stereo": "off"}
    st.update(overrides)
    return st


def test_check_state_matching_is_empty(rr):
    state = _sample_state()
    expected = {"sources": 3, "frames": 10, "marks": [1, 5], "viewNodeType": "RVSequenceGroup",
                "stereo": "off"}
    assert rr.check_state(state, expected) == []


def test_check_state_none_state(rr):
    assert rr.check_state(None, {"sources": 1}) == ["RV did not return its state"]


def test_check_state_sources_mismatch(rr):
    probs = rr.check_state(_sample_state(), {"sources": 5})
    assert any("sources" in p for p in probs)


def test_check_state_marks_mismatch(rr):
    probs = rr.check_state(_sample_state(), {"marks": [1, 2, 3]})
    assert any("marks" in p for p in probs)


def test_check_state_view_node_type_mismatch(rr):
    probs = rr.check_state(_sample_state(), {"viewNodeType": "RVLayoutGroup"})
    assert any("view" in p for p in probs)


def test_check_state_stereo_mismatch(rr):
    probs = rr.check_state(_sample_state(), {"stereo": "pair"})
    assert any("stereo" in p for p in probs)


# ---------------------------------------------------------------------------
# CLI, via subprocess (sys.executable only)
# ---------------------------------------------------------------------------

def test_cli_help_exits_0_and_documents_key_options():
    result = subprocess.run([sys.executable, str(RV_REVIEW_SCRIPT), "--help"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    out = result.stdout
    for text in ("--frames-json", "--compare", "--views", "--stereo", "--latlong",
                 "--state", "RV_BIN"):
        assert text in out


def test_cli_wrong_rv_bin_exits_1_with_rv_review_error(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = subprocess.run(
        [sys.executable, str(RV_REVIEW_SCRIPT), "--rv-bin", str(empty_dir), "x.png"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert result.stderr.startswith("rv_review:")
