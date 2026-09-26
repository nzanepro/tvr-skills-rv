# rvpkg commands and output

Read this for the full option list, what each command prints, and a verified transcript of a
package's whole life cycle in a throw-away support area.

## Options (OpenRV 3.1)

| Option | Effect |
|---|---|
| `-list` | list packages in every support area |
| `-info PKG ...` | details of packages |
| `-env` | print the `Packages` folder of every support area, in search order |
| `-add AREA PKGFILE ...` | copy package files into `AREA/Packages` (makes them available, does not install) |
| `-install PKG ...` | install (unpack) packages that are already in an area |
| `-uninstall PKG ...` | remove the unpacked files; the package stays available |
| `-remove PKG ...` | delete the package file from its area |
| `-optin PKG ...` | make installed optional packages load for every user of the area |
| `-include DIR` | also search DIR, as if it were on `RV_SUPPORT_PATH` |
| `-only DIR` | search DIR instead of `RV_SUPPORT_PATH` (the install's own area is still listed; cannot be combined with `-add` or `-include`) |
| `-force` | answer yes to every question; always pass it from scripts |

`PKG` can be the full path of the package file (most reliable), the package's display name
(matches every copy in every area), or the file name (did not match in testing). Commands
can be combined, e.g. `-install -add AREA file.rvpkg`. OpenRV 4 adds `-update`.

All commands exit 0 unless the arguments are malformed, including when nothing matched
("No matching packages found"). An error such as a missing area prints `ERROR: ...` and
`exiting`, with a non-zero exit.

## -list

```text
I L - 1.2 "Additional RV Nodes" <install>/plugins/Packages/additional_nodes-1.2.rvpkg
I - O 2.5 "OpenColorIO Basic Color Management" <install>/plugins/Packages/ocio_source_setup-2.5.rvpkg
- - O 1.2 "Python Example Mode" <area>/Packages/pyhello-1.2.rvpkg
```

Flags: `I` installed, `L` loaded, `O` optional; `-` means no. So `I - O` is installed but
waiting for opt-in, and `- - O` is available but not installed.

OpenRV 3.1 ships 35 packages in its own area, including the OCIO setup, annotation, session
manager, OTIO reader, sync, the rvio overlay scripts, stereo and lat-long viewers, the Nuke
integration and several examples; most optional ones are installed but not opted in.

## -info

```text
Name: Python Example Mode
Version: 1.2
Installed: YES
Loadable: NO
Directory:
Author: Autodesk, Inc.
Requires:
RV-Version: 3.12.9
OpenRV-Version: 1.0.0
Hidden: NO
System: NO
Optional: YES
Writable: YES
Dir-Writable: NO
Modes: pyhello.py
Files: pyhello.py
```

`Dir-Writable: NO` means the area cannot be changed by this user (typical for the install's
own area). `rvpkg_list.py --info` returns these fields as JSON.

## Verified life cycle

Run in an empty folder `AREA` with `RV_SUPPORT_PATH=AREA`, using a copy of the example package
`pyhello-1.2.rvpkg` (each step 0.1 to 0.25 s):

| Command | Output | `-list` afterwards |
|---|---|---|
| `rvpkg -force -add AREA pyhello-1.2.rvpkg` (before creating AREA) | `ERROR: target support directory AREA does not exist: please create it first` / `exiting` | |
| `rvpkg -force -add AREA pyhello-1.2.rvpkg` | (nothing); AREA gains `ConfigFiles Mu Nodes Packages Python SupportFiles ...` | `- - O 1.2 "Python Example Mode" AREA/Packages/pyhello-1.2.rvpkg` |
| `rvpkg -info pyhello-1.2.rvpkg` | `No matching packages found` | |
| `rvpkg -force -install AREA/Packages/pyhello-1.2.rvpkg` | `INFO: installing ...`; adds `Python/pyhello.py`, `Packages/rvinstall`, `Mu/rvload2` | `I - O ...` |
| `rvpkg -force -optin AREA/Packages/pyhello-1.2.rvpkg` | `INFO: opting-in all users for mode pyhello` | `I L - ...` |
| `rvpkg -force -uninstall "Python Example Mode"` | `INFO: Some Files Cannot Be Removed` (empty list); `Python/pyhello.py` removed | `- - O ...` |
| `rvpkg -force -remove AREA/Packages/pyhello-1.2.rvpkg` | (nothing) | package gone |
| `rvpkg -force -install -add AREA pyhello-1.2.rvpkg` | `INFO: installing ...` | `I - O ...` |

The "Some Files Cannot Be Removed" message came from the second copy of the same package in
the install's read-only area, which the display name also matched. Use full paths so a
command touches exactly one copy.

## Scripting notes

- Close stdin (or pass `-force`) so a confirmation prompt can never hang a script;
  `rv_tool.py` does both.
- Check the result with `-list` (or `rvpkg_list.py`), never with rvpkg's exit code.
- Changes apply the next time RV starts; a running RV does not reload packages.
- `-add` then `-install` on a shared area affects everyone who has that area on their
  `RV_SUPPORT_PATH`; ask before changing a shared area.
