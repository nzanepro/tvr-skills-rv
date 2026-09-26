"""Tests for rvio/scripts/rvio_codecs.py.

rvio_codecs.py does `sys.path.insert(0, <its folder>)` then `import rv_tool` (which in turn
imports rv_find the same way). rv_tool.run is monkeypatched on the loaded module's own
`rv_tool` sibling so no real rvio is ever launched.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "rvio" / "scripts"
RVIO_CODECS_PATH = SCRIPTS_DIR / "rvio_codecs.py"
SIBLING_MODULE_NAMES = ("rv_tool", "rv_find")


def _load_rvio_codecs():
    saved_sys_path = list(sys.path)
    saved_modules = {name: sys.modules.pop(name, None) for name in SIBLING_MODULE_NAMES}
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec = importlib.util.spec_from_file_location("rvio_codecs_under_test",
                                                       RVIO_CODECS_PATH)
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
def rvio_codecs():
    return _load_rvio_codecs()


def outpath_of(args):
    return Path(args[args.index("-o") + 1])


class TestProbe:
    def test_success_reports_ok_and_seconds(self, rvio_codecs, tmp_path, monkeypatch):
        def fake_run(tool, args, rv_bin=None, timeout=None):
            outpath_of(args).write_bytes(b"x")
            return {"exit": 0, "elapsed_s": 0.4, "error_lines": [], "message": "ok"}

        monkeypatch.setattr(rvio_codecs.rv_tool, "run", fake_run)
        entry = rvio_codecs.probe("mjpeg", "mov", str(tmp_path))
        assert entry == {"ok": True, "seconds": 0.4}

    def test_failure_reports_error_text(self, rvio_codecs, tmp_path, monkeypatch):
        def fake_run(tool, args, rv_bin=None, timeout=None):
            return {"exit": 1, "elapsed_s": 0.2,
                   "error_lines": ["ERROR: Invalid video codec: libx264"], "message": "failed"}

        monkeypatch.setattr(rvio_codecs.rv_tool, "run", fake_run)
        entry = rvio_codecs.probe("libx264", "mov", str(tmp_path))
        assert entry["ok"] is False
        assert entry["error"] == "ERROR: Invalid video codec: libx264"

    def test_not_found_raises_file_not_found(self, rvio_codecs, tmp_path, monkeypatch):
        def fake_run(tool, args, rv_bin=None, timeout=None):
            return {"exit": 127, "elapsed_s": None, "error_lines": [], "message": "not found"}

        monkeypatch.setattr(rvio_codecs.rv_tool, "run", fake_run)
        with pytest.raises(FileNotFoundError):
            rvio_codecs.probe("mjpeg", "mov", str(tmp_path))

    def test_dnxhd_uses_1920x1080_and_bitrate_outparam(self, rvio_codecs, tmp_path,
                                                        monkeypatch):
        captured = {}

        def fake_run(tool, args, rv_bin=None, timeout=None):
            captured["args"] = args
            outpath_of(args).write_bytes(b"x")
            return {"exit": 0, "elapsed_s": 0.5, "error_lines": [], "message": "ok"}

        monkeypatch.setattr(rvio_codecs.rv_tool, "run", fake_run)
        rvio_codecs.probe("dnxhd", "mov", str(tmp_path))
        args = captured["args"]
        assert "width=1920,height=1080" in args[0]
        assert args[-2:] == ["-outparams", "vcc:b=36000000"]

    def test_probe_output_file_is_cleaned_up(self, rvio_codecs, tmp_path, monkeypatch):
        def fake_run(tool, args, rv_bin=None, timeout=None):
            outpath_of(args).write_bytes(b"x")
            return {"exit": 0, "elapsed_s": 0.1, "error_lines": [], "message": "ok"}

        monkeypatch.setattr(rvio_codecs.rv_tool, "run", fake_run)
        rvio_codecs.probe("mjpeg", "mov", str(tmp_path))
        assert list(Path(tmp_path).glob("probe_mjpeg.mov")) == []


class TestMain:
    def test_writable_list_and_report(self, rvio_codecs, monkeypatch, capsys):
        def fake_run(tool, args, rv_bin=None, timeout=None):
            if "libx264" in args:
                return {"exit": 1, "elapsed_s": 0.2,
                       "error_lines": ["ERROR: Invalid video codec: libx264"],
                       "message": "failed"}
            outpath_of(args).write_bytes(b"x")
            return {"exit": 0, "elapsed_s": 0.3, "error_lines": [], "message": "ok"}

        monkeypatch.setattr(rvio_codecs.rv_tool, "run", fake_run)
        rc = rvio_codecs.main(["--codec", "mjpeg", "--codec", "libx264", "--codec", "dnxhd"])
        report = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert report["writable"] == ["mjpeg", "dnxhd"]
        assert report["codecs"]["libx264"]["error"] == "ERROR: Invalid video codec: libx264"
        assert report["codecs"]["dnxhd"]["ok"] is True

    def test_not_found_returns_127(self, rvio_codecs, monkeypatch, capsys):
        def fake_run(tool, args, rv_bin=None, timeout=None):
            return {"exit": 127, "elapsed_s": None, "error_lines": [], "message": "rvio not found"}

        monkeypatch.setattr(rvio_codecs.rv_tool, "run", fake_run)
        rc = rvio_codecs.main(["--codec", "mjpeg"])
        err = capsys.readouterr().err
        assert rc == 127
        assert "not found" in err
