"""Tests for rv_review.py 0.2.2 fixes: the decode check before a load, the launcher's
message when RV exits, log lines the launcher itself causes, errors / warnings always lists,
-outsrgb on the annotated export and the raise-to-front command.

Nothing here starts RV, rvpush or rvio: the functions that would are monkeypatched.
"""
import io
import json
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image


def _png(path, size=(16, 8), color=(200, 50, 50)):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def _jpeg_bytes(size=(64, 64)):
    buf = io.BytesIO()
    Image.effect_noise(size, 60).convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# header_problem / decode_problem
# ---------------------------------------------------------------------------

def test_header_problem_valid_png_passes(tmp_path, rr):
    assert rr.header_problem(str(_png(tmp_path / "ok.png"))) is None


def test_header_problem_truncated_png(tmp_path, rr):
    good = _png(tmp_path / "ok.png", (64, 64)).read_bytes()
    bad = tmp_path / "truncated.png"
    bad.write_bytes(good[:200] if len(good) > 200 else good[:-20])
    assert "IEND" in rr.header_problem(str(bad))


@pytest.mark.parametrize("name, data, words", [
    ("empty.png", b"", "empty"),
    ("text.png", b"hello, not a png at all", "signature"),
    ("text.exr", b"hello, not an exr", "OpenEXR"),
    ("text.tif", b"hello, not a tiff", "TIFF"),
    ("text.jpg", b"hello, not a jpeg", "JPEG"),
    ("text.dpx", b"hello, not a dpx file", "DPX"),
])
def test_header_problem_wrong_magic(tmp_path, rr, name, data, words):
    p = tmp_path / name
    p.write_bytes(data)
    assert words in rr.header_problem(str(p))


def test_header_problem_truncated_dpx(tmp_path, rr):
    p = tmp_path / "short.dpx"
    p.write_bytes(b"SDPX" + b"\0" * 12 + struct.pack(">I", 10000) + b"\0" * 100)
    assert "truncated" in rr.header_problem(str(p))
    p.write_bytes(b"XPDS" + b"\0" * 12 + struct.pack("<I", 120) + b"\0" * 100)
    assert rr.header_problem(str(p)) is None


def test_header_problem_jpeg_end_marker_only_when_asked(tmp_path, rr):
    p = tmp_path / "cut.jpg"
    p.write_bytes(_jpeg_bytes()[:-400])
    assert "end-of-image" in rr.header_problem(str(p))
    assert rr.header_problem(str(p), jpeg_end=False) is None


def test_header_problem_unknown_format_passes(tmp_path, rr):
    p = tmp_path / "clip.mov"
    p.write_bytes(b"anything")
    assert rr.header_problem(str(p)) is None


def test_decode_problem_pillow_catches_a_truncated_jpeg(tmp_path, rr):
    p = tmp_path / "cut.jpg"
    p.write_bytes(_jpeg_bytes()[:-400])
    assert "cannot be decoded" in rr.decode_problem(str(p), Image)


def test_decode_problem_pillow_catches_corrupt_png_data(tmp_path, rr):
    # a PNG whose IDAT is garbage but whose chunks and IEND are intact: only a decode sees it
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    p = tmp_path / "corrupt.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", b"not zlib at all")
                  + chunk(b"IEND", b""))
    assert rr.header_problem(str(p)) is None
    assert "cannot be decoded" in rr.decode_problem(str(p), Image)


def test_decode_problem_valid_files_and_without_pillow(tmp_path, rr):
    png = _png(tmp_path / "ok.png")
    jpg = tmp_path / "ok.jpg"
    jpg.write_bytes(_jpeg_bytes())
    assert rr.decode_problem(str(png), Image) is None
    assert rr.decode_problem(str(jpg), Image) is None
    assert rr.decode_problem(str(png), None) is None


# ---------------------------------------------------------------------------
# sequence_first_file / decode_targets / decode_check
# ---------------------------------------------------------------------------

@pytest.fixture()
def seq_dir(tmp_path):
    for n in (1003, 1001, 1002):
        _png(tmp_path / "seq" / f"shot.{n}.png")
    (tmp_path / "seq" / "other.1000.png").write_bytes(b"x")
    return tmp_path / "seq"


@pytest.mark.parametrize("spec, first", [
    ("shot.#.png", "shot.1001.png"),
    ("shot.1002-1003#.png", "shot.1002.png"),
    ("shot.@@@@.png", "shot.1001.png"),
    ("shot.%04d.png", "shot.1001.png"),
])
def test_sequence_first_file(seq_dir, rr, spec, first):
    assert Path(rr.sequence_first_file(str(seq_dir / spec))).name == first


def test_sequence_first_file_none_when_nothing_matches(seq_dir, tmp_path, rr):
    assert rr.sequence_first_file(str(seq_dir / "nope.#.png")) is None
    assert rr.sequence_first_file(str(tmp_path / "missing" / "a.#.png")) is None


def test_decode_targets_stills_and_first_frames_only(seq_dir, tmp_path, rr):
    still = _png(tmp_path / "a.png")
    tokens = [str(still), str(tmp_path / "clip.mov"), str(seq_dir / "shot.#.png"),
              "[", str(still), str(tmp_path / "b.exr"), "-in", "3", "]", str(tmp_path / "r.rv")]
    got = rr.decode_targets(tokens)
    assert got == [str(still), str(seq_dir / "shot.1001.png"), str(tmp_path / "b.exr")]


