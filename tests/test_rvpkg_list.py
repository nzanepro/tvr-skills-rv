"""Tests for rvpkg/scripts/rvpkg_list.py.

rvpkg_list.py does `sys.path.insert(0, <its folder>)` then `import rv_tool` (which imports
rv_find the same way). The captured "rvpkg -list" / "rvpkg -info" text below is representative
sample output (a generic /home/example/.rv path is used in place of any real user's home).
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "rvpkg" / "scripts"
RVPKG_LIST_PATH = SCRIPTS_DIR / "rvpkg_list.py"
SIBLING_MODULE_NAMES = ("rv_tool", "rv_find")


def _load_rvpkg_list():
    saved_sys_path = list(sys.path)
    saved_modules = {name: sys.modules.pop(name, None) for name in SIBLING_MODULE_NAMES}
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec = importlib.util.spec_from_file_location("rvpkg_list_under_test", RVPKG_LIST_PATH)
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
def rvpkg_list():
    return _load_rvpkg_list()


LIST_OUTPUT = (
    'I L - 1.2 "Additional RV Nodes" /opt/rv/plugins/Packages/additional_nodes-1.2.rvpkg\n'
    'I - O 2.5 "OpenColorIO Basic Color Management" '
    'C:/Program Files/OpenRV/plugins/Packages/ocio_source_setup-2.5.rvpkg\n'
    '- - O 1.2 "Python Example Mode" /home/example/.rv/Packages/pyhello-1.2.rvpkg\n'
    'INFO: something\n'
)

INFO_OUTPUT = (
    "Name: Python Example Mode\n"
    "Version: 1.2\n"
    "Installed: YES\n"
    "Loadable: NO\n"
    "Directory: \n"
    "Author: Example Author\n"
    "Requires: \n"
    "RV-Version: 3.12.9\n"
    "OpenRV-Version: 1.0.0\n"
    "Optional: YES\n"
    "Modes: pyhello.py\n"
    "Files: pyhello.py\n"
    "Name: Python Example Mode\n"
    "Version: 1.2\n"
    "Installed: NO\n"
)


class TestParseList:
    def test_ignores_non_matching_lines(self, rvpkg_list):
        packages = rvpkg_list.parse_list(LIST_OUTPUT)
        assert len(packages) == 3

    def test_flags_map_to_installed_loaded_optional(self, rvpkg_list):
        packages = rvpkg_list.parse_list(LIST_OUTPUT)
        nodes = packages[0]
        assert nodes["installed"] is True
        assert nodes["loaded"] is True
        assert nodes["optional"] is False

        ocio = packages[1]
        assert ocio["installed"] is True
        assert ocio["loaded"] is False
        assert ocio["optional"] is True

        pyhello = packages[2]
        assert pyhello["installed"] is False
        assert pyhello["loaded"] is False
        assert pyhello["optional"] is True

    def test_forward_slash_path_split(self, rvpkg_list):
        nodes = rvpkg_list.parse_list(LIST_OUTPUT)[0]
        assert nodes["area"] == "/opt/rv/plugins/Packages"
        assert nodes["file"] == "additional_nodes-1.2.rvpkg"

    def test_backslash_and_drive_letter_path_split(self, rvpkg_list):
        pyhello = rvpkg_list.parse_list(LIST_OUTPUT)[2]
        assert pyhello["area"] == "/home/example/.rv/Packages"
        assert pyhello["file"] == "pyhello-1.2.rvpkg"

    def test_windows_style_path_with_spaces_is_kept(self, rvpkg_list):
        ocio = rvpkg_list.parse_list(LIST_OUTPUT)[1]
        assert ocio["path"] == ("C:/Program Files/OpenRV/plugins/Packages/"
                                "ocio_source_setup-2.5.rvpkg")
        assert ocio["area"] == "C:/Program Files/OpenRV/plugins/Packages"
        assert ocio["name"] == "OpenColorIO Basic Color Management"

    def test_version_and_name(self, rvpkg_list):
        nodes = rvpkg_list.parse_list(LIST_OUTPUT)[0]
        assert nodes["version"] == "1.2"
        assert nodes["name"] == "Additional RV Nodes"


class TestParseInfo:
    def test_two_blocks(self, rvpkg_list):
        blocks = rvpkg_list.parse_info(INFO_OUTPUT)
        assert len(blocks) == 2

    def test_yes_no_become_booleans(self, rvpkg_list):
        first = rvpkg_list.parse_info(INFO_OUTPUT)[0]
        assert first["installed"] is True
        assert first["loadable"] is False
        assert first["optional"] is True

    def test_keys_are_lower_snake_case(self, rvpkg_list):
        first = rvpkg_list.parse_info(INFO_OUTPUT)[0]
        assert first["rv_version"] == "3.12.9"
        assert first["openrv_version"] == "1.0.0"

    def test_second_block_is_minimal(self, rvpkg_list):
        second = rvpkg_list.parse_info(INFO_OUTPUT)[1]
        assert second == {"name": "Python Example Mode", "version": "1.2", "installed": False}


class TestMainParse:
    def test_lists_all_packages(self, rvpkg_list, tmp_path, capsys):
        f = tmp_path / "list.txt"
        f.write_text(LIST_OUTPUT)
        rc = rvpkg_list.main(["--parse", str(f)])
        report = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert len(report["packages"]) == 3

    def test_name_filter_is_case_insensitive(self, rvpkg_list, tmp_path, capsys):
        f = tmp_path / "list.txt"
        f.write_text(LIST_OUTPUT)
        rc = rvpkg_list.main(["--parse", str(f), "--name", "ocio"])
        report = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert len(report["packages"]) == 1
        assert report["packages"][0]["name"] == "OpenColorIO Basic Color Management"

    def test_name_filter_matches_file_too(self, rvpkg_list, tmp_path, capsys):
        f = tmp_path / "list.txt"
        f.write_text(LIST_OUTPUT)
        rc = rvpkg_list.main(["--parse", str(f), "--name", "pyhello"])
        report = json.loads(capsys.readouterr().out)
        assert len(report["packages"]) == 1

    def test_parse_stdin(self, rvpkg_list, monkeypatch, capsys):
        import io
        monkeypatch.setattr(sys, "stdin", io.StringIO(LIST_OUTPUT))
        rc = rvpkg_list.main(["--parse", "-"])
        assert rc == 0


# --- real-tool test: only when RV / OpenRV is actually installed --------------------------

def _rv_found():
    saved_sys_path = list(sys.path)
    saved = sys.modules.pop("rv_find", None)
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec = importlib.util.spec_from_file_location("rv_find_probe", SCRIPTS_DIR / "rv_find.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.locate("rvpkg", versions=False)["found"]
    finally:
        sys.path[:] = saved_sys_path
        if saved is not None:
            sys.modules["rv_find"] = saved
        else:
            sys.modules.pop("rv_find", None)


@pytest.mark.skipif(not _rv_found(), reason="RV / OpenRV is not installed on this machine")
def test_real_rvpkg_lists_at_least_one_package(rvpkg_list, capsys):
    rc = rvpkg_list.main([])
    report = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert len(report["packages"]) >= 1
