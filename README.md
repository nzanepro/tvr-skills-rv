# tvr-skills-rv

A [Claude Code](https://docs.claude.com/en/docs/claude-code) skill for reviewing images in
[RV / OpenRV](https://github.com/AcademySoftwareFoundation/OpenRV).

`rv-review` turns stacked comparison sheets (several renders of the same view, one above the
other, each with a label) into equal-size labelled frames and loads them into a single RV session
as a flip-book: every view's versions back to back, with a timeline mark at the start of each
view. Stepping frames flips between versions in place; Alt+Left / Alt+Right jumps between views.
If the review window is still open, the next review replaces its contents instead of opening
another window.

## Contents

| Path | What it is |
|---|---|
| `rv-review/SKILL.md` | the skill: when to use it, the steps, and RV gotchas |
| `rv-review/scripts/sheet_panels.py` | splits sheets into labelled, equal-size frames (`split`), or labels unstacked renders (`label`); writes `frames.json` |
| `rv-review/scripts/rv_review.ps1` | opens the frames in RV, or replaces the contents of the running review window via `rvpush`; sets sequence view, 1 fps, stopped, view marks |
| `tests/` | tests for the frame helper |

## Install

Copy or link the `rv-review` folder into your Claude Code skills folder
(`~/.claude/skills/` for every project, or `<project>/.claude/skills/` for one project):

```bash
git clone <this repo> tvr-skills-rv
cp -r tvr-skills-rv/rv-review ~/.claude/skills/
```

On Windows a directory junction keeps the installed skill in step with the clone:

```powershell
cmd /c mklink /J "$env:USERPROFILE\.claude\skills\rv-review" "<clone>\rv-review"
```

## Requirements

- RV or OpenRV. The launcher looks for `rv.exe` via `-RvBin`, the `RV_BIN` environment variable,
  `PATH`, then the usual install folders under Program Files.
- Python 3 with `numpy` and `Pillow`.
- Windows PowerShell 5.1 or later for the launcher. The frame helper is cross-platform; on macOS
  or Linux, call `rv` and `rvpush` directly as described in the skill's Gotchas section.

## Usage outside Claude

```bash
python rv-review/scripts/sheet_panels.py split view1_sheet.png view2_sheet.png --out rv_frames
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File rv-review\scripts\rv_review.ps1 -FramesJson rv_frames\frames.json
```

## Tests

```bash
python -m pytest tests -q
```
