"""Tests for the iOS Simulator path of rv-review/scripts/app_capture.py.

Kept in a file of its own (rather than added to tests/test_rv_review_capture.py) so it does
not collide with concurrent edits to that file's web_capture / rasterize tests.

Covers three bugs in the iOS capture path:

- `xcrun simctl io <dev> screenshot` needs an absolute --out (a relative one failed on
  Xcode 26.4 with "The folder ... doesn't exist"): --out is resolved with os.path.abspath
  up front, so every derived path -- including the one passed to simctl -- is absolute.
- `simctl ui <dev> content_size` reports mixed-case values ("Small", "extra-Small"); the
  restore step now matches case-insensitively and restores the canonical lower-case spelling.
- A setting that cannot be read, or cannot be restored, now appends to a "warnings" list
  instead of being skipped without a trace, and --dry-run previews the restore commands (with
  a "<current>" placeholder, since nothing is queried from the simulator in a dry run).

Nothing here launches xcrun/simctl: `subprocess.run` is replaced with a fake that answers the
handful of simctl invocations a capture makes, and sys.platform / shutil.which are patched so
the "needs macOS" guard does not block the (mocked) non-dry-run tests on this Windows box.
"""
import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_CAPTURE_SCRIPT = REPO_ROOT / "rv-review" / "scripts" / "app_capture.py"


def _ios_args(**overrides):
    base = dict(screen="home", out="caps", device="booted", appearance=None, content_size=None,
               locales=None, bundle=None, clean_status_bar=False, settle=0.0)
    base.update(overrides)
    return Namespace(**base)


def _completed(returncode=0, stdout=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="" if returncode == 0 else "boom")


def _fake_simctl_run(appearance="dark", content_size="extra-Small", fail=None):
    """A stand-in for subprocess.run that answers `simctl ui <dev> appearance` / `content_size`
    queries, succeeds at everything else, except any command `fail(cmd)` flags as a failure."""
    def run(cmd, **kwargs):
        if fail and fail(cmd):
            return _completed(1, "")
        if cmd[-1:] == ["appearance"] and "ui" in cmd:
            return _completed(0, f"{appearance}\n")
        if cmd[-1:] == ["content_size"] and "ui" in cmd:
            return _completed(0, f"{content_size}\n")
        return _completed(0, "")
    return run


def _darwin_with_xcrun(monkeypatch, ac):
    monkeypatch.setattr(ac.sys, "platform", "darwin")
    monkeypatch.setattr(ac.shutil, "which", lambda n: "/usr/bin/xcrun" if n == "xcrun" else None)


# ---------------------------------------------------------------------------
# canonicalization helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("light", "light"), ("Dark", "dark"), ("LIGHT", "light"),
    ("", None), (None, None), ("sepia", None),
])
def test_canon_ios_appearance(ac, raw, expected):
    assert ac._canon_ios_appearance(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("Small", "small"), ("extra-Small", "extra-small"), ("MEDIUM", "medium"),
    ("accessibility-Large", "accessibility-large"),
    ("", None), (None, None), ("bogus", None),
])
def test_canon_ios_content_size(ac, raw, expected):
    assert ac._canon_ios_content_size(raw) == expected


# ---------------------------------------------------------------------------
# Fix 1: --out is resolved to an absolute path before it reaches simctl
# ---------------------------------------------------------------------------

