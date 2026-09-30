---
name: rvpkg
description: "Manages RV / OpenRV packages (.rvpkg plugins) with the rvpkg command-line tool: lists what is available, installed, loaded or optional in each support area, adds, installs, uninstalls, removes and opts packages in, reads package details, and sets up RV_SUPPORT_PATH areas. Use when an RV plugin or package must be installed, removed, enabled or checked. Not for converting media (rvio), listing image sequences (rvls), or viewing media in RV (rv-review)."
license: MIT
compatibility: Needs RV or OpenRV (rvpkg) and Python 3.9 or later for the helper scripts (standard library only); rvpkg_list.py --parse also works on saved output without RV. Desktop only (Claude Code CLI, desktop app or IDE extension on Windows, macOS or Linux); not claude.ai in a browser or the iOS / Android apps, which cannot run RV on your machine.
metadata:
  version: 0.1.3
---

# Manage RV packages with rvpkg

An RV package is a zip file with the extension `.rvpkg` that holds Mu / Python modes, rvio
overlay scripts, node definitions or other support files plus a `PACKAGE` description. RV
loads packages from its support areas. rvpkg lists, adds, installs, uninstalls, removes and
opts packages in without opening RV.

## Find the tool

```bash
python scripts/rv_find.py --path rvpkg
```

Search order: `--rv-bin`, `rv_bin` in `~/.config/tvr-skills-rv/config.json`, `PATH`, the
Windows registry, the usual install folders, then an OpenRV built from source (`~/OpenRV`,
or the checkout you are in). No shell variable is read. If nothing is found, ask the
user where RV is installed and pass `--rv-bin`.

Plugin setting "RV bin folder": [${user_config.rv_bin}]. When the text between those
brackets is a folder path, pass it to the scripts as `--rv-bin "<that path>"`. When it still
starts with a dollar sign, the setting is empty or the skill was installed on its own: pass
nothing for it, and never pass that text as a path.

## Scripts

Paths are relative to this skill's folder; run each with `--help` first.

| Script | Use |
|---|---|
| `scripts/rvpkg_list.py [--name TEXT] [--info] [--include DIR] [--only DIR]` | runs `rvpkg -list` and `-env`, prints JSON: support areas and every package with installed / loaded / optional flags, version, name, path; `--info` adds author, requirements, modes, files and writability |
| `scripts/rv_tool.py rvpkg -- ARGS` | runs rvpkg with closed stdin and a timeout; exit 3 on "No matching packages found" (rvpkg itself exits 0) |
| `scripts/rv_find.py` | finds rv, rvio, rvls, rvpkg, rvpush |

## The package life cycle

| Step | Command | What changes |
|---|---|---|
| See what exists | `rvpkg -list` | nothing |
| Where RV looks | `rvpkg -env` | nothing |
| Details of one package | `rvpkg -info /path/to/name-1.0.rvpkg` | nothing |
| Make it available in an area | `rvpkg -force -add AREA name-1.0.rvpkg` | copies the file to `AREA/Packages`, creates the area's folders |
| Install it | `rvpkg -force -install AREA/Packages/name-1.0.rvpkg` | unpacks its files into `AREA/Mu`, `AREA/Python`, ...; records it in `AREA/Packages/rvinstall` |
| Add and install in one step | `rvpkg -force -install -add AREA name-1.0.rvpkg` | both of the above |
| Load an optional package for everyone using the area | `rvpkg -force -optin AREA/Packages/name-1.0.rvpkg` | the `L` flag appears in `-list` |
| Uninstall | `rvpkg -force -uninstall AREA/Packages/name-1.0.rvpkg` | removes the unpacked files; the `.rvpkg` stays available |
| Remove | `rvpkg -force -remove AREA/Packages/name-1.0.rvpkg` | deletes the `.rvpkg` from the area |

`-list` rows read `I L O VERSION "Name" PATH`: `I` installed, `L` loaded (RV will load it),
`O` optional (loads only after opt-in); `-` means no.

There is no opt-out command. A user turns an optional package off in RV under Preferences >
Packages (the "Load" check box); for everyone, uninstall it.

## Steps for any change

1. `python scripts/rvpkg_list.py --name TEXT` to see the package's current state and path.
2. Pick the support area: the user area for one person (`~\AppData\Roaming\RV`,
   `~/Library/Application Support/RV`, `~/.rv`), a shared studio folder on `RV_SUPPORT_PATH`
   for a team. The install's own `Packages` folder is normally read-only; do not change it.
3. Run the rvpkg command with `-force` first (it never prompts then) and the package's full
   path, through `scripts/rv_tool.py rvpkg -- ...` so "No matching packages found" fails.
4. Run `rvpkg_list.py` again and confirm the flags changed as intended. The change takes
   effect the next time RV starts.

## Gotchas

- **rvpkg exits 0 when nothing matched.** It prints "No matching packages found" and does
  nothing. `rv_tool.py` turns that into exit 3.
- **Name the package by its full path.** A bare file name such as `name-1.0.rvpkg` did not
  match in testing; the display name (`"Python Example Mode"`) matches every copy in every
  area, so `-info` printed two blocks.
- **`-add` needs the area folder to exist** ("please create it first"), then creates the
  area's subfolders.
- **`-only DIR` does not hide the install's own packages**, cannot be combined with `-add` or
  `-include`, and creates the area folders in DIR as a side effect of `-list`.
- **`RV_SUPPORT_PATH` replaces the default user area**; include the user area in it when you
  set it (`;` separates entries on Windows, `:` elsewhere).
- `-uninstall` may print "Some Files Cannot Be Removed" with an empty list; check with
  `-list` rather than trusting the message.
- Package modes load when RV starts. rvio runs no modes (its start-up script is empty); it
  only uses files that installed packages put on the support path, such as the `-leader` /
  `-overlay` scripts from the `rvio_basic_scripts` package.

## Read when

| Read | When |
|---|---|
| `references/rvpkg-commands.md` | every option, exact outputs, a verified life-cycle transcript |
| `references/support-areas.md` | where packages live, `RV_SUPPORT_PATH`, area layout, studio setups |
| `references/package-format.md` | what is inside an `.rvpkg`, the `PACKAGE` file, building your own |
