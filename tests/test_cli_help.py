"""CLI --help smoke tests for sheet_panels.py, plus a couple of hygiene checks
on rv-review/: rv_review.py's DEFAULT_TAG, and no personal/absolute paths
baked into any file under rv-review/. Only sys.executable is ever launched.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEET_PANELS_SCRIPT = REPO_ROOT / "rv-review" / "scripts" / "sheet_panels.py"
RV_REVIEW_SCRIPT = REPO_ROOT / "rv-review" / "scripts" / "rv_review.py"
RV_REVIEW_DIR = REPO_ROOT / "rv-review"


def _run_help(*args):
    return subprocess.run([sys.executable, str(SHEET_PANELS_SCRIPT), *args, "--help"],
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
