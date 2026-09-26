# Inside an .rvpkg and building one

Read this when you need to know what a package contains, check its requirements before
installing it, or make a small package of your own (for example to ship a custom rvio overlay
script or a Python mode to a team).

## The archive

An `.rvpkg` is a plain zip archive named `name-VERSION.rvpkg`, with every file at the top
level (no folders). Inspect one without installing it:

```bash
python -m zipfile -l name-1.0.rvpkg                                        # list files
python -c "import zipfile,sys; print(zipfile.ZipFile(sys.argv[1]).read('PACKAGE').decode())" name-1.0.rvpkg
```

## PACKAGE

A YAML file. Example from the example package that ships with OpenRV:

```yaml
package: Python Example Mode
author: Example Author
organization: Example Org
version: 1.2
requires: ''
rv: 3.12.9
openrv: 1.0.0
optional: true

modes:
  - file: pyhello.py
    menu: 'Tools/PYHELLO'
    shortcut: ''
    event: ''
    load: immediate

description: |
    <p>This is a simple python package example</p>
```

| Field | Meaning |
|---|---|
| `package` | display name (what `-list` shows in quotes) |
| `version` | must match the version in the file name |
| `rv`, `openrv` | minimum RV / OpenRV versions |
| `requires` | other packages (by file name) that must be installed first |
| `optional` | `true`: installed packages load only after opt-in |
| `system`, `hidden` | shipped with RV / not shown in the preferences list |
| `modes` | Mu or Python files RV loads as modes: `file`, `menu`, `shortcut`, `event`, `load` (`immediate` or `delay`), optional `requires` |
| `files` | other files and where they go, e.g. `- file: notes.txt` with `location: SupportFiles/$PACKAGE` (`$PACKAGE` is the file name without the version) |
| `description` | HTML shown in RV's preferences |

In a test package, a `.mu` file without an entry went to `Mu/`, a `.py` mode to `Python/`,
and `notes.txt` with `location: SupportFiles/$PACKAGE` to `SupportFiles/demopkg/`. Give every
file that is not a Mu or Python module a `files` entry.

## Building a package

1. Put the code and a `PACKAGE` file in a folder, with no sub-folders.
2. Zip the files (not the folder) as `name-VERSION.rvpkg`:
   `python -c "import zipfile,sys,os; z=zipfile.ZipFile(sys.argv[1],'w'); [z.write(f, os.path.basename(f)) for f in sys.argv[2:]]" mypkg-1.0.rvpkg PACKAGE mymode.py`
3. Test it in a throw-away area first:
   set `RV_SUPPORT_PATH` to an empty folder, then
   `rvpkg -force -install -add <folder> mypkg-1.0.rvpkg` and `rvpkg -list`.
4. For an rvio overlay script, put `NAME.mu` (with `module: NAME` inside) in the package;
   after install it lands in `AREA/Mu/NAME.mu` and `rvio ... -overlay NAME args` finds it,
   even while an optional package is not opted in (see the rvio skill's
   `slates-overlays.md` for the function signature). Verified with a copy of `watermark.mu`
   renamed `demomark`: `rvio "shot.#.png" -t 1010 -overlay demomark "Custom Overlay" .8
   -o demo.png` drew the text, with `RV_SUPPORT_PATH` set to the test area.

The OpenRV source tree has more examples under `src/plugins/rv-packages/` on GitHub
(`AcademySoftwareFoundation/OpenRV`).