def test_decode_check_reports_bad_files_with_the_escape_hatch(tmp_path, rr):
    ok = _png(tmp_path / "ok.png")
    bad = tmp_path / "bad.png"
    bad.write_bytes(ok.read_bytes()[:30])
    msgs = rr.decode_check([str(ok), str(bad)], Image)
    assert len(msgs) == 1
    assert str(bad) in msgs[0] and "--no-decode-check" in msgs[0]
    assert rr.decode_check([str(ok)], None) == []
    assert rr.decode_check([], "auto") == []


def test_cli_no_decode_check_is_passed_to_review(tmp_path, monkeypatch, rr):
    src = _png(tmp_path / "a.png")
    seen = {}

    def fake_review(tokens, rv, rvpush, *args):
        seen["check_decode"] = args[-1]
        return {"ok": True, "errors": [], "warnings": []}

    monkeypatch.setattr(rr, "find_rv", lambda rv_bin=None: ("rv", "rvpush"))
    monkeypatch.setattr(rr, "review", fake_review)
    assert rr.main([str(src), "--no-decode-check"]) == 0
    assert seen["check_decode"] is False
    assert rr.main([str(src)]) == 0
    assert seen["check_decode"] is True


# ---------------------------------------------------------------------------
# log noise and the launcher's messages
# ---------------------------------------------------------------------------

def test_parse_log_ignores_the_launchers_own_polling_and_rv_file_probing(rr):
    text = ("ERROR: RvNetwork: no session for incoming connection\n"
            "ERROR: connection aborted reading greeting\n"
            "INFO: trying brute force to find an image reader for review.rv\n"
            "WARNING: trying brute force to find an image reader for review.rv\n"
            "ERROR: real problem\n")
    assert rr.parse_log(text) == (["real problem"], [])


class _FakeProc:
    pid = 4242

    def __init__(self, code):
        self.code = code

    def poll(self):
        return self.code


def _patch_launch(monkeypatch, rr, proc, clock):
    monkeypatch.setattr(rr, "_rvpush", lambda *a, **k: (1, ""))
    monkeypatch.setattr(rr, "_launch_detached", lambda args, log=None: proc)
    monkeypatch.setattr(rr.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(rr.time, "sleep", lambda s: None)


def test_load_says_when_rv_exited(monkeypatch, rr):
    _patch_launch(monkeypatch, rr, _FakeProc(1), iter([0.0, 57.0, 57.0, 57.0]))
    with pytest.raises(rr.RvError) as e:
        rr._load("rv", "rvpush", "t", ["a.png"])
    msg = str(e.value)
    assert "exited after 57 s (exit code 1)" in msg and "did not answer" not in msg


def test_load_timeout_when_rv_keeps_running(monkeypatch, rr):
    _patch_launch(monkeypatch, rr, _FakeProc(None), iter([0.0] + [rr.LAUNCH_TIMEOUT_S + 1] * 5))
    with pytest.raises(rr.RvError) as e:
        rr._load("rv", "rvpush", "t", ["a.png"])
    assert f"did not answer rvpush within {int(rr.LAUNCH_TIMEOUT_S)} s" in str(e.value)
    assert "is running" in str(e.value)


# ---------------------------------------------------------------------------
# envelope lists, -outsrgb, raise command
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [None, {"action": "state"}, {"errors": None, "warnings": None},
                                  {"action": "notes", "problems": []}])
def test_envelope_errors_and_warnings_are_always_lists(rr, body):
    res = rr.envelope(body)
    assert res["errors"] == [] and res["warnings"] == []
    assert json.loads(json.dumps(res))["errors"] == []


def test_envelope_keeps_given_lists(rr):
    res = rr.envelope({"errors": ["e"], "warnings": ["w"]}, rr.EXIT_MISMATCH)
    assert res["errors"] == ["e"] and res["warnings"] == ["w"]


def test_export_annotated_asks_rvio_for_srgb_output(tmp_path, monkeypatch, rr):
    rv = tmp_path / "bin" / "rv"
    rv.parent.mkdir()
    rvio = rv.parent / ("rvio.exe" if rr.os.name == "nt" else "rvio")
    rvio.write_text("", encoding="utf-8")
    out = tmp_path / "notes"

    def fake_rvpush(rvpush, tag, *args):
        (out / "annotated_session.rv").write_text("GTOa (4)\n", encoding="utf-8")
        return 0, ""

    class R:
        returncode, stdout, stderr = 0, "", ""

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        Image.new("RGB", (4, 4)).save(out / "annotated.0002.png")
        return R()

    monkeypatch.setattr(rr, "_rvpush", fake_rvpush)
    monkeypatch.setattr(rr.subprocess, "run", fake_run)
    images, cmd, problems = rr.export_annotated(str(rv), "rvpush", "t", [2], out)
    assert "-outsrgb" in cmd and calls[0] == cmd
    assert images == {2: str(out / "annotated.0002.png")} and problems == []


def test_raise_window_command_is_one_ascii_line_that_compiles(rr):
    cmd = rr.RAISE_WINDOW_COMMAND
    assert "\n" not in cmd and cmd.isascii()
    compile(cmd, "<py-exec>", "exec")
    assert "raise_()" in cmd and "sessionWindow" in cmd
