"""Tests for rv_tool.py (identical copies in rvio/scripts, rvls/scripts, rvpkg/scripts).

rv_tool.py does `sys.path.insert(0, <its folder>)` then `import rv_find`. It is loaded here
by file path with importlib; the fixture makes sure that import resolves to the rv_find.py
sitting next to the copy under test, not one a different test file left in sys.modules.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "rvio" / "scripts"
RV_TOOL_PATH = SCRIPTS_DIR / "rv_tool.py"


def _load_rv_tool():
    saved_sys_path = list(sys.path)
    saved_rv_find = sys.modules.pop("rv_find", None)
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec = importlib.util.spec_from_file_location("rv_tool_under_test", RV_TOOL_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_sys_path
        if saved_rv_find is not None:
            sys.modules["rv_find"] = saved_rv_find
        else:
            sys.modules.pop("rv_find", None)


@pytest.fixture()
def rv_tool():
    return _load_rv_tool()


# --- error_lines / drop_progress ------------------------------------------------------

class TestErrorLines:
    def test_picks_error_prefixed_lines(self, rv_tool):
        text = "ERROR: foo\nok line\n  ERROR: bar  \n"
        assert rv_tool.error_lines(text) == ["ERROR: foo", "ERROR: bar"]

    def test_picks_no_matching_packages_found(self, rv_tool):
        assert rv_tool.error_lines("No matching packages found\n") == \
            ["No matching packages found"]

    def test_picks_exiting(self, rv_tool):
        assert rv_tool.error_lines("exiting\n") == ["exiting"]

    def test_ignores_unrelated_lines(self, rv_tool):
        assert rv_tool.error_lines("Writing frame 5\nall good\n") == []

    def test_none_text_is_safe(self, rv_tool):
        assert rv_tool.error_lines(None) == []


class TestDropProgress:
    def test_removes_writing_frame_lines(self, rv_tool):
        text = "Writing frame 5\nsomething\nWriting frame 6 done\nlast line"
        assert rv_tool.drop_progress(text) == "something\nlast line"

    def test_none_text_is_safe(self, rv_tool):
        assert rv_tool.drop_progress(None) == ""


# --- decide_exit -----------------------------------------------------------------------

class TestDecideExit:
    def test_nonzero_returncode_is_exit_1(self, rv_tool):
        code, message = rv_tool.decide_exit("rvio", 1, [])
        assert code == 1
        assert "exit code 1" in message

    def test_unsigned_wraparound_shows_signed_code(self, rv_tool):
        code, message = rv_tool.decide_exit("rvio", 4294967295, ["ERROR: x"])
        assert code == 1
        assert "exit code -1" in message
        assert "ERROR: x" in message

    def test_rvio_default_strict_errors_with_exit_0_becomes_3(self, rv_tool):
        code, _ = rv_tool.decide_exit("rvio", 0, ["ERROR: x"], strict=None)
        assert code == 3

    def test_rvls_default_not_strict_errors_with_exit_0_stays_0(self, rv_tool):
        code, message = rv_tool.decide_exit("rvls", 0, ["ERROR: x"], strict=None)
        assert code == 0
        assert "not fatal" in message

    def test_strict_true_forces_rvls_to_3(self, rv_tool):
        code, _ = rv_tool.decide_exit("rvls", 0, ["ERROR: x"], strict=True)
        assert code == 3

    def test_strict_false_lets_rvio_stay_0(self, rv_tool):
        code, _ = rv_tool.decide_exit("rvio", 0, ["ERROR: x"], strict=False)
        assert code == 0

    def test_no_errors_is_ok(self, rv_tool):
        code, message = rv_tool.decide_exit("rvio", 0, [])
        assert code == 0
        assert "finished OK" in message


# --- run() -------------------------------------------------------------------------------

class TestRun:
    """run() resolves the tool through rv_find.locate, so that is monkeypatched to point at
    the current Python interpreter, which is then given a small -c script to act as the
    "tool"."""

    def _patch_locate_to_python(self, rv_tool, tool="rvio"):
        rv_tool.rv_find.locate = lambda t, rv_bin=None, versions=False: {
            "found": True, "tools": {tool: sys.executable}}

    def test_error_lines_in_output_give_exit_3(self, rv_tool):
        self._patch_locate_to_python(rv_tool)
        res = rv_tool.run("rvio", ["-c", "import sys; print('ERROR: boom', file=sys.stderr)"])
        assert res["exit"] == 3
        assert res["error_lines"] == ["ERROR: boom"]
        assert res["returncode"] == 0

    def test_timeout_gives_exit_124(self, rv_tool):
        self._patch_locate_to_python(rv_tool)
        res = rv_tool.run("rvio", ["-c", "import time; time.sleep(5)"], timeout=0.5)
        assert res["exit"] == 124

    def test_tool_not_found_gives_exit_127(self, rv_tool):
        rv_tool.rv_find.locate = lambda t, rv_bin=None, versions=False: {
            "found": False, "tools": {}, "error": "rvio not found"}
        res = rv_tool.run("rvio", [])
        assert res["exit"] == 127
        assert res["message"] == "rvio not found"

    def test_clean_run_gives_exit_0(self, rv_tool):
        self._patch_locate_to_python(rv_tool)
        res = rv_tool.run("rvio", ["-c", "print('all good')"])
        assert res["exit"] == 0
        assert res["returncode"] == 0
        assert res["error_lines"] == []


# --- main() --------------------------------------------------------------------------

def test_main_refuses_gui_tool(rv_tool, capsys):
    rc = rv_tool.main(["rv"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "opens a window" in err


def test_main_json_prints_result_and_returns_its_exit(rv_tool, monkeypatch, capsys):
    rv_tool.rv_find.locate = lambda t, rv_bin=None, versions=False: {
        "found": True, "tools": {"rvio": sys.executable}}
    rc = rv_tool.main(["--json", "rvio", "--", "-c", "print('hi')"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"exit": 0' in out