def test_ios_dry_run_relative_out_becomes_absolute(tmp_path, ac, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = _ios_args(out="caps")

    files, log, warnings, restore = ac.capture_ios(a, True)

    assert os.path.isabs(a.out)
    assert files and all(os.path.isabs(f) for f in files)
    shot_cmds = [c for c in log if "screenshot" in c]
    assert shot_cmds and all(os.path.isabs(c[-1]) for c in shot_cmds)


def test_ios_real_run_relative_out_reaches_simctl_as_absolute(tmp_path, ac, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _darwin_with_xcrun(monkeypatch, ac)
    monkeypatch.setattr(ac.subprocess, "run", _fake_simctl_run())
    a = _ios_args(out="caps")

    files, log, warnings, restore = ac.capture_ios(a, False)

    assert warnings == []
    shot_cmds = [c for c in log if "screenshot" in c]
    assert shot_cmds and all(os.path.isabs(c[-1]) for c in shot_cmds)
    assert files and all(os.path.isabs(f) for f in files)


# ---------------------------------------------------------------------------
# Fix 2: content_size restore matches case-insensitively, canonical lower-case value
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [("Small", "small"), ("extra-Small", "extra-small"),
                                           ("small", "small")])
def test_ios_restores_content_size_case_insensitively(tmp_path, ac, monkeypatch, raw, expected):
    monkeypatch.chdir(tmp_path)
    _darwin_with_xcrun(monkeypatch, ac)
    monkeypatch.setattr(ac.subprocess, "run", _fake_simctl_run(content_size=raw))
    a = _ios_args(out="caps")

    files, log, warnings, restore = ac.capture_ios(a, False)

    assert warnings == []
    cs_restore = [c for c in restore if c[4] == "content_size"]
    assert cs_restore == [["xcrun", "simctl", "ui", "booted", "content_size", expected]]


# ---------------------------------------------------------------------------
# Fix 3: a setting that cannot be read or restored becomes a warning, not silence
# ---------------------------------------------------------------------------

def test_ios_warns_when_appearance_cannot_be_read(tmp_path, ac, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _darwin_with_xcrun(monkeypatch, ac)
    fail = lambda cmd: cmd[-1:] == ["appearance"] and "ui" in cmd  # noqa: E731
    monkeypatch.setattr(ac.subprocess, "run", _fake_simctl_run(fail=fail))
    a = _ios_args(out="caps")

    files, log, warnings, restore = ac.capture_ios(a, False)

    assert len(files) == 1                       # the capture itself still completes
    assert any("appearance" in w for w in warnings)
    assert not any(c[4] == "appearance" for c in restore)
    assert any(c[4] == "content_size" for c in restore)   # the other setting is unaffected


def test_ios_warns_on_unrecognized_content_size_value(tmp_path, ac, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _darwin_with_xcrun(monkeypatch, ac)
    monkeypatch.setattr(ac.subprocess, "run", _fake_simctl_run(content_size="Weird"))
    a = _ios_args(out="caps")

    files, log, warnings, restore = ac.capture_ios(a, False)

    assert any("Weird" in w for w in warnings)
    assert not any(c[4] == "content_size" for c in restore)


def test_ios_restore_failure_warns_instead_of_crashing(tmp_path, ac, monkeypatch):
    # the getters succeed, but every *set* command (variant changes and restores alike) fails
    fail = lambda cmd: len(cmd) == 6 and cmd[4] in ("appearance", "content_size")  # noqa: E731
    monkeypatch.chdir(tmp_path)
    _darwin_with_xcrun(monkeypatch, ac)
    monkeypatch.setattr(ac.subprocess, "run", _fake_simctl_run(fail=fail))
    a = _ios_args(out="caps")

    files, log, warnings, restore = ac.capture_ios(a, False)

    assert len(files) == 1                        # capture still completed
    assert len(restore) == 2                       # both settings were recognized and queued
    assert len(warnings) == 2                      # ... and both failed to restore, as warnings
    assert all("could not restore" in w for w in warnings)


# ---------------------------------------------------------------------------
# Fix 4: --dry-run previews the restore commands
# ---------------------------------------------------------------------------

def test_ios_dry_run_restore_preview(tmp_path, ac, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = _ios_args(out="caps")

    files, log, warnings, restore = ac.capture_ios(a, True)

    assert warnings == []
    assert len(restore) == 2
    assert all(c[:4] == ["xcrun", "simctl", "ui", "booted"] for c in restore)
    assert all(c[-1] == ac.IOS_DRY_RUN_PLACEHOLDER for c in restore)
    assert all(c in log for c in restore)          # visible in the full command log too


def test_ios_dry_run_status_bar_clear_stays_last(tmp_path, ac, monkeypatch):
    """The restore preview must not push the status-bar-clear command (which review tooling
    keys off of) out of its usual last-command position."""
    monkeypatch.chdir(tmp_path)
    a = _ios_args(out="caps", clean_status_bar=True)

    files, log, warnings, restore = ac.capture_ios(a, True)

    assert log[-1][-1] == "clear"


# ---------------------------------------------------------------------------
# CLI level
# ---------------------------------------------------------------------------

def _run_cli(*args, cwd):
    return subprocess.run([sys.executable, str(APP_CAPTURE_SCRIPT), *map(str, args)],
                          capture_output=True, text=True, timeout=30, cwd=cwd)


def test_app_capture_cli_ios_dry_run_reports_warnings_and_restore(tmp_path):
    r = _run_cli("ios", "--screen", "home", "--out", "caps", "--dry-run", cwd=tmp_path)

    assert r.returncode == 0, r.stderr
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res["ok"] is True
    assert res["warnings"] == []
    assert isinstance(res["restore"], list) and res["restore"]
    assert all(c[-1] == "<current>" for c in res["restore"])
    assert all(c in res["commands"] for c in res["restore"])


def test_app_capture_cli_android_dry_run_has_empty_warnings_and_restore(tmp_path):
    # a dry run starts nothing, whatever adb this machine has
    r = _run_cli("android", "--screen", "home", "--out", "caps", "--dry-run", cwd=tmp_path)

    assert r.returncode == 0, r.stderr
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res["warnings"] == []
    assert res["restore"] == []
