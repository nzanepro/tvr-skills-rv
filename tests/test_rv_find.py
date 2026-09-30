"""Tests for rv_find.py (identical copies in rvio/scripts, rvls/scripts, rvpkg/scripts).

rv_find.py is a standalone script (no sibling imports of its own), so it is loaded by file
path with importlib. All discovery-order tests build a fake install tree in tmp_path and pass explicit
platform / home / root / registry / which / cwd, so behaviour is independent of the host OS
and of whatever is really installed on the machine running the suite.
"""
import importlib.util
import json
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


# --- discovery order ------------------------------------------------------------------------

def which_in(folder):
    """A shutil.which stand-in that finds names only in folder (host PATH never leaks in)."""
    folder = Path(folder)

    def which(name):
        p = folder / name
        return str(p) if p.is_file() else None
    return which


def no_which(name):
    return None


def where(tmp_path, **kw):
    """Keyword arguments that keep find_rv away from the real machine: an empty home and
    root, no config, no PATH hits, no registry and a current folder with no OpenRV above."""
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    base = {"home": tmp_path / "home", "root": tmp_path / "root", "registry": lambda: None,
            "which": no_which, "cwd": cwd}
    base.update(kw)
    return base


class TestPrecedenceChain:
    """--rv-bin beats the config file beats PATH beats the registry (Windows) beats the
    install folders beats an OpenRV build."""

    def test_chain(self, tmp_path, rvfind):
        plat = "linux"
        home, root = tmp_path / "home", tmp_path / "root"
        checkout = home / "OpenRV"
        (checkout).mkdir(parents=True)
        (checkout / "rvcmds.sh").write_text("")
        build_dir = checkout / "_build" / "stage" / "app" / "bin"
        make_tool(build_dir, "rvio", plat)
        kw = where(tmp_path, platform=plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", **kw)
        assert (bin_dir, source) == (build_dir, "OpenRV build")

        install_dir = root / "opt" / "rv-1.0" / "bin"
        make_tool(install_dir, "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", **kw)
        assert (bin_dir, source) == (install_dir, "install folder")

        path_dir = tmp_path / "path_bin"
        make_tool(path_dir, "rvio", plat)
        kw["which"] = which_in(path_dir)
        bin_dir, source, _ = rvfind.find_rv("rvio", **kw)
        assert (bin_dir, source) == (path_dir, "PATH")

        config_dir = tmp_path / "config_bin"
        make_tool(config_dir, "rvio", plat)
        rvfind.config_path(home).parent.mkdir(parents=True)
        rvfind.config_path(home).write_text(json.dumps({"rv_bin": str(config_dir)}))
        bin_dir, source, _ = rvfind.find_rv("rvio", **kw)
        assert (bin_dir, source) == (config_dir, "config")

        arg_bin_dir = tmp_path / "arg_bin_dir"
        make_tool(arg_bin_dir, "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin=str(arg_bin_dir), **kw)
        assert (bin_dir, source) == (arg_bin_dir, "--rv-bin")

    def test_candidates_order(self, tmp_path, rvfind):
        path_dir = tmp_path / "path_bin"
        make_tool(path_dir, "rvio", "linux")
        cands = rvfind.candidates(rv_bin=str(tmp_path / "argbin"),
                                  config={"rv_bin": str(tmp_path / "cfgbin")},
                                  **where(tmp_path, platform="linux", which=which_in(path_dir)))
        assert [c[0] for c in cands][:3] == ["--rv-bin", "config", "PATH"]


class TestRegistryPrecedence:
    """Registry is windows-only, and sits between PATH and the install folders."""

    def test_registry_beats_install_folder(self, tmp_path, rvfind):
        plat = "win32"
        pf = tmp_path / "root" / "Program Files"
        install_dir = pf / "OpenRV-1.0" / "bin"
        make_tool(install_dir, "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", **where(tmp_path, platform=plat))
        assert (bin_dir, source) == (install_dir, "install folder")

        reg_dir = tmp_path / "registry_bin"
        make_tool(reg_dir, "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", **where(
            tmp_path, platform=plat, registry=lambda: str(reg_dir / "rv.exe")))
        assert (bin_dir, source) == (reg_dir, "registry")

    def test_path_beats_registry(self, tmp_path, rvfind):
        plat = "win32"
        reg_dir = tmp_path / "registry_bin"
        make_tool(reg_dir, "rvio", plat)
        path_dir = tmp_path / "path_bin"
        make_tool(path_dir, "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", **where(
            tmp_path, platform=plat, which=which_in(path_dir),
            registry=lambda: str(reg_dir / "rv.exe")))
        assert (bin_dir, source) == (path_dir, "PATH")

    def test_registry_not_consulted_on_linux(self, tmp_path, rvfind):
        reg_dir = tmp_path / "registry_bin"
        make_tool(reg_dir, "rvio", "linux")
        with pytest.raises(rvfind.RvNotFound):
            rvfind.find_rv("rvio", **where(tmp_path, platform="linux",
                                           registry=lambda: str(reg_dir / "rvio")))


# --- --rv-bin / config forms ------------------------------------------------------------------

class TestRvBinForms:
    """--rv-bin (and rv_bin in the config file) accept an install root, a bin folder, an .app
    bundle or a tool file directly."""

    def test_install_root(self, tmp_path, rvfind):
        root_dir = tmp_path / "install_root"
        make_tool(root_dir / "bin", "rvio", "linux")
        bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin=str(root_dir),
                                            **where(tmp_path, platform="linux"))
        assert (bin_dir, source) == (root_dir / "bin", "--rv-bin")

    def test_bin_folder_directly(self, tmp_path, rvfind):
        bin_dir_path = tmp_path / "just_bin"
        make_tool(bin_dir_path, "rvio", "linux")
        bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin=str(bin_dir_path),
                                            **where(tmp_path, platform="linux"))
        assert (bin_dir, source) == (bin_dir_path, "--rv-bin")

    def test_app_bundle(self, tmp_path, rvfind):
        app = tmp_path / "OpenRV.app"
        make_tool(app / "Contents" / "MacOS", "rvio", "darwin")
        bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin=str(app),
                                            **where(tmp_path, platform="darwin"))
        assert (bin_dir, source) == (app / "Contents" / "MacOS", "--rv-bin")

    def test_tool_file(self, tmp_path, rvfind):
        bin_dir_path = tmp_path / "bin_for_file"
        tool_path = make_tool(bin_dir_path, "rvio", "linux")
        bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin=str(tool_path),
                                            **where(tmp_path, platform="linux"))
        assert (bin_dir, source) == (bin_dir_path, "--rv-bin")

    def test_wrong_rv_bin_raises_with_flag_name(self, tmp_path, rvfind):
        empty = tmp_path / "empty"
        empty.mkdir()
        path_dir = tmp_path / "path_bin"
        make_tool(path_dir, "rvio", "linux")
        with pytest.raises(rvfind.RvNotFound, match="--rv-bin"):
            rvfind.find_rv("rvio", rv_bin=str(empty),
                           **where(tmp_path, platform="linux", which=which_in(path_dir)))

    def test_wrong_config_rv_bin_raises_naming_the_file(self, tmp_path, rvfind):
        empty = tmp_path / "empty2"
        empty.mkdir()
        path_dir = tmp_path / "path_bin"
        make_tool(path_dir, "rvio", "linux")
        with pytest.raises(rvfind.RvNotFound, match="config.json"):
            rvfind.find_rv("rvio", config={"rv_bin": str(empty)},
                           **where(tmp_path, platform="linux", which=which_in(path_dir)))

    def test_config_tilde_is_the_home_folder(self, tmp_path, rvfind):
        home = tmp_path / "home"
        make_tool(home / "rv" / "bin", "rvio", "linux")
        bin_dir, source, _ = rvfind.find_rv("rvio", config={"rv_bin": "~/rv"},
                                            **where(tmp_path, platform="linux"))
        assert (bin_dir, source) == (home / "rv" / "bin", "config")


