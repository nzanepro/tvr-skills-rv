"""Shared fixtures for the sheet_panels tests.

sheet_panels.py is a standalone script (not part of an installed package), so it
is loaded by file path with importlib rather than a normal import.
"""
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "rv-review" / "scripts" / "sheet_panels.py"


def _load_sheet_panels():
    spec = importlib.util.spec_from_file_location("sheet_panels", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def sp():
    """A fresh import of sheet_panels.py for each test."""
    return _load_sheet_panels()
