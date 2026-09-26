"""Tests for rvio/scripts/rvio_cmd.py.

rvio_cmd.py is a standalone script that does `sys.path.insert(0, <its folder>)` then
`import rv_tool` (which in turn imports rv_find the same way). It is loaded here by file
path with importlib; the fixture makes sure that import resolves to the rv_tool.py sitting
next to rvio_cmd.py, not a same-named module a different test file left in sys.modules.
"""
import importlib.util
import json
import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "rvio" / "scripts"
RVIO_CMD_PATH = SCRIPTS_DIR / "rvio_cmd.py"
SIBLING_MODULE_NAMES = ("rv_tool", "rv_find")


def _load_rvio_cmd():
    saved_sys_path = list(sys.path)
    saved_modules = {name: sys.modules.pop(name, None) for name in SIBLING_MODULE_NAMES}
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec = importlib.util.spec_from_file_location("rvio_cmd_under_test", RVIO_CMD_PATH)
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
def rvio_cmd():
    """A fresh import of rvio_cmd.py, with its own rv_tool / rv_find siblings attached."""
    return _load_rvio_cmd()


def touch(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


# --- sequence notation -------------------------------------------------------------------

class TestSplitSequence:
    @pytest.mark.parametrize("spec,expected", [
        ("plate.#.exr", ("plate.", None, "#", ".exr")),
        ("plate.####.exr", ("plate.", None, "####", ".exr")),
        ("plate.@@@.exr", ("plate.", None, "@@@", ".exr")),
        ("plate.@.exr", ("plate.", None, "@", ".exr")),
        ("plate.%04d.exr", ("plate.", None, "%04d", ".exr")),
        ("plate.%d.exr", ("plate.", None, "%d", ".exr")),
        ("plate.1001-1100#.exr", ("plate.", "1001-1100", "#", ".exr")),
        ("plate.1-5,8-10#.exr", ("plate.", "1-5,8-10", "#", ".exr")),
        ("plate.1-9x2#.exr", ("plate.", "1-9x2", "#", ".exr")),
        ("plate.-2-1#.exr", ("plate.", "-2-1", "#", ".exr")),
        ("plate.-100--98#.exr", ("plate.", "-100--98", "#", ".exr")),
    ])
    def test_recognised_forms(self, rvio_cmd, spec, expected):
        assert rvio_cmd.split_sequence(spec) == expected

    def test_no_sequence_token(self, rvio_cmd):
        assert rvio_cmd.split_sequence("plain.exr") is None

    def test_is_sequence(self, rvio_cmd):
        assert rvio_cmd.is_sequence("plate.#.exr") is True
        assert rvio_cmd.is_sequence("plate.exr") is False


class TestPadding:
    @pytest.mark.parametrize("token,digits", [
        ("#", 4), ("####", 4), ("@@@", 3), ("@", 1), ("%04d", 4), ("%d", 0), ("%05d", 5),
    ])
    def test_padding(self, rvio_cmd, token, digits):
        assert rvio_cmd.padding(token) == digits


class TestParseRanges:
    @pytest.mark.parametrize("text,frames", [
        ("1-5,8-10", [1, 2, 3, 4, 5, 8, 9, 10]),
        ("1-9x2", [1, 3, 5, 7, 9]),
        ("-2-1", [-2, -1, 0, 1]),
        ("-100--98", [-100, -99, -98]),
        ("1001-1100", list(range(1001, 1101))),
        ("5", [5]),
    ])
    def test_parse_ranges(self, rvio_cmd, text, frames):
        assert rvio_cmd.parse_ranges(text) == frames

    def test_bad_range_raises(self, rvio_cmd):
        with pytest.raises(rvio_cmd.CheckError):
            rvio_cmd.parse_ranges("not-a-range")


class TestFramesOnDisk:
    def test_basic_listing(self, rvio_cmd, tmp_path):
        touch(tmp_path / "plate.0001.exr")
        touch(tmp_path / "plate.0002.exr")
        touch(tmp_path / "plate.0010.exr")
        found = rvio_cmd.frames_on_disk(str(tmp_path / "plate.#.exr"))
        assert found == [1, 2, 10]

    def test_wrong_padding_is_excluded(self, rvio_cmd, tmp_path):
        touch(tmp_path / "plate.0001.exr")
        touch(tmp_path / "plate.5.exr")     # one digit; '#' needs 4+
        found = rvio_cmd.frames_on_disk(str(tmp_path / "plate.#.exr"))
        assert found == [1]

    def test_range_filters_files_on_disk(self, rvio_cmd, tmp_path):
        touch(tmp_path / "plate.0001.exr")
        touch(tmp_path / "plate.0002.exr")
        touch(tmp_path / "plate.0010.exr")
        found = rvio_cmd.frames_on_disk(str(tmp_path / "plate.1-5#.exr"))
        assert found == [1, 2]

    def test_missing_folder_returns_empty(self, rvio_cmd, tmp_path):
        found = rvio_cmd.frames_on_disk(str(tmp_path / "nope" / "plate.#.exr"))
        assert found == []


# --- build() ------------------------------------------------------------------------------

class Args:
    """A stand-in for argparse's Namespace with every rvio_cmd option defaulted to None/False,
    so tests only need to set the fields they care about."""
    _DEFAULTS = dict(
        inputs=[], output=None, range=None, fps=None, outfps=None, range_start=None,
        range_offset=None, cut_in=None, cut_out=None, resize=None, scale=None, outres=None,
        crop=None, uncrop=None, pa=None, outpa=None, in_colour=None, in_gamma=None,
        out_colour=None, out_gamma=None, exposure=None, flut=None, llut=None, dlut=None,
        codec=None, exr_compression=None, quality=None, outformat=None, outrgb=False,
        outchannelmap=None, outstereo=None, outparam=None, allow_codec=False, audio=None,
        audio_offset=None, no_movie_audio=False, audiocodec=None, audiorate=None,
        audiochannels=None, comment=None, copyright=None, slate=None, leader_frames=None,
        frameburn=None, watermark=None, matte=None, bug=None, threads=None, verbose=False,
        extra=None, mkdir=False,
    )

    def __init__(self, **overrides):
        for key, value in self._DEFAULTS.items():
            setattr(self, key, value)
        for key, value in overrides.items():
            setattr(self, key, value)


def build_argv(rvio_cmd, **overrides):
    """(argv, warnings) from build() for a minimal single-input args object."""
    overrides.setdefault("inputs", ["plate.#.exr"])
    overrides.setdefault("output", "out.mov")
    a = Args(**overrides)
    return rvio_cmd.build(a)


class TestBuildOrderAndBrackets:
    def test_order_sources_globals_extras_leaders_o_outparams(self, rvio_cmd):
        argv, _ = build_argv(rvio_cmd, fps=24.0, slate=["Studio", "Shot=sh010"],
                             extra=["-rthreads", "4"], outparam=["timecode=01:00:00:00"])
        assert argv == (
            ["plate.#.exr", "-fps", "24.0"]
            + ["-rthreads", "4"]
            + ["-leader", "simpleslate", "Studio", "Shot=sh010"]
            + ["-o", "out.mov", "-outparams", "timecode=01:00:00:00"]
        )

    def test_per_source_options_wrap_source_in_brackets(self, rvio_cmd):
        argv, _ = build_argv(rvio_cmd, crop=[0, 0, 10, 10], pa=1.5, range_start=5,
                             llut="look.cube")
        assert argv[:12] == ["[", "-crop", "0", "0", "10", "10", "-pa", "1.5", "-llut",
                             "look.cube", "-rs", "5"]
        assert argv[12:15] == ["plate.#.exr", "]", "-o"]

    def test_no_per_source_options_no_brackets(self, rvio_cmd):
        argv, _ = build_argv(rvio_cmd)
        assert argv[0] == "plate.#.exr"
        assert "[" not in argv

    def test_audio_wraps_first_source(self, rvio_cmd):
        a = Args(inputs=["plate.#.exr"], output="out.mov", audio="sound.wav")
        argv, _ = rvio_cmd.build(a)
        assert argv[:4] == ["[", "plate.#.exr", "sound.wav", "]"]

    def test_slate_fields_with_spaces_stay_single_argv_items(self, rvio_cmd):
        argv, _ = build_argv(rvio_cmd, slate=["My Studio", "Shot=sh 010"])
        assert "My Studio" in argv
        assert "Shot=sh 010" in argv

    def test_frameburn_no_values_uses_defaults(self, rvio_cmd):
        argv, _ = build_argv(rvio_cmd, frameburn=[])
        i = argv.index("-overlay")
        assert argv[i:i + 5] == ["-overlay", "frameburn", "0.4", "1.0", "30"]

    def test_frameburn_absent_by_default(self, rvio_cmd):
        argv, _ = build_argv(rvio_cmd)
        assert "-overlay" not in argv


class TestColourFlags:
    @pytest.mark.parametrize("choice,flag", [("srgb", "-insrgb"), ("log", "-inlog"),
                                             ("709", "-in709"), ("linear", None)])
    def test_in_colour(self, rvio_cmd, choice, flag):
        argv, _ = build_argv(rvio_cmd, in_colour=choice)
        if flag:
            assert flag in argv
        else:
            assert not any(f.startswith("-in") for f in argv)

    @pytest.mark.parametrize("choice,flag", [("srgb", "-outsrgb"), ("log", "-outlog"),
                                             ("aces", "-outaces")])
    def test_out_colour(self, rvio_cmd, choice, flag):
        argv, _ = build_argv(rvio_cmd, out_colour=choice)
        assert flag in argv


class TestCodecHandling:
    def test_exr_compression_lowercase_becomes_upper_codec_flag(self, rvio_cmd):
        argv, _ = build_argv(rvio_cmd, output="out.#.exr", exr_compression="dwab")
        i = argv.index("-codec")
        assert argv[i + 1] == "DWAB"

    def test_codec_and_exr_compression_both_rejected(self, rvio_cmd):
        with pytest.raises(rvio_cmd.CheckError, match="not both"):
            build_argv(rvio_cmd, output="out.#.exr", codec="PIZ", exr_compression="dwab")

    def test_unknown_exr_compression_rejected(self, rvio_cmd):
        with pytest.raises(rvio_cmd.CheckError, match="unknown"):
            build_argv(rvio_cmd, output="out.#.exr", exr_compression="FOO")

    @pytest.mark.parametrize("codec", ["libx264", "h264", "hevc"])
    def test_unavailable_movie_codec_rejected(self, rvio_cmd, codec):
        with pytest.raises(rvio_cmd.CheckError, match="cannot write"):
            build_argv(rvio_cmd, codec=codec)

    def test_allow_codec_overrides_rejection(self, rvio_cmd):
        argv, warnings = build_argv(rvio_cmd, codec="libx264", allow_codec=True)
        assert "-codec" in argv and "libx264" in argv

    def test_rawvideo_in_mov_rejected(self, rvio_cmd):
        with pytest.raises(rvio_cmd.CheckError, match="unreadable"):
            build_argv(rvio_cmd, output="out.mov", codec="rawvideo")

    def test_rawvideo_in_mp4_is_not_specially_rejected(self, rvio_cmd):
        # the check only fires for the exact ".mov" extension
        argv, warnings = build_argv(rvio_cmd, output="out.mp4", codec="rawvideo")
        assert "-codec" in argv

    def test_prores_ks_warns_build_dependent(self, rvio_cmd):
        argv, warnings = build_argv(rvio_cmd, codec="prores_ks")
        assert any("only in some OpenRV builds" in w for w in warnings)

    def test_quality_with_mpeg4_warns(self, rvio_cmd):
        argv, warnings = build_argv(rvio_cmd, codec="mpeg4", quality=0.5)
        assert any("only changes mjpeg" in w for w in warnings)

    def test_dwaa_quality_le_1_warns(self, rvio_cmd):
        argv, warnings = build_argv(rvio_cmd, output="out.#.exr", exr_compression="DWAA",
                                    quality=0.5)
        assert any("DWA level" in w for w in warnings)

    def test_dwaa_quality_above_1_no_warning(self, rvio_cmd):
        argv, warnings = build_argv(rvio_cmd, output="out.#.exr", exr_compression="DWAA",
                                    quality=45)
        assert not any("DWA level" in w for w in warnings)


class TestAudioCodec:
    @pytest.mark.parametrize("codec", ["aac", "libfdk_aac", "mp3", "libmp3lame"])
    def test_unavailable_audio_codec_rejected(self, rvio_cmd, codec):
        with pytest.raises(rvio_cmd.CheckError, match="cannot mux"):
            build_argv(rvio_cmd, audiocodec=codec)

    def test_pcm_audio_codec_accepted(self, rvio_cmd):
        argv, _ = build_argv(rvio_cmd, audiocodec="pcm_s24le")
        assert "-audiocodec" in argv and "pcm_s24le" in argv

    def test_audio_with_two_inputs_rejected(self, rvio_cmd):
        a = Args(inputs=["a.#.exr", "b.#.exr"], output="out.mov", audio="sound.wav")
        with pytest.raises(rvio_cmd.CheckError, match="one picture source"):
            rvio_cmd.build(a)


class TestOverlaysAndSlate:
    def test_slate_value_starting_with_dash_rejected(self, rvio_cmd):
        with pytest.raises(rvio_cmd.CheckError, match="starts with '-'"):
            build_argv(rvio_cmd, slate=["Studio", "-bad"])

    def test_slate_value_starting_with_dash_rejected_on_the_real_cli(self, rvio_cmd, tmp_path,
                                                                     monkeypatch):
        # on the real command line, argparse's own nargs="+" parsing swallows a plain
        # "-word" as the start of a new option before build() ever sees it; a negative
        # number is the one dash-prefixed value that argparse still passes through, and it
        # hits the same check.
        monkeypatch.chdir(tmp_path)
        touch(tmp_path / "plate.0001.exr")
        rc = rvio_cmd.main(["plate.#.exr", "-o", "out.mov", "--slate", "Studio", "-5"])
        assert rc == 2

    def test_slate_field_without_equals_rejected(self, rvio_cmd):
        with pytest.raises(rvio_cmd.CheckError, match="Name=Value"):
            build_argv(rvio_cmd, slate=["Studio", "NoEquals"])

    def test_bug_logo_must_be_tiff(self, rvio_cmd):
        with pytest.raises(rvio_cmd.CheckError, match="TIFF"):
            build_argv(rvio_cmd, bug=["logo.png"])

    def test_bug_logo_tiff_accepted(self, rvio_cmd):
        argv, _ = build_argv(rvio_cmd, bug=["logo.tif"])
        assert argv[-4:-1] == ["-overlay", "bug", "logo.tif"] or "bug" in argv

    def test_outparam_starting_with_dash_rejected(self, rvio_cmd):
        with pytest.raises(rvio_cmd.CheckError, match="starts with '-'"):
            build_argv(rvio_cmd, outparam=["-bad=1"])


# --- checks: inputs and outputs ------------------------------------------------------------

class TestCheckInputs:
    def test_missing_input_raises(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        a = Args(inputs=["nope.exr"], output="out.mov")
        with pytest.raises(rvio_cmd.CheckError, match="does not exist"):
            rvio_cmd.check_inputs(a)

    def test_wildcard_rejected(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        touch(tmp_path / "plate.0001.exr")
        a = Args(inputs=["plate.*.exr"], output="out.mov")
        with pytest.raises(rvio_cmd.CheckError, match="wildcards"):
            rvio_cmd.check_inputs(a)

    def test_sequence_with_no_files_rejected(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        a = Args(inputs=["plate.#.exr"], output="out.mov")
        with pytest.raises(rvio_cmd.CheckError, match="no files match"):
            rvio_cmd.check_inputs(a)

    def test_movieproc_input_skips_existence_check(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        a = Args(inputs=["smptebars,start=1,end=2.movieproc"], output="out.mov")
        rvio_cmd.check_inputs(a)   # must not raise


class TestCheckOutput:
    def test_missing_extension_rejected(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        a = Args(inputs=["plate.#.exr"], output="noext")
        with pytest.raises(rvio_cmd.CheckError, match="needs an extension"):
            rvio_cmd.check_output(a)

    def test_missing_output_folder_rejected(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        a = Args(inputs=["plate.#.exr"], output=str(tmp_path / "nofolder" / "out.mov"))
        with pytest.raises(rvio_cmd.CheckError, match="does not exist"):
            rvio_cmd.check_output(a)

    def test_mkdir_flag_skips_folder_check_even_without_run(self, rvio_cmd, tmp_path,
                                                             monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "will_not_be_created" / "out.mov"
        a = Args(inputs=["plate.#.exr"], output=str(target), mkdir=True)
        rvio_cmd.check_output(a)   # must not raise
        assert not target.parent.exists()   # --mkdir alone does not create it; --run does

    def test_single_image_name_for_multi_frame_input_rejected(self, rvio_cmd, tmp_path,
                                                               monkeypatch):
        monkeypatch.chdir(tmp_path)
        touch(tmp_path / "plate.0001.exr")
        touch(tmp_path / "plate.0002.exr")
        a = Args(inputs=["plate.#.exr"], output="out.exr")
        with pytest.raises(rvio_cmd.CheckError, match="more than one frame"):
            rvio_cmd.check_output(a)

    def test_single_image_ok_with_explicit_range(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        touch(tmp_path / "plate.0001.exr")
        touch(tmp_path / "plate.0002.exr")
        a = Args(inputs=["plate.#.exr"], output="out.exr", range="1")
        rvio_cmd.check_output(a)   # must not raise

    def test_no_check_flag_skips_checks(self, rvio_cmd, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        rc = rvio_cmd.main(["nope.exr", "-o", "out.exr", "--no-check"])
        assert rc == 0


# --- expected_frames -----------------------------------------------------------------------

class TestExpectedFrames:
    def test_sequence_with_gap_counts_span(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        touch(tmp_path / "plate.0001.exr")
        touch(tmp_path / "plate.0002.exr")
        touch(tmp_path / "plate.0005.exr")
        a = Args(inputs=["plate.#.exr"], output="out.mov")
        assert rvio_cmd.expected_frames(a) == 5   # rvio holds frames across the gap: 5-1+1

    def test_slate_adds_leader_frames(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        touch(tmp_path / "plate.0001.exr")
        a = Args(inputs=["plate.#.exr"], output="out.mov", slate=["Studio", "Shot=sh010"],
                 leader_frames=8)
        assert rvio_cmd.expected_frames(a) == 1 + 8

    def test_explicit_range_counts_inclusive_span_plus_slate(self, rvio_cmd, tmp_path,
                                                              monkeypatch):
        monkeypatch.chdir(tmp_path)
        a = Args(inputs=["plate.#.exr"], output="out.mov", range="1001-1010",
                 slate=["Studio", "Shot=sh010"])
        assert rvio_cmd.expected_frames(a) == (1010 - 1001 + 1) + 1

    def test_single_range_frame(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        a = Args(inputs=["plate.#.exr"], output="out.mov", range="5")
        assert rvio_cmd.expected_frames(a) == 1


# --- printing --------------------------------------------------------------------------

class TestRender:
    def test_posix_shlex_round_trip(self, rvio_cmd):
        argv, _ = build_argv(rvio_cmd, crop=[0, 0, 10, 10], slate=["My Studio", "Shot=sh 010"])
        lines = rvio_cmd.render("rvio", argv)
        assert shlex.split(lines["posix"])[1:] == argv

    def test_hash_and_brackets_are_quoted_in_posix(self, rvio_cmd):
        argv, _ = build_argv(rvio_cmd, crop=[0, 0, 10, 10])
        lines = rvio_cmd.render("rvio", argv)
        assert "'['" in lines["posix"] and "']'" in lines["posix"]
        assert "'plate.#.exr'" in lines["posix"]

    def test_spaces_are_quoted_in_powershell_and_cmd(self, rvio_cmd):
        argv, _ = build_argv(rvio_cmd, slate=["My Studio", "Shot=sh 010"])
        lines = rvio_cmd.render("rvio", argv)
        assert "'My Studio'" in lines["powershell"]
        assert '"My Studio"' in lines["cmd"]


# --- main(): checks return exit 2, warnings print, JSON printed -----------------------------

class TestMainChecks:
    def test_missing_input_exit_2(self, rvio_cmd, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        rc = rvio_cmd.main(["nope.exr", "-o", "out.mov"])
        err = capsys.readouterr().err
        assert rc == 2
        assert "does not exist" in err

    def test_check_ok_prints_json_by_default(self, rvio_cmd, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        touch(tmp_path / "plate.0001.exr")
        rc = rvio_cmd.main(["plate.#.exr", "-o", "out.exr", "--range", "1"])
        out = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert out["argv"][0] == "rvio"

    def test_warning_is_printed_to_stderr(self, rvio_cmd, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        touch(tmp_path / "plate.0001.exr")
        rc = rvio_cmd.main(["plate.#.exr", "-o", "out.mov", "--range", "1", "--codec",
                           "prores_ks"])
        err = capsys.readouterr().err
        assert rc == 0
        assert "warning" in err


# --- --run: drives rv_tool.run() through the loaded module's rv_tool sibling ----------------

class TestRunPath:
    def test_run_counts_written_files_and_returns_0(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        touch(tmp_path / "plate.0001.exr")
        touch(tmp_path / "plate.0002.exr")

        def fake_run(tool, args, rv_bin=None, timeout=None, cwd=None, strict=None, env=None):
            touch(tmp_path / "out.0001.exr")
            touch(tmp_path / "out.0002.exr")
            return {"exit": 0, "message": "rvio finished OK", "elapsed_s": 0.1,
                   "argv": ["rvio"] + args, "error_lines": []}

        monkeypatch.setattr(rvio_cmd.rv_tool, "run", fake_run)
        rc = rvio_cmd.main(["plate.#.exr", "-o", "out.#.exr", "--run"])
        assert rc == 0

    def test_run_missing_output_files_returns_4(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        touch(tmp_path / "plate.0001.exr")
        touch(tmp_path / "plate.0002.exr")

        def fake_run(tool, args, rv_bin=None, timeout=None, cwd=None, strict=None, env=None):
            return {"exit": 0, "message": "rvio finished OK", "elapsed_s": 0.1,
                   "argv": ["rvio"] + args, "error_lines": []}

        monkeypatch.setattr(rvio_cmd.rv_tool, "run", fake_run)
        rc = rvio_cmd.main(["plate.#.exr", "-o", "out.#.exr", "--run"])
        assert rc == 4

    def test_run_propagates_tools_own_exit_code(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        touch(tmp_path / "plate.0001.exr")

        def fake_run(tool, args, rv_bin=None, timeout=None, cwd=None, strict=None, env=None):
            return {"exit": 3, "message": "rvio exited 0 but reported 1 error(s)",
                   "elapsed_s": 0.1, "argv": ["rvio"] + args,
                   "error_lines": ["ERROR: bad"]}

        monkeypatch.setattr(rvio_cmd.rv_tool, "run", fake_run)
        rc = rvio_cmd.main(["plate.#.exr", "-o", "out.#.exr", "--run"])
        assert rc == 3

    def test_mkdir_creates_folder_before_run(self, rvio_cmd, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        touch(tmp_path / "plate.0001.exr")
        target_folder = tmp_path / "made_by_mkdir"

        def fake_run(tool, args, rv_bin=None, timeout=None, cwd=None, strict=None, env=None):
            assert target_folder.is_dir()   # must already exist when the tool "runs"
            touch(target_folder / "out.0001.exr")
            return {"exit": 0, "message": "ok", "elapsed_s": 0.1, "argv": ["rvio"] + args,
                   "error_lines": []}

        monkeypatch.setattr(rvio_cmd.rv_tool, "run", fake_run)
        rc = rvio_cmd.main(["plate.#.exr", "-o", str(target_folder / "out.#.exr"), "--run",
                           "--mkdir"])
        assert rc == 0