class TestConfigFile:
    def test_path_is_under_the_home_folder(self, tmp_path, rvfind):
        assert rvfind.config_path(tmp_path) == tmp_path / ".config" / "tvr-skills-rv" / "config.json"

    def test_missing_file_is_empty(self, tmp_path, rvfind):
        assert rvfind.load_config(tmp_path) == {}

    @pytest.mark.parametrize("text", ["{not json", "[1, 2]", '{"rv_bin": 3}'])
    def test_broken_file_is_an_error_naming_it(self, tmp_path, rvfind, text):
        path = rvfind.config_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text(text)
        with pytest.raises(rvfind.ConfigError, match="config.json"):
            rvfind.load_config(tmp_path)
        with pytest.raises(rvfind.RvNotFound, match="config file problem"):
            rvfind.find_rv("rvio", **where(tmp_path, platform="linux", home=tmp_path))

    def test_broken_file_is_not_read_when_rv_bin_answers(self, tmp_path, rvfind):
        path = rvfind.config_path(tmp_path / "home")
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        folder = tmp_path / "bin"
        make_tool(folder, "rvio", "linux")
        bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin=str(folder),
                                            **where(tmp_path, platform="linux"))
        assert (bin_dir, source) == (folder, "--rv-bin")


# --- OpenRV built from source -------------------------------------------------------------------

