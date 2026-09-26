"""Tests for rvls/scripts/rvls_check.py.

rvls_check.py does `sys.path.insert(0, <its folder>)` then `import rv_tool` (which imports
rv_find the same way). The captured "rvls -l" text fixtures below are real OpenRV 3.1 output
(spacing kept exact); the expected facts alongside them were cross-checked by actually running
rvls_check.py --parse against these fixtures.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "rvls" / "scripts"
RVLS_CHECK_PATH = SCRIPTS_DIR / "rvls_check.py"
SIBLING_MODULE_NAMES = ("rv_tool", "rv_find")


def _load_rvls_check():
    saved_sys_path = list(sys.path)
    saved_modules = {name: sys.modules.pop(name, None) for name in SIBLING_MODULE_NAMES}
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec = importlib.util.spec_from_file_location("rvls_check_under_test", RVLS_CHECK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_sys_path
        for name, mod in saved_modules.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)


@pytest.fixture()
def rvls_check():
    return _load_rvls_check()


# --- captured "rvls" output, byte-for-byte (spacing matters for column parsing) ------------

PLAIN_RVLS = "gaps/plate.1-5,8-10#.png\ngaps/readme.txt\n"

LONG_RVLS = (
    "INFO: Read image info from gaps/plate.0002.png\n"
    "ERROR: No plugin found can read gaps/readme.txt\n"
    "    w x h    typ   #ch   fps   #fr   file\n"
    "   64 x 64    8i   3       0    10   gaps/plate.1-5,8-10#.png\n"
    "    0 x 0     11   0                 gaps/readme.txt\n"
)

LONG_RVLS_WITH_AUDIO = (
    "     w x h     typ   #ch   fps   #fr   #ach   file\n"
    "     0 x 0      11   0                        out/au/aac.mov\n"
    "   320 x 180    8i   3      24    24      1   out/au/mono44.mov\n"
    "                                          2   out/au/ripped.wav\n"
    "   320 x 180    8i   3      24    24      2   out/au/with_audio.mov\n"
    "    64 x 64     8i   3       0     3          pads/a b.1-3#.png\n"
    "    64 x 64     8i   3       0     4          pads/neg.-2-1@@@.png\n"
    "    64 x 64     8i   3       0    11          pads/np.1-3,10-11@.png\n"
    "    64 x 64     8i   3       0     3          pads/p5_1-3@@@@@.png\n"
    "    64 x 64     8i   3       0     1          pads/single.png\n"
    "    64 x 64     8i   3       0     3          pads/v001_1-3#.png\n"
    "    64 x 64     8i   3       0    30          step/s.1-9x2,20-22,30#.png\n"
    "    64 x 64     8i   3      24     9          step/x.mov\n"
    "   320 x 180   16f   8       0    24          out/st/pair.1001-1024#.exr\n"
)


def entries_by_spec(entries):
    return {e["spec"]: e for e in entries}


# --- describe() / parse_sequence_name --------------------------------------------------

class TestParseSequenceName:
    def test_plain_range_and_padding(self, rvls_check):
        seq = rvls_check.parse_sequence_name("plate.1-5,8-10#.png")
        assert seq["prefix"] == "plate."
        assert seq["suffix"] == ".png"
        assert seq["frames"] == [1, 2, 3, 4, 5, 8, 9, 10]
        assert seq["padding"] == 4

    def test_non_sequence_name_returns_none(self, rvls_check):
        assert rvls_check.parse_sequence_name("readme.txt") is None

    def test_at_padding(self, rvls_check):
        seq = rvls_check.parse_sequence_name("neg.-2-1@@@.png")
        assert seq["frames"] == [-2, -1, 0, 1]
        assert seq["padding"] == 3


class TestDescribe:
    def test_sequence_with_gap(self, rvls_check):
        e = rvls_check.describe("gaps/plate.1-5,8-10#.png")
        assert e["pattern"] == "gaps/plate.#.png"
        assert e["sequence"] is True
        assert (e["first"], e["last"], e["count"], e["span"]) == (1, 10, 8, 10)
        assert e["missing"] == [6, 7]
        assert e["padding"] == 4

    def test_non_sequence_file(self, rvls_check):
        e = rvls_check.describe("gaps/readme.txt")
        assert e["sequence"] is False
        assert (e["first"], e["last"]) == (None, None)
        assert e["count"] == 1


# --- parse_rvls_output: plain listing ---------------------------------------------------

class TestParsePlainOutput:
    def test_plain_listing(self, rvls_check):
        entries = entries_by_spec(rvls_check.parse_rvls_output(PLAIN_RVLS))
        plate = entries["gaps/plate.1-5,8-10#.png"]
        assert (plate["first"], plate["last"], plate["count"], plate["span"]) == (1, 10, 8, 10)
        assert plate["missing"] == [6, 7]
        assert plate["padding"] == 4
        readme = entries["gaps/readme.txt"]
        assert readme["sequence"] is False
        assert readme["count"] == 1


# --- parse_rvls_output: "-l" listing, with INFO/ERROR lines and a header ------------------

class TestParseLongOutput:
    def test_info_and_error_lines_are_skipped(self, rvls_check):
        entries = rvls_check.parse_rvls_output(LONG_RVLS)
        assert len(entries) == 2   # only the two data rows, not the INFO/ERROR lines

    def test_plate_row(self, rvls_check):
        plate = entries_by_spec(rvls_check.parse_rvls_output(LONG_RVLS))[
            "gaps/plate.1-5,8-10#.png"]
        assert plate["pattern"] == "gaps/plate.#.png"
        assert (plate["first"], plate["last"], plate["count"], plate["span"]) == (1, 10, 8, 10)
        assert plate["missing"] == [6, 7]
        assert plate["padding"] == 4
        assert (plate["width"], plate["height"]) == (64, 64)
        assert plate["type"] == "8i"
        assert plate["channels"] == 3
        assert plate["fps"] == 0.0
        assert plate["frames_reported"] == 10
        assert plate["audio_channels"] is None
        assert plate["readable"] is True

    def test_unreadable_file_row(self, rvls_check):
        readme = entries_by_spec(rvls_check.parse_rvls_output(LONG_RVLS))["gaps/readme.txt"]
        assert (readme["width"], readme["height"]) == (0, 0)
        assert readme["readable"] is False


class TestParseLongOutputWithAudioColumn:
    @pytest.fixture()
    def entries(self, rvls_check):
        return entries_by_spec(rvls_check.parse_rvls_output(LONG_RVLS_WITH_AUDIO))

    def test_aac_movie_unreadable(self, entries):
        e = entries["out/au/aac.mov"]
        assert e["readable"] is False

    def test_mono_audio_channel_count(self, entries):
        e = entries["out/au/mono44.mov"]
        assert e["audio_channels"] == 1
        assert e["count"] == 24

    def test_ripped_wav_is_audio_only(self, entries):
        e = entries["out/au/ripped.wav"]
        assert e["width"] is None
        assert e["audio_channels"] == 2
        assert e["readable"] is True

    def test_with_audio_movie(self, entries):
        e = entries["out/au/with_audio.mov"]
        assert e["audio_channels"] == 2
        assert e["count"] == 24

    def test_name_with_space_is_kept(self, entries):
        assert "pads/a b.1-3#.png" in entries
        e = entries["pads/a b.1-3#.png"]
        assert e["pattern"] == "pads/a b.#.png"
        assert (e["first"], e["last"], e["count"]) == (1, 3, 3)

    def test_negative_frame_range(self, entries):
        e = entries["pads/neg.-2-1@@@.png"]
        assert (e["first"], e["last"], e["count"]) == (-2, 1, 4)
        assert e["padding"] == 3

    def test_single_at_padding(self, entries):
        e = entries["pads/np.1-3,10-11@.png"]
        assert e["count"] == 5
        assert e["missing"] == [4, 5, 6, 7, 8, 9]
        assert e["padding"] == 1

    def test_five_at_padding(self, entries):
        e = entries["pads/p5_1-3@@@@@.png"]
        assert e["padding"] == 5

    def test_stepped_range(self, entries):
        e = entries["step/s.1-9x2,20-22,30#.png"]
        assert e["count"] == 9
        assert e["frames"] == [1, 3, 5, 7, 9, 20, 21, 22, 30]

    def test_movie_is_not_a_sequence_but_reports_frame_count(self, entries):
        e = entries["step/x.mov"]
        assert e["sequence"] is False
        assert e["count"] == 9

    def test_exr_pair_type_and_channels(self, entries):
        e = entries["out/st/pair.1001-1024#.exr"]
        assert e["type"] == "16f"
        assert e["channels"] == 8
        assert (e["first"], e["last"], e["count"]) == (1001, 1024, 24)

    def test_single_png_not_a_sequence(self, entries):
        e = entries["pads/single.png"]
        assert e["sequence"] is False
        assert e["count"] == 1


# --- spec_matcher -------------------------------------------------------------------------

class TestSpecMatcher:
    def test_matches_folder_and_predicate(self, rvls_check):
        folder, pred, want = rvls_check.spec_matcher("gaps/plate.#.png")
        assert folder == "gaps"
        entries = rvls_check.parse_rvls_output(LONG_RVLS)
        matched = [e for e in entries if pred(e)]
        assert len(matched) == 1
        assert matched[0]["spec"] == "gaps/plate.1-5,8-10#.png"

    def test_want_none_when_spec_has_no_range(self, rvls_check):
        _, _, want = rvls_check.spec_matcher("gaps/plate.#.png")
        assert want is None

    def test_want_set_when_spec_has_explicit_range(self, rvls_check):
        _, _, want = rvls_check.spec_matcher("gaps/plate.1-10#.png")
        assert want == list(range(1, 11))

    def test_no_frame_token_returns_none(self, rvls_check):
        assert rvls_check.spec_matcher("gaps/readme.txt") is None


# --- check() --------------------------------------------------------------------------

class Expectations:
    """Stand-in for argparse's Namespace with rvls_check's --expect-* options defaulted."""
    def __init__(self, **overrides):
        defaults = dict(expect_range=None, expect_count=None, no_gaps=False, expect_res=None,
                        expect_channels=None, readable=False, expect_type=None)
        defaults.update(overrides)
        for k, v in defaults.items():
            setattr(self, k, v)


class TestCheck:
    @pytest.fixture()
    def plate_entry(self, rvls_check):
        return [rvls_check.describe("gaps/plate.1-5,8-10#.png")]

    def test_no_expectations_no_problems(self, rvls_check, plate_entry):
        assert rvls_check.check(plate_entry, Expectations()) == []

    def test_no_gaps_reports_missing_frames(self, rvls_check, plate_entry):
        problems = rvls_check.check(plate_entry, Expectations(no_gaps=True))
        assert len(problems) == 1
        assert "missing frame" in problems[0]
        assert "6, 7" in problems[0]

    def test_expect_range_mismatch(self, rvls_check, plate_entry):
        problems = rvls_check.check(plate_entry, Expectations(expect_range="1-20"))
        assert "expected 1-20" in problems[0]

    def test_expect_range_match_no_problem(self, rvls_check, plate_entry):
        assert rvls_check.check(plate_entry, Expectations(expect_range="1-10")) == []

    def test_expect_count_mismatch(self, rvls_check, plate_entry):
        problems = rvls_check.check(plate_entry, Expectations(expect_count=99))
        assert "expected 99" in problems[0]

    def test_nothing_matched(self, rvls_check):
        assert rvls_check.check([], Expectations()) == ["nothing matched: no file or sequence "
                                                        "found"]

    def test_expect_res_mismatch(self, rvls_check):
        e = entries_by_spec(rvls_check.parse_rvls_output(LONG_RVLS))["gaps/plate.1-5,8-10#.png"]
        problems = rvls_check.check([e], Expectations(expect_res="100x100"))
        assert "expected 100x100" in problems[0]

    def test_readable_flag_catches_unreadable_entry(self, rvls_check):
        e = entries_by_spec(rvls_check.parse_rvls_output(LONG_RVLS))["gaps/readme.txt"]
        problems = rvls_check.check([e], Expectations(readable=True))
        assert "could not read it" in problems[0]


# --- main(): --parse and exit codes --------------------------------------------------

class TestMainParse:
    def test_parse_stdin(self, rvls_check, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(PLAIN_RVLS))
        rc = rvls_check.main(["--parse", "-"])
        assert rc == 0

    def test_ok_when_expectations_met(self, rvls_check, tmp_path, capsys):
        f = tmp_path / "listing.txt"
        f.write_text("    w x h    typ   #ch   fps   #fr   file\n"
                     "   64 x 64    8i   3       0    10   gaps/plate.1-5,8-10#.png\n")
        rc = rvls_check.main(["--parse", str(f), "--expect-range", "1-10", "--expect-res",
                              "64x64", "--expect-channels", "3", "--readable"])
        report = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert report["ok"] is True

    def test_no_gaps_fails(self, rvls_check, tmp_path, capsys):
        f = tmp_path / "listing.txt"
        f.write_text("    w x h    typ   #ch   fps   #fr   file\n"
                     "   64 x 64    8i   3       0    10   gaps/plate.1-5,8-10#.png\n")
        rc = rvls_check.main(["--parse", str(f), "--no-gaps"])
        assert rc == 1

    def test_expect_range_mismatch_fails(self, rvls_check, tmp_path):
        f = tmp_path / "listing.txt"
        f.write_text(PLAIN_RVLS)
        rc = rvls_check.main(["--parse", str(f), "--expect-range", "1-20"])
        assert rc == 1

    def test_expect_res_mismatch_fails(self, rvls_check, tmp_path):
        f = tmp_path / "listing.txt"
        f.write_text("    w x h    typ   #ch   fps   #fr   file\n"
                     "   64 x 64    8i   3       0    10   gaps/plate.1-5,8-10#.png\n")
        rc = rvls_check.main(["--parse", str(f), "--expect-res", "100x100"])
        assert rc == 1

    def test_readable_failure(self, rvls_check, tmp_path):
        f = tmp_path / "listing.txt"
        f.write_text(LONG_RVLS)
        rc = rvls_check.main(["--parse", str(f), "--readable"])
        assert rc == 1

    def test_needs_paths_or_parse(self, rvls_check):
        with pytest.raises(SystemExit) as exc:
            rvls_check.main([])
        assert exc.value.code == 2


# --- real-tool tests: only run when RV / OpenRV is actually installed ---------------------

def _rv_found():
    saved_sys_path = list(sys.path)
    saved = sys.modules.pop("rv_find", None)
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec = importlib.util.spec_from_file_location("rv_find_probe", SCRIPTS_DIR / "rv_find.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.locate("rvls", versions=False)["found"]
    finally:
        sys.path[:] = saved_sys_path
        if saved is not None:
            sys.modules["rv_find"] = saved
        else:
            sys.modules.pop("rv_find", None)


def _write_tiny_png(path, width=4, height=4, color=(255, 0, 0)):
    """A minimal valid RGB PNG using only zlib + struct (no Pillow), just so rvls has
    something real to read."""
    import struct
    import zlib

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = bytes(color) * width
    raw = b"".join(b"\x00" + row for _ in range(height))
    idat = zlib.compress(raw, 9)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)


@pytest.mark.skipif(not _rv_found(), reason="RV / OpenRV is not installed on this machine")
class TestRealRvls:
    def test_no_gaps_reports_the_missing_frame(self, rvls_check, tmp_path):
        _write_tiny_png(tmp_path / "plate.0001.png")
        _write_tiny_png(tmp_path / "plate.0002.png")
        _write_tiny_png(tmp_path / "plate.0004.png")
        rc = rvls_check.main([str(tmp_path), "--no-gaps"])
        assert rc == 1


@pytest.mark.skipif(not _rv_found(), reason="RV / OpenRV is not installed on this machine")
def test_real_rvio_writes_expected_frame_count(tmp_path):
    """rvio_cmd.py --run against a real rvio: three input frames should produce at least
    three output files."""
    import importlib.util as ilu
    scripts_dir = REPO_ROOT / "rvio" / "scripts"
    saved_sys_path = list(sys.path)
    saved_modules = {n: sys.modules.pop(n, None) for n in ("rv_tool", "rv_find")}
    sys.path.insert(0, str(scripts_dir))
    try:
        spec = ilu.spec_from_file_location("rvio_cmd_real", scripts_dir / "rvio_cmd.py")
        rvio_cmd = ilu.module_from_spec(spec)
        spec.loader.exec_module(rvio_cmd)
    finally:
        sys.path[:] = saved_sys_path
        for n, m in saved_modules.items():
            if m is not None:
                sys.modules[n] = m
            else:
                sys.modules.pop(n, None)

    _write_tiny_png(tmp_path / "plate.0001.png")
    _write_tiny_png(tmp_path / "plate.0002.png")
    _write_tiny_png(tmp_path / "plate.0003.png")
    import os
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        rc = rvio_cmd.main(["plate.#.png", "-o", "out.#.png", "--run"])
    finally:
        os.chdir(old_cwd)
    assert rc == 0
    assert len(list(tmp_path.glob("out.*.png"))) >= 3
