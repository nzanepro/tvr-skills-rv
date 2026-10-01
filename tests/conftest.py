"""Shared fixtures for the rv-review tests.

The scripts are standalone files (not an installed package), so each is loaded by file path
with importlib. The scripts folder is also put on sys.path, because several scripts import
their neighbours (compare_dirs imports sheet_panels, rv_review imports review_manifest and
rv_session, and so on).
"""
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "rv-review" / "scripts"
SCRIPT_PATH = SCRIPTS / "sheet_panels.py"
RV_REVIEW_SCRIPT_PATH = SCRIPTS / "rv_review.py"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_script(name):
    """A fresh import of rv-review/scripts/<name>.py."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_sheet_panels():
    return load_script("sheet_panels")


def _load_rv_review():
    return load_script("rv_review")


@pytest.fixture()
def sp():
    """A fresh import of sheet_panels.py for each test."""
    return _load_sheet_panels()


@pytest.fixture()
def rr():
    """A fresh import of rv_review.py for each test."""
    return _load_rv_review()


@pytest.fixture()
def rm():
    """A fresh import of review_manifest.py."""
    return load_script("review_manifest")


@pytest.fixture()
def rs():
    """A fresh import of rv_session.py."""
    return load_script("rv_session")


@pytest.fixture()
def cd():
    """A fresh import of compare_dirs.py."""
    return load_script("compare_dirs")


@pytest.fixture()
def rset():
    """A fresh import of review_set.py."""
    return load_script("review_set")


@pytest.fixture()
def rz():
    """A fresh import of rasterize.py."""
    return load_script("rasterize")


@pytest.fixture()
def wc():
    """A fresh import of web_capture.py."""
    return load_script("web_capture")


@pytest.fixture()
def ac():
    """A fresh import of app_capture.py."""
    return load_script("app_capture")


def pytest_addoption(parser):
    parser.addoption("--rv-bin", metavar="DIR", default=None,
                     help="RV bin folder for the tests that run real RV tools (gtoinfo, "
                          "rvio); default: the rv-review lookup (config file, PATH, the "
                          "usual install folders)")


def find_rv_bin(rv_bin=None):
    """Folder holding rv / gtoinfo / rvio when RV is installed (--rv-bin, then the lookup
    rv_review.py uses), else None. Real-tool tests skip without it; they never open an RV
    window."""
    if rv_bin:
        return Path(rv_bin) if Path(rv_bin).is_dir() else None
    hit = shutil.which("rv")
    if hit:
        return Path(hit).parent
    try:
        rr = _load_rv_review()
        rv, _ = rr.find_rv()
        return rv.parent
    except Exception:
        return None


@pytest.fixture()
def rv_bin(request):
    b = find_rv_bin(request.config.getoption("--rv-bin"))
    if b is None:
        pytest.skip("RV / OpenRV not installed (or use --rv-bin DIR)")
    return b
