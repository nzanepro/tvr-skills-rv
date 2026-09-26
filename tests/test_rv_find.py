"""Tests for rv_find.py (identical copies in rvio/scripts, rvls/scripts, rvpkg/scripts).

rv_find.py is a standalone script (no sibling imports of its own), so it is loaded by file
path with importlib. All discovery-order tests build a fake install tree in tmp_path and pass
explicit env / platform / home / root / registry, so behaviour is independent of the host OS
and of whatever is really installed on the machine running the suite.
"""
import importlib.util
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RV_FIND_PATHS = {
    "rvio": REPO_ROOT / "rvio" / "scripts" / "rv_find.py",
    "rvls": REPO_ROOT / "rvls" / "scripts" / "rv_find.py",
    "rvpkg": REPO_ROOT / "rvpkg" / "scripts" / "rv_find.py",
}
RV_TOOL_PATHS = {
    "rvio": REPO_ROOT / "rvio" / "scripts" / "rv_tool.py",
    "rvls": REPO_ROOT / "rvls" / "scripts" / "rv_tool.py",
    "rvpkg": REPO_ROOT / "rvpkg" / "scripts" / "rv_tool.py",
}


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_NAMES_HELPER = _load(RV_FIND_PATHS["rvio"], "rv_find_names_helper")  # pure functions only


@pytest.fixture()
def rvfind():
    """A fresh import of rvio's copy of rv_find.py (proven byte-identical below)."""
    return _load(RV_FIND_PATHS["rvio"], "rv_find_under_test")