class TestOpenRvBuild:
    """The openrv-build plugin leaves a source checkout (with rvcmds.sh at its top) and a
    staged build under _build/stage/app; nothing else marks it."""

    def _checkout(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "rvcmds.sh").write_text("")
        return folder

    def test_macos_app_in_home_openrv(self, tmp_path, rvfind):
        checkout = self._checkout(tmp_path / "home" / "OpenRV")
        macos = checkout / "_build" / "stage" / "app" / "RV.app" / "Contents" / "MacOS"
        make_tool(macos, "rv", "darwin")
        bin_dir, source, _ = rvfind.find_rv("rv", **where(tmp_path, platform="darwin"))
        assert (bin_dir, source) == (macos, "OpenRV build")

    def test_linux_bin_found_from_a_folder_inside_the_checkout(self, tmp_path, rvfind):
        checkout = self._checkout(tmp_path / "src" / "OpenRV")
        build_bin = checkout / "_build" / "stage" / "app" / "bin"
        make_tool(build_bin, "rvio", "linux")
        inside = checkout / "src" / "lib"
        inside.mkdir(parents=True)
        bin_dir, source, _ = rvfind.find_rv("rvio", **where(tmp_path, platform="linux",
                                                            cwd=inside))
        assert (bin_dir, source) == (build_bin, "OpenRV build")

    def test_windows_drive_openrv(self, tmp_path, rvfind):
        checkout = self._checkout(tmp_path / "root" / "OpenRV")
        build_bin = checkout / "_build" / "stage" / "app" / "bin"
        make_tool(build_bin, "rvio", "win32")
        bin_dir, source, _ = rvfind.find_rv("rvio", **where(tmp_path, platform="win32"))
        assert (bin_dir, source) == (build_bin, "OpenRV build")

    def test_folder_without_marker_is_not_a_build(self, tmp_path, rvfind):
        folder = tmp_path / "home" / "OpenRV"
        make_tool(folder / "_build" / "stage" / "app" / "bin", "rvio", "linux")
        with pytest.raises(rvfind.RvNotFound, match="OpenRV build"):
            rvfind.find_rv("rvio", **where(tmp_path, platform="linux"))

    def test_roots_are_cwd_parents_then_home_then_windows_drive(self, tmp_path, rvfind):
        cwd = tmp_path / "a" / "b"
        roots = rvfind.openrv_build_roots("win32", home=tmp_path / "home",
                                          root=tmp_path / "root", cwd=cwd)
        assert roots[:3] == [cwd, cwd.parent, cwd.parent.parent]
        assert roots[-2:] == [tmp_path / "home" / "OpenRV", tmp_path / "root" / "OpenRV"]


# --- Windows folders ---------------------------------------------------------------------------

def test_program_files_under_a_test_root_are_literal(tmp_path, rvfind):
    assert rvfind.program_files_dirs(tmp_path) == [tmp_path / "Program Files",
                                                   tmp_path / "Program Files (x86)"]


@pytest.mark.skipif(os.name == "nt", reason="known folders exist on Windows")
def test_known_folder_is_none_off_windows(rvfind):
    assert rvfind.known_folder(rvfind.FOLDERID_PROGRAM_FILES[0]) is None


def test_install_patterns_take_injected_program_files(tmp_path, rvfind):
    pats = rvfind.install_patterns("win32", program_files=lambda: [tmp_path / "PF"])
    assert str(tmp_path / "PF" / "OpenRV*" / "bin") in pats
    assert all(p.startswith(str(tmp_path / "PF")) for p in pats)


# --- install folder version ordering ------------------------------------------------------

