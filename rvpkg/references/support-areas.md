# Support areas and RV_SUPPORT_PATH

Read this when deciding where a package should live, setting up a shared studio area, or
working out why RV does or does not see a package.

## Default areas

`rvpkg -env` prints each area's `Packages` folder in search order. Without `RV_SUPPORT_PATH`:

| OS | User area | Install area (read-only for normal users) |
|---|---|---|
| Windows | `%APPDATA%\RV` | `<install>\plugins` |
| macOS | `~/Library/Application Support/RV` | inside the app bundle |
| Linux | `~/.rv` | `<install>/plugins` |

## RV_SUPPORT_PATH

- A list of area folders, separated by `;` on Windows and `:` on macOS and Linux.
- It replaces the user area. The install's own area is still appended, so the built-in
  packages stay visible. Put the user area in the list yourself if it should still count:
  `RV_SUPPORT_PATH=/studio/rv/support:~/.rv` (the shell expands a `~` after the `:`).
- The same package can exist in several areas; `-list` shows every copy with its path.
- `rvpkg -include DIR` adds one area for a single command; `-only DIR` uses DIR instead of
  the variable (the install area is still listed).

## Area layout

`rvpkg -add` creates these folders the first time it writes to an area:

```text
AREA/
  Packages/       .rvpkg files, rvinstall (installed list)
  Mu/             Mu modes and rvio leader / overlay scripts; rvload, rvload2 (load list)
  Python/         Python modes
  SupportFiles/   per-package data files
  ConfigFiles/  ImageFormats/  MovieFormats/  Nodes/  OIIO/  Output/  Profiles/
  MediaLibrary/  lib/
```

Only rvpkg should edit `rvinstall`, `rvload` and `rvload2`.

## Choosing an area

| Situation | Area |
|---|---|
| One user tries a package | the user area, named explicitly: `rvpkg -force -install -add <user area> file.rvpkg` |
| A team shares packages | a shared folder on every machine's `RV_SUPPORT_PATH`; `-add` and `-install` there once, then `-optin` if the package is optional and everyone should get it |
| A test or CI run | an empty temporary folder set as `RV_SUPPORT_PATH` for that process only |
| Built-in packages | leave the install area alone; opt in or out per user instead |

## Related settings

- `RV_PREFS_OVERRIDE_PATH` and `RV_PREFS_CLOBBER_PATH` point at folders of preference files
  that provide defaults or force values for every user, so studio-wide settings do not
  require editing each user's prefs.
- A package's Python code can find its own support files under `SupportFiles/<package>` in
  the area where it was installed.