def make_tool(folder, tool="rvio", platform=None):
    """Create an empty, executable stand-in for `tool` inside folder; return its path."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = _NAMES_HELPER.exe_names(tool, platform)[0]
    p = folder / name
    p.write_text("")
    if os.name != "nt":
        p.chmod(0o755)
    return p


# --- the three copies are kept identical -----------------------------------------------

class TestIdenticalCopies:
    """rv_find.py and rv_tool.py must be byte-identical across the three skills."""

    def test_rv_find_copies_identical(self):
        bodies = {name: p.read_bytes() for name, p in RV_FIND_PATHS.items()}
        assert bodies["rvio"] == bodies["rvls"] == bodies["rvpkg"]

    def test_rv_tool_copies_identical(self):
        bodies = {name: p.read_bytes() for name, p in RV_TOOL_PATHS.items()}
        assert bodies["rvio"] == bodies["rvls"] == bodies["rvpkg"]


# --- exe_names / os_kind ----------------------------------------------------------------

class TestExeNames:
    def test_windows_names_end_in_exe(self, rvfind):
        assert rvfind.exe_names("rvio", "win32") == ("rvio.exe",)
        assert rvfind.exe_names("rv", "win32") == ("rv.exe",)

    def test_macos_rv_may_be_rv_or_rv64(self, rvfind):
        assert rvfind.exe_names("rv", "darwin") == ("RV", "RV64", "rv")
        assert rvfind.exe_names("rvio", "darwin") == ("rvio",)

    def test_linux_plain_names(self, rvfind):
        assert rvfind.exe_names("rvio", "linux") == ("rvio",)

    def test_os_kind(self, rvfind):
        assert rvfind.os_kind("win32") == "windows"
        assert rvfind.os_kind("darwin") == "macos"
        assert rvfind.os_kind("linux") == "linux"


# --- discovery order: full precedence chain ---------------------------------------------

class TestPrecedenceChain:
    """--rv-bin beats RV_BIN beats RVPUSH beats RV_PATH beats RV_APP_RV beats RV_HOME
    beats PATH beats install folders (registry is covered separately, windows-only)."""

    def test_chain(self, tmp_path, rvfind):
        # PATH lookup depends on the REAL host's shutil.which conventions (PATHEXT on
        # Windows, exec bit on POSIX), independent of the platform this test simulates
        # everywhere else, so name the fake tools the way the real host can find them.
        plat = "win32" if os.name == "nt" else "linux"
        home = tmp_path / "home"
        root = tmp_path / "root"
        if plat == "win32":
            pf = tmp_path / "Program Files"
            install_dir = pf / "OpenRV-1.0" / "bin"
            env = {"ProgramFiles": str(pf)}
        else:
            install_dir = root / "opt" / "rv-1.0" / "bin"
            env = {}
        make_tool(install_dir, "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat, home=home,
                                            root=root, registry=lambda: None)
        assert (bin_dir, source) == (install_dir, "install folder")

        path_dir = tmp_path / "path_bin"
        make_tool(path_dir, "rvio", plat)
        env["PATH"] = str(path_dir)
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat, home=home,
                                            root=root, registry=lambda: None)
        assert (bin_dir, source) == (path_dir, "PATH")

        home_root = tmp_path / "rv_home_root"
        make_tool(home_root / "bin", "rvio", plat)
        env["RV_HOME"] = str(home_root)
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat, home=home,
                                            root=root, registry=lambda: None)
        assert (bin_dir, source) == (home_root / "bin", "RV_HOME")

        app_rv_dir = tmp_path / "app_rv_dir"
        make_tool(app_rv_dir, "rvio", plat)
        env["RV_APP_RV"] = str(app_rv_dir / "rv")   # rv itself need not exist
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat, home=home,
                                            root=root, registry=lambda: None)
        assert (bin_dir, source) == (app_rv_dir, "RV_APP_RV")

        rv_path_dir = tmp_path / "rv_path_dir"
        make_tool(rv_path_dir, "rvio", plat)
        env["RV_PATH"] = str(rv_path_dir / "rv")
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat, home=home,
                                            root=root, registry=lambda: None)
        assert (bin_dir, source) == (rv_path_dir, "RV_PATH")

        rvpush_dir = tmp_path / "rvpush_dir"
        make_tool(rvpush_dir, "rvio", plat)
        env["RVPUSH_RV_EXECUTABLE_PATH"] = str(rvpush_dir / "rv")
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat, home=home,
                                            root=root, registry=lambda: None)
        assert (bin_dir, source) == (rvpush_dir, "RVPUSH_RV_EXECUTABLE_PATH")

        rv_bin_dir = tmp_path / "rv_bin_env_dir"
        make_tool(rv_bin_dir, "rvio", plat)
        env["RV_BIN"] = str(rv_bin_dir)
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat, home=home,
                                            root=root, registry=lambda: None)
        assert (bin_dir, source) == (rv_bin_dir, "RV_BIN")

        arg_bin_dir = tmp_path / "arg_bin_dir"
        make_tool(arg_bin_dir, "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin=str(arg_bin_dir), env=env,
                                            platform=plat, home=home, root=root,
                                            registry=lambda: None)
        assert (bin_dir, source) == (arg_bin_dir, "--rv-bin")

    def test_rvpush_none_is_ignored(self, tmp_path, rvfind):
        plat = "linux"
        rv_path_dir = tmp_path / "rv_path_dir"
        make_tool(rv_path_dir, "rvio", plat)
        env = {"RVPUSH_RV_EXECUTABLE_PATH": "none", "RV_PATH": str(rv_path_dir / "rv")}
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat,
                                            home=tmp_path / "home", root=tmp_path / "root",
                                            registry=lambda: None)
        assert (bin_dir, source) == (rv_path_dir, "RV_PATH")

    def test_rv_home_dot_app_maps_to_contents_macos(self, tmp_path, rvfind):
        plat = "darwin"
        app = tmp_path / "RV.app"
        make_tool(app / "Contents" / "MacOS", "rvio", plat)
        env = {"RV_HOME": str(app)}
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat,
                                            home=tmp_path / "home", root=tmp_path / "root",
                                            registry=lambda: None)
        assert (bin_dir, source) == (app / "Contents" / "MacOS", "RV_HOME")


class TestRegistryPrecedence:
    """Registry is windows-only, and sits between PATH and the install folders."""

    def test_registry_beats_install_folder(self, tmp_path, rvfind):
        plat = "win32"
        pf = tmp_path / "Program Files"
        install_dir = pf / "OpenRV-1.0" / "bin"
        make_tool(install_dir, "rvio", plat)
        env = {"ProgramFiles": str(pf)}
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat,
                                            home=tmp_path / "home", root=tmp_path / "root",
                                            registry=lambda: None)
        assert (bin_dir, source) == (install_dir, "install folder")

        reg_dir = tmp_path / "registry_bin"
        make_tool(reg_dir, "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat,
                                            home=tmp_path / "home", root=tmp_path / "root",
                                            registry=lambda: str(reg_dir / "rv.exe"))
        assert (bin_dir, source) == (reg_dir, "registry")

    def test_path_beats_registry(self, tmp_path, rvfind):
        plat = "win32"
        env = {}
        reg_dir = tmp_path / "registry_bin"
        make_tool(reg_dir, "rvio", plat)
        path_dir = tmp_path / "path_bin"
        make_tool(path_dir, "rvio", plat)
        env["PATH"] = str(path_dir)
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat,
                                            home=tmp_path / "home", root=tmp_path / "root",
                                            registry=lambda: str(reg_dir / "rv.exe"))
        assert (bin_dir, source) == (path_dir, "PATH")


# --- candidates() order, directly ---------------------------------------------------------

def test_candidates_order_lists_named_sources_first(tmp_path, rvfind):
    plat = "linux"
    env = {"RV_BIN": str(tmp_path / "rvbin"),
           "RVPUSH_RV_EXECUTABLE_PATH": str(tmp_path / "push" / "rv"),
           "RV_PATH": str(tmp_path / "rvpath" / "rv"),
           "RV_APP_RV": str(tmp_path / "apprv" / "rv"),
           "RV_HOME": str(tmp_path / "rvhome")}
    cands = rvfind.candidates(rv_bin=str(tmp_path / "argbin"), env=env, platform=plat,
                              home=tmp_path / "home", root=tmp_path / "root",
                              registry=lambda: None)
    sources = [c[0] for c in cands]
    expected_prefix = ["--rv-bin", "RV_BIN", "RVPUSH_RV_EXECUTABLE_PATH", "RV_PATH",
                       "RV_APP_RV", "RV_HOME"]
    assert sources[:len(expected_prefix)] == expected_prefix


# --- --rv-bin / RV_BIN forms --------------------------------------------------------------

class TestRvBinForms:
    """--rv-bin (and RV_BIN) accept an install root, a bin folder, an .app bundle or a tool
    file directly."""

    def test_install_root(self, tmp_path, rvfind):
        root_dir = tmp_path / "install_root"
        make_tool(root_dir / "bin", "rvio", "linux")
        bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin=str(root_dir), env={},
                                            platform="linux", home=tmp_path / "home",
                                            root=tmp_path / "root", registry=lambda: None)
        assert (bin_dir, source) == (root_dir / "bin", "--rv-bin")

    def test_bin_folder_directly(self, tmp_path, rvfind):
        bin_dir_path = tmp_path / "just_bin"
        make_tool(bin_dir_path, "rvio", "linux")
        bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin=str(bin_dir_path), env={},
                                            platform="linux", home=tmp_path / "home",
                                            root=tmp_path / "root", registry=lambda: None)
        assert (bin_dir, source) == (bin_dir_path, "--rv-bin")

    def test_app_bundle(self, tmp_path, rvfind):
        app = tmp_path / "OpenRV.app"
        make_tool(app / "Contents" / "MacOS", "rvio", "darwin")
        bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin=str(app), env={},
                                            platform="darwin", home=tmp_path / "home",
                                            root=tmp_path / "root", registry=lambda: None)
        assert (bin_dir, source) == (app / "Contents" / "MacOS", "--rv-bin")

    def test_tool_file(self, tmp_path, rvfind):
        bin_dir_path = tmp_path / "bin_for_file"
        tool_path = make_tool(bin_dir_path, "rvio", "linux")
        bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin=str(tool_path), env={},
                                            platform="linux", home=tmp_path / "home",
                                            root=tmp_path / "root", registry=lambda: None)
        assert (bin_dir, source) == (bin_dir_path, "--rv-bin")

    def test_wrong_rv_bin_raises_with_flag_name(self, tmp_path, rvfind):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(rvfind.RvNotFound, match="--rv-bin"):
            rvfind.find_rv("rvio", rv_bin=str(empty), env={}, platform="linux",
                           home=tmp_path / "home", root=tmp_path / "root",
                           registry=lambda: None)

    def test_wrong_rv_bin_env_raises_with_var_name(self, tmp_path, rvfind):
        empty = tmp_path / "empty2"
        empty.mkdir()
        env = {"RV_BIN": str(empty)}
        with pytest.raises(rvfind.RvNotFound, match="RV_BIN"):
            rvfind.find_rv("rvio", env=env, platform="linux", home=tmp_path / "home",
                           root=tmp_path / "root", registry=lambda: None)


# --- install folder version ordering ------------------------------------------------------

class TestInstallFolderOrdering:
    def test_windows_autodesk_newest_first(self, tmp_path, rvfind):
        plat = "win32"
        pf = tmp_path / "Program Files"
        make_tool(pf / "Autodesk" / "RV-2024.9" / "bin", "rvio", plat)
        make_tool(pf / "Autodesk" / "RV-2024.10" / "bin", "rvio", plat)
        env = {"ProgramFiles": str(pf)}
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat,
                                            home=tmp_path / "home", root=tmp_path / "root",
                                            registry=lambda: None)
        assert (bin_dir, source) == (pf / "Autodesk" / "RV-2024.10" / "bin", "install folder")

    def test_linux_openrv_newest_first(self, tmp_path, rvfind):
        plat = "linux"
        root = tmp_path / "root"
        make_tool(root / "opt" / "OpenRV-3.1" / "bin", "rvio", plat)
        make_tool(root / "opt" / "OpenRV-3.2" / "bin", "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", env={}, platform=plat,
                                            home=tmp_path / "home", root=root,
                                            registry=lambda: None)
        assert (bin_dir, source) == (root / "opt" / "OpenRV-3.2" / "bin", "install folder")

    def test_macos_home_applications(self, tmp_path, rvfind):
        plat = "darwin"
        home = tmp_path / "home"
        make_tool(home / "Applications" / "OpenRV-1.0.app" / "Contents" / "MacOS", "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", env={}, platform=plat, home=home,
                                            root=tmp_path / "root", registry=lambda: None)
        expected = home / "Applications" / "OpenRV-1.0.app" / "Contents" / "MacOS"
        assert (bin_dir, source) == (expected, "install folder")


# --- PATH lookup: host-dependent exe naming ------------------------------------------------

class TestPathLookup:
    """shutil.which() reads the real host PATHEXT/exec bits, not the simulated `platform`
    argument, so a bare (no-extension) name can only be found on a POSIX host."""

    def test_windows_exe_names_found_on_any_host(self, tmp_path, rvfind):
        plat = "win32"
        d = tmp_path / "pathdir_win"
        make_tool(d, "rvio", plat)
        env = {"PATH": str(d)}
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat,
                                            home=tmp_path / "home", root=tmp_path / "root",
                                            registry=lambda: None)
        assert (bin_dir, source) == (d, "PATH")

    def test_posix_bare_names_need_posix_host(self, tmp_path, rvfind):
        if os.name == "nt":
            pytest.skip("bare-name PATH lookup needs a POSIX host (Windows shutil.which "
                        "requires a PATHEXT-matching extension)")
        plat = "linux"
        d = tmp_path / "pathdir_posix"
        make_tool(d, "rvio", plat)
        env = {"PATH": str(d)}
        bin_dir, source, _ = rvfind.find_rv("rvio", env=env, platform=plat,
                                            home=tmp_path / "home", root=tmp_path / "root",
                                            registry=lambda: None)
        assert (bin_dir, source) == (d, "PATH")

    def test_macos_rv_bare_name_needs_posix_host(self, tmp_path, rvfind):
        if os.name == "nt":
            pytest.skip("bare-name PATH lookup needs a POSIX host")
        plat = "darwin"
        d = tmp_path / "pathdir_mac"
        make_tool(d, "rv", plat)
        env = {"PATH": str(d)}
        bin_dir, source, _ = rvfind.find_rv("rv", env=env, platform=plat,
                                            home=tmp_path / "home", root=tmp_path / "root",
                                            registry=lambda: None)
        assert (bin_dir, source) == (d, "PATH")


# --- errors and reporting -----------------------------------------------------------------

def test_nothing_found_lists_what_was_tried(tmp_path, rvfind):
    with pytest.raises(rvfind.RvNotFound) as exc:
        rvfind.find_rv("rvio", env={}, platform="linux", home=tmp_path / "home",
                       root=tmp_path / "root", registry=lambda: None)
    msg = str(exc.value)
    assert "rvio not found" in msg
    for token in ("--rv-bin", "RV_BIN", "RVPUSH_RV_EXECUTABLE_PATH", "RV_PATH", "RV_APP_RV",
                 "RV_HOME", "PATH", "registry", "install"):
        assert token in msg


def test_locate_returns_error_dict_instead_of_raising(tmp_path, rvfind):
    report = rvfind.locate("rvio", env={}, platform="linux", home=tmp_path / "home",
                           root=tmp_path / "root", registry=lambda: None)
    assert report["found"] is False
    assert report["error"]


def test_tools_in_reports_none_for_missing_tools(tmp_path, rvfind):
    folder = tmp_path / "bin"
    make_tool(folder, "rvio", "linux")
    tools = rvfind.tools_in(folder, "linux")
    assert tools["rvio"] is not None
    assert tools["rv"] is None
    assert tools["rvpush"] is None


def test_locate_versions_false_does_not_run_anything(tmp_path, rvfind, monkeypatch):
    folder = tmp_path / "bin"
    make_tool(folder, "rvio", "linux")
    make_tool(folder, "rvls", "linux")

    def boom(*a, **k):
        raise AssertionError("tool_version must not be called when versions=False")

    monkeypatch.setattr(rvfind, "tool_version", boom)
    report = rvfind.locate("rvio", rv_bin=str(folder), versions=False, env={},
                           platform="linux", home=tmp_path / "home", root=tmp_path / "root",
                           registry=lambda: None)
    assert report["found"] is True
    assert report["versions"] == {}


# --- main() ---------------------------------------------------------------------------

class TestMain:
    def test_path_flag_prints_path_and_returns_0(self, tmp_path, rvfind, capsys):
        fake_bin = tmp_path / "bin"
        tool_path = make_tool(fake_bin, "rvls", None)   # None -> name for the real host
        rc = rvfind.main(["--path", "rvls", "--rv-bin", str(fake_bin)])
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out == str(tool_path)

    def test_path_flag_returns_1_when_missing(self, tmp_path, rvfind, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()
        rc = rvfind.main(["--path", "rvls", "--rv-bin", str(empty)])
        capsys.readouterr()
        assert rc == 1
