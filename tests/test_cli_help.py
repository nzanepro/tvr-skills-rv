"""CLI --help smoke tests for sheet_panels.py, plus a couple of hygiene checks
on rv-review/: rv_review.py's DEFAULT_TAG, and no personal/absolute paths
baked into any file under rv-review/. Only sys.executable is ever launched.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "rv-review" / "scripts"
SHEET_PANELS_SCRIPT = SCRIPTS_DIR / "sheet_panels.py"
RV_REVIEW_SCRIPT = SCRIPTS_DIR / "rv_review.py"
REVIEW_MANIFEST_SCRIPT = SCRIPTS_DIR / "review_manifest.py"
RV_SESSION_SCRIPT = SCRIPTS_DIR / "rv_session.py"
COMPARE_DIRS_SCRIPT = SCRIPTS_DIR / "compare_dirs.py"
REVIEW_SET_SCRIPT = SCRIPTS_DIR / "review_set.py"
RASTERIZE_SCRIPT = SCRIPTS_DIR / "rasterize.py"
WEB_CAPTURE_SCRIPT = SCRIPTS_DIR / "web_capture.py"
APP_CAPTURE_SCRIPT = SCRIPTS_DIR / "app_capture.py"
RV_REVIEW_DIR = REPO_ROOT / "rv-review"


def _run_help(*args):
    return subprocess.run([sys.executable, str(SHEET_PANELS_SCRIPT), *args, "--help"],
                          capture_output=True, text=True, timeout=30)


def _run_script_help(script, *args):
    return subprocess.run([sys.executable, str(script), *args, "--help"],
                          capture_output=True, text=True, timeout=30)


def test_top_level_help_exits_0_and_describes_stacked_sheets():
    result = _run_help()
    assert result.returncode == 0
    assert "stacked sheet" in result.stdout
    assert "pixel (0, 0)" in result.stdout


def test_split_help_exits_0_and_mentions_frames_json_and_out():
    result = _run_help("split")
    assert result.returncode == 0
    assert "frames.json" in result.stdout
    assert "--out" in result.stdout


def test_label_help_exits_0_and_mentions_image_label_and_title():
    result = _run_help("label")
    assert result.returncode == 0
    assert "IMAGE=LABEL" in result.stdout
    assert "--title" in result.stdout


def test_rv_review_help_exits_0_and_mentions_rvpush_and_tag():
    # rv_review.py's own --help is also covered by tests/test_rv_review.py; a little
    # overlap across files is fine, and this keeps the full script list in one place.
    result = _run_script_help(RV_REVIEW_SCRIPT)
    assert result.returncode == 0
    assert "rvpush" in result.stdout
    assert "--tag" in result.stdout


def test_review_manifest_help_exits_0_and_mentions_schema_version():
    result = _run_script_help(REVIEW_MANIFEST_SCRIPT)
    assert result.returncode == 0
    assert "schema_version" in result.stdout
    assert "manifest" in result.stdout


def test_rv_session_top_level_help_exits_0_and_lists_subcommands():
    result = _run_script_help(RV_SESSION_SCRIPT)
    assert result.returncode == 0
    assert "{write,check,render}" in result.stdout
    assert "gtoinfo" in result.stdout


def test_rv_session_write_help_exits_0_and_mentions_layout_and_out():
    result = _run_script_help(RV_SESSION_SCRIPT, "write")
    assert result.returncode == 0
    assert "--layout" in result.stdout
    assert "manifest" in result.stdout


def test_rv_session_check_help_exits_0_and_mentions_gtoinfo():
    result = _run_script_help(RV_SESSION_SCRIPT, "check")
    assert result.returncode == 0
    assert "gtoinfo" in result.stdout
    assert "--rv-bin" in result.stdout


def test_rv_session_render_help_exits_0_and_mentions_rvio_and_out():
    result = _run_script_help(RV_SESSION_SCRIPT, "render")
    assert result.returncode == 0
    assert "rvio" in result.stdout
    assert "--out" in result.stdout


def test_compare_dirs_help_exits_0_and_mentions_changed_fraction_and_gain():
    result = _run_script_help(COMPARE_DIRS_SCRIPT)
    assert result.returncode == 0
    assert "changed_fraction" in result.stdout
    assert "--gain" in result.stdout


def test_review_set_help_exits_0_and_mentions_variant_and_anchor():
    result = _run_script_help(REVIEW_SET_SCRIPT)
    assert result.returncode == 0
    assert "variant" in result.stdout
    assert "--anchor" in result.stdout


def test_rasterize_help_exits_0_and_mentions_resvg_and_same_size():
    result = _run_script_help(RASTERIZE_SCRIPT)
    assert result.returncode == 0
    assert "resvg" in result.stdout
    assert "--same-size" in result.stdout


def test_web_capture_help_exits_0_and_mentions_breakpoints_and_playwright():
    result = _run_script_help(WEB_CAPTURE_SCRIPT)
    assert result.returncode == 0
    assert "--breakpoints" in result.stdout
    assert "Playwright" in result.stdout


def test_app_capture_top_level_help_exits_0_and_lists_platforms():
    result = _run_script_help(APP_CAPTURE_SCRIPT)
    assert result.returncode == 0
    assert "{ios,android,electron}" in result.stdout
    assert "xcrun simctl" in result.stdout


def test_app_capture_ios_help_exits_0_and_mentions_content_size():
    result = _run_script_help(APP_CAPTURE_SCRIPT, "ios")
    assert result.returncode == 0
    assert "--content-size" in result.stdout
    assert "Dynamic Type" in result.stdout


def test_app_capture_android_help_exits_0_and_mentions_font_scale():
    result = _run_script_help(APP_CAPTURE_SCRIPT, "android")
    assert result.returncode == 0
    assert "--font-scale" in result.stdout
    assert "--demo-mode" in result.stdout


def test_app_capture_electron_help_exits_0_and_mentions_main_and_playwright():
    result = _run_script_help(APP_CAPTURE_SCRIPT, "electron")
    assert result.returncode == 0
    assert "--main" in result.stdout
    assert "Playwright" in result.stdout


def test_rv_review_declares_default_tag():
    text = RV_REVIEW_SCRIPT.read_text()
    assert 'DEFAULT_TAG = "rv-review"' in text


def test_no_personal_paths_under_rv_review():
    forbidden = ("C:\\Users", "/Users/", "/home/")
    offenders = []
    for path in RV_REVIEW_DIR.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for needle in forbidden:
            if needle in text:
                offenders.append((str(path.relative_to(REPO_ROOT)), needle))
    assert not offenders, offenders