class TestInstallFolderOrdering:
    def test_windows_autodesk_newest_first(self, tmp_path, rvfind):
        plat = "win32"
        pf = tmp_path / "root" / "Program Files"
        make_tool(pf / "Autodesk" / "RV-2024.9" / "bin", "rvio", plat)
        make_tool(pf / "Autodesk" / "RV-2024.10" / "bin", "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", **where(tmp_path, platform=plat))
        assert (bin_dir, source) == (pf / "Autodesk" / "RV-2024.10" / "bin", "install folder")

    def test_windows_program_files_x86(self, tmp_path, rvfind):
        plat = "win32"
        pf86 = tmp_path / "root" / "Program Files (x86)"
        make_tool(pf86 / "Shotgun" / "RV-7.0" / "bin", "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", **where(tmp_path, platform=plat))
        assert (bin_dir, source) == (pf86 / "Shotgun" / "RV-7.0" / "bin", "install folder")

    def test_linux_openrv_newest_first(self, tmp_path, rvfind):
        plat = "linux"
        root = tmp_path / "root"
        make_tool(root / "opt" / "OpenRV-3.1" / "bin", "rvio", plat)
        make_tool(root / "opt" / "OpenRV-3.2" / "bin", "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", **where(tmp_path, platform=plat))
        assert (bin_dir, source) == (root / "opt" / "OpenRV-3.2" / "bin", "install folder")

    def test_macos_home_applications(self, tmp_path, rvfind):
        plat = "darwin"
        home = tmp_path / "home"
        make_tool(home / "Applications" / "OpenRV-1.0.app" / "Contents" / "MacOS", "rvio", plat)
        bin_dir, source, _ = rvfind.find_rv("rvio", **where(tmp_path, platform=plat))
        expected = home / "Applications" / "OpenRV-1.0.app" / "Contents" / "MacOS"
        assert (bin_dir, source) == (expected, "install folder")


# --- PATH lookup through the real shutil.which ---------------------------------------------------

class TestPathLookup:
    """Without an injected which, the lookup is shutil.which itself; the test points it at
    one folder by patching the module's shutil.which."""

    def test_default_which_is_shutil_which(self, tmp_path, rvfind, monkeypatch):
        d = tmp_path / "pathdir"
        tool = make_tool(d, "rvio", None)          # None -> name for the real host
        calls = []

        def fake_which(name):
            calls.append(name)
            return str(tool) if name == tool.name else None
        monkeypatch.setattr(rvfind.shutil, "which", fake_which)
        kw = where(tmp_path)
        del kw["which"]
        bin_dir, source, _ = rvfind.find_rv("rvio", **kw)
        assert (bin_dir, source) == (d, "PATH")
        assert tool.name in calls


# --- errors and reporting -----------------------------------------------------------------

def test_nothing_found_lists_what_was_tried(tmp_path, rvfind):
    with pytest.raises(rvfind.RvNotFound) as exc:
        rvfind.find_rv("rvio", **where(tmp_path, platform="linux"))
    msg = str(exc.value)
    assert "rvio not found" in msg
    for token in ("--rv-bin", "config.json", "PATH", "registry", "install", "OpenRV build"):
        assert token in msg


def test_locate_returns_error_dict_instead_of_raising(tmp_path, rvfind):
    report = rvfind.locate("rvio", **where(tmp_path, platform="linux"))
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
    report = rvfind.locate("rvio", rv_bin=str(folder), versions=False,
                           **where(tmp_path, platform="linux"))
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


def test_unsubstituted_plugin_option_is_not_a_path(tmp_path, rvfind):
    """An agent that passes the unset option's placeholder as --rv-bin gets the normal search."""
    path_dir = tmp_path / "path_bin"
    make_tool(path_dir, "rvio", "linux")
    placeholder = "$" + "{user_config.rv_bin}"
    bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin=placeholder,
                                        **where(tmp_path, platform="linux", which=which_in(path_dir)))
    assert (bin_dir, source) == (path_dir, "PATH")


def test_config_file_with_a_byte_order_mark_is_read(tmp_path, rvfind):
    """PowerShell 5.1's Set-Content -Encoding utf8 writes a byte-order mark first."""
    path = rvfind.config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xef\xbb\xbf" + b'{"rv_bin": "/opt/rv/bin"}')
    assert rvfind.load_config(tmp_path) == {"rv_bin": "/opt/rv/bin"}


def test_rv_bin_flag_expands_a_leading_tilde(tmp_path, rvfind):
    """A quoted ~ reaches the script unexpanded; --rv-bin treats it like the config file does."""
    home = tmp_path / "home"
    make_tool(home / "rv" / "bin", "rvio", "linux")
    bin_dir, source, _ = rvfind.find_rv("rvio", rv_bin="~/rv", **where(tmp_path, platform="linux"))
    assert (bin_dir, source) == (home / "rv" / "bin", "--rv-bin")
