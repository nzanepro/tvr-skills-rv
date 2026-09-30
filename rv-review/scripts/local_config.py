"""Where things live on this computer, for the rv-review scripts, without reading any
shell or system variable.

- The config file ~/.config/tvr-skills-rv/config.json: one JSON object of strings, all
  optional, for example
      {"rv_bin": "/opt/rv/bin", "chrome": "/usr/bin/chromium",
       "playwright_browsers": "~/pw-browsers", "adb": "~/Android/Sdk/platform-tools/adb"}
  A leading ~ in a value is the home folder. A command-line flag (--rv-bin, --chrome,
  --playwright-browsers, --adb) wins over the file.
- Windows folders (Program Files, Local and Roaming AppData) from the shell's known-folder
  API, with the standard locations under the home folder or C:/ as the fallback.
- An OpenRV built from source by the openrv-build plugin: a checkout with rvcmds.sh at its
  top and the build under _build/stage/app.

Standard library only; imported by rv_review.py, web_capture.py, app_capture.py and
rasterize.py from the same folder.
"""
import json
import sys
from pathlib import Path

CONFIG_PARTS = (".config", "tvr-skills-rv", "config.json")    # under the home folder
OPENRV_MARKER = "rvcmds.sh"               # at the top of an OpenRV source checkout
# Windows KNOWNFOLDERIDs
FOLDERID_PROGRAM_FILES = ("905e63b6-c1bf-494e-b29c-65b732d3d21a",    # this process's
                          "6d809377-6af0-444b-8957-a3773f02200e",    # 64-bit
                          "7c5a40ef-a0fb-4bfc-874a-c0f2e0b9fa8e")    # 32-bit
FOLDERID_LOCAL_APPDATA = "f1b32785-6fba-4fcf-9d55-7b8e7f157091"
FOLDERID_ROAMING_APPDATA = "3eb685db-65f9-4cf6-a03a-e3ef65729f3d"


class ConfigError(ValueError):
    """The config file exists but is not a JSON object of strings."""


def os_kind(platform=None):
    """'windows', 'macos' or 'linux' for a sys.platform value (default: this machine)."""
    p = platform or sys.platform
    if p.startswith("win"):
        return "windows"
    if p == "darwin":
        return "macos"
    return "linux"


def _home(home=None):
    return Path.home() if home is None else Path(home)


# --- config file --------------------------------------------------------------------------

def config_path(home=None):
    """~/.config/tvr-skills-rv/config.json (home: the home folder; default Path.home())."""
    return _home(home).joinpath(*CONFIG_PARTS)


def load_config(home=None):
    """Settings from the config file: {} when there is none; ConfigError when it is broken."""
    path = config_path(home)
    if not path.is_file():
        return {}
    try:
        # utf-8-sig: PowerShell 5.1 writes a byte-order mark with -Encoding utf8
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{path} is not valid JSON ({exc}); fix it or delete it") from None
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise ConfigError(f'{path} must hold one JSON object of strings, such as '
                          f'{{"rv_bin": "/opt/rv/bin"}}; fix it or delete it')
    return data


def expand_home(value, home=None):
    """value with a leading ~ (alone, or before / or \\) made the home folder. Used for the
    config file's values and for path flags alike, since a quoted ~ reaches the script as is."""
    value = str(value).strip()
    if value == "~" or value.startswith(("~/", "~\\")):
        return str(_home(home) / value[2:])
    return value


def config_value(config, key, home=None):
    """config[key] as a path string, with a leading ~ made the home folder; None if unset."""
    value = (config or {}).get(key, "").strip()
    return expand_home(value, home) if value else None


def setting(flag_value, key, config=None, home=None):
    """(value, source): a command-line flag first, then the config file, else (None, None).
    config: settings dict (default: read the config file; ConfigError when it is broken)."""
    if flag_value:
        return expand_home(flag_value, home), "flag"
    if config is None:
        config = load_config(home)
    value = config_value(config, key, home)
    return (value, "config") if value else (None, None)


# --- Windows folders ----------------------------------------------------------------------

def known_folder(guid):
    """A Windows known folder from SHGetKnownFolderPath, or None (always None elsewhere)."""
    try:
        import ctypes
        import uuid
        from ctypes import wintypes
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
    except (ImportError, AttributeError, OSError):
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    u = uuid.UUID(guid)
    g = GUID(u.fields[0], u.fields[1], u.fields[2],
             (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
    out = ctypes.c_wchar_p()
    try:
        if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
            return None
        value = out.value
        ole32.CoTaskMemFree(out)
    except (OSError, AttributeError, ValueError):
        return None
    return Path(value) if value else None


def program_files_dirs(root="/"):
    """Program Files folders to search on Windows: the known folders, then C:/Program Files
    and C:/Program Files (x86). Under a test root only the root's own folders are used."""
    dirs = []
    if str(root) == "/":
        for guid in FOLDERID_PROGRAM_FILES:
            p = known_folder(guid)
            if p and p not in dirs:
                dirs.append(p)
        base = Path("C:/")
    else:
        base = Path(root)
    for name in ("Program Files", "Program Files (x86)"):
        if base / name not in dirs:
            dirs.append(base / name)
    return dirs


def local_appdata(home=None, lookup=None):
    """Windows Local AppData: the known folder, else ~/AppData/Local. lookup: known_folder
    stand-in for tests."""
    found = (lookup or known_folder)(FOLDERID_LOCAL_APPDATA) if home is None else None
    return found or _home(home) / "AppData" / "Local"


def roaming_appdata(home=None, lookup=None):
    """Windows Roaming AppData: the known folder, else ~/AppData/Roaming."""
    found = (lookup or known_folder)(FOLDERID_ROAMING_APPDATA) if home is None else None
    return found or _home(home) / "AppData" / "Roaming"


# --- OpenRV built from source ---------------------------------------------------------------

def openrv_build_roots(platform=None, home=None, root="/", cwd=None):
    """Folders that may be an OpenRV source checkout: the current folder and its parents,
    ~/OpenRV and, on Windows, C:/OpenRV (the openrv-build plugin's default places)."""
    cwd = Path.cwd() if cwd is None else Path(cwd)
    out = [cwd] + list(cwd.parents)
    out.append(_home(home) / "OpenRV")
    if os_kind(platform) == "windows":
        out.append((Path("C:/") if str(root) == "/" else Path(root)) / "OpenRV")
    return out


def openrv_build_bin(checkout, platform=None):
    """The staged bin folder of an OpenRV build in checkout."""
    app = Path(checkout) / "_build" / "stage" / "app"
    if os_kind(platform) == "macos":
        return app / "RV.app" / "Contents" / "MacOS"
    return app / "bin"


def openrv_build_dir(platform=None, home=None, root="/", cwd=None):
    """Bin folder of the first OpenRV checkout found (one that holds rvcmds.sh), or None."""
    for folder in openrv_build_roots(platform, home, root, cwd):
        try:
            if (folder / OPENRV_MARKER).is_file():
                return openrv_build_bin(folder, platform)
        except OSError:
            continue
    return None
