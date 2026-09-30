# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). A release is numbered by the plugin version,
`version` in `.claude-plugin/plugin.json` (the top-level `version` in
`.claude-plugin/marketplace.json` matches it), and tagged `vX.Y.Z`. Each skill also has its
own version, `metadata.version` in its `SKILL.md`, which changes only when that skill changes,
so a skill's version can be lower than the plugin's; each release's "Versions" line lists the
skill versions it changed.

## [0.2.2] - 2026-09-26

### Fixed

- `rv-review`: headless Chrome is no longer waited on until it exits. `web_capture.py` and `rasterize.py` stop the browser and its helper processes once the screenshot PNG is complete, so builds that never exit after `--screenshot` (Chrome 153 on macOS) no longer report every capture as failed after 120 s. A `chrome-headless-shell` (on PATH, or Playwright's, newest first) is preferred after `CHROME_PATH`, each capture's temporary profile is removed, and `rasterize.py --help` documents `CHROME_PATH`.
- `app_capture.py ios` passes absolute paths to `simctl io screenshot` (a relative `--out` failed with Xcode 26.4) and restores the text size when simctl reports it as `Small` / `extra-Small`. A setting that cannot be read or restored is reported in `warnings`, and `--dry-run` lists the restore commands.
- A truncated or corrupt still loaded with `ok: true` because RV shows "error reading" on screen but logs nothing. `rv_review.py` now decodes every still and the first frame of every sequence before the load (header and end checks, plus a full decode of PNG / JPEG / GIF / BMP / WebP when Pillow is installed). Failures still load but are listed in `errors` with exit 3. `--no-decode-check` skips the check.
- `--export-annotated` wrote linear values (frames too dark); rvio now runs with `-outsrgb`, and the exported pixels match the source.
- When the RV it launched exits before answering, the launcher now says "RV exited after N s (exit code X)" instead of "did not answer within 60 s". The `RvNetwork: no session for incoming connection` / `connection aborted reading greeting` lines that its own polling causes, and RV's "trying brute force to find an image reader" line, no longer count as load errors.
- `errors` and `warnings` are always lists, also in `--state`, `--notes` and error results.
- `scripts/check_repo.py` lists files with `git ls-files`, so an in-repo `.venv` no longer fails the check or its tests. Without git it skips `.venv`, `venv`, `.tox`, `node_modules`, `dist`, `build` and `.mypy_cache`.

### Changed

- Frame labels made by `compare_dirs.py`, `review_set.py` and `sheet_panels.py label` sit in the title band under the title instead of over the image. The band, fonts and label box grow with the image (for example 1440 px pages and 1206 x 2622 phone captures), so they stay readable when RV fits the frame to its window. `sheet_panels.py split` is unchanged.
- `review_set.py` orders the sub-folders of a ROOT in review order: before, after / baseline, candidate / old, new / expected, actual, and breakpoints by width. Screens named `<page>__<breakpoint>` go in width order. `--order` still wins.
- `compare_dirs.py` measures 16-bit and float greyscale pairs on every level (`measured_depth: "full"`); other inputs, including 16-bit colour that Pillow reads as 8 bits, are measured at 8 bits, as now documented.
- After a refresh the review window is raised (best effort); SKILL.md notes that on macOS a covered RV window may not repaint.
- Every raw `rvpush` example in the references sets `RVPUSH_RV_EXECUTABLE_PATH=none`, because a plain rvpush starts a new RV when none answers.
- Deprecated `Image.fromarray(arr, mode)` calls replaced (removed in Pillow 13).
- Versions: rv-review 0.2.2.

## [0.2.1] - 2026-09-26

### Fixed

- `rv-review`: `--compare wipe` now opens split down the middle (first source on the left, second on the right) by setting the top source's `stencil.visibleBox`; before, RV turned the wipes mode on but showed only the first source until the edge was dragged. Saved wipe sessions (`--save-session`, `rv_session.py write --layout wipe`) carry the same split, and rvio renders it.
- After a wipe, difference, over or replace load the RV window is named after the first item and the layout instead of "Untitled".

### Added

- The read-back reports `composite`, `wipe` and `wipeBox` for stack layouts and checks them (and the stack view type) against the requested layout, so `ok` covers the layout, not only the node type.
- `rv_review.py --selftest`: sends Left / Right / Alt+Left / Alt+Right to the review window through RV's event tables, checks that each moves the frame as documented, restores the starting frame and prints JSON. It needs no keyboard focus and, on macOS, no Accessibility permission.
- CI runs the tests on Python 3.9 as well as 3.10 and 3.13; `tests/test_py39_compat.py` checks 3.9 syntax and flags newer runtime APIs.
- `rvio`: verified results for OpenRV 3.0.0 on macOS (Apple silicon), with its codec table: writes mjpeg, mpeg4, png, mpeg1video, cfhd, v210, v410, jpeg2000 and tiff, and refuses the others with "Unsupported codec" or "Invalid video codec" depending on the codec.

### Changed

- Every skill needs Python 3.9 or later (was 3.10+ for rv-review and 3.8+ for the others); the whole suite also runs on 3.9.
- Key tables say "Alt (Option on macOS)" and note that Alt+Right with no later mark goes to the last frame.
- Versions: rv-review 0.2.1; rvio, rvls and rvpkg 0.1.1.

## [0.2.0] - 2026-09-26

### Added

- `rv-review`: baseline vs candidate comparison (`compare_dirs.py`): pairs two folders, two files or a test tool's output (Playwright, jest-image-snapshot, Unity Graphics Test Framework, Unreal automation report, Flutter goldens), measures differences, and writes labelled baseline / candidate / absolute-difference frames of the changed pairs, most changed first, with `frames.json`, `compare_report.json` / `.md` and CI exit codes (0 same, 1 changed, 2 error).
- `review_set.py`: one screen across many variants (devices, light / dark, text sizes, locales, states) back to back with a mark per screen.
- UI, app and web capture: `web_capture.py` (pages at named breakpoints through Playwright or an installed Chrome / Edge), `app_capture.py` (iOS Simulator, Android, Electron; appearance, text size, display size, locale), `rasterize.py` (SVG to PNG through resvg, rsvg-convert, CairoSVG, Inkscape, Playwright or Chrome). Nothing is installed; backends are detected.
- `.rv` session writer (`rv_session.py write|check|render`, standard library): sequences with marks, wipe / difference stacks, tile, per-source in / out, fps, views, text annotations, and gtoinfo checks and rvio rendering. The launcher opens `.rv` files and writes them with `--save-session`.
- Integration for other skills: versioned review manifest (`--manifest`, `schema_version` 1) with free-form `meta` carried through; JSON results with each item's frame range; `--notes` / `--export-annotated` read the reviewer's annotations back per item; RV's ERROR / WARNING log lines are reported and fail the load.
- References: compare-dirs, ui-app-web, sessions, integration. `--compare difference-inverted`.
- Contributing guide, security policy, issue and pull-request templates, `scripts/check_repo.py` (frontmatter, manifest paths and privacy checks), an animated demo, a README Support section and a GitHub Sponsor button.

### Changed

- Every `rv_review.py` output, errors included, is one JSON line with `schema`, `schema_version`, `ok` and `exit_code`; `--state` now prints that envelope with the state under `state`.

## [0.1.0] - 2026-09-26

### Added

- `rv-review` skill: loads stills, stacked comparison sheets, movies, image sequences,
  multi-view and stereo EXRs and 360 lat-long images into one RV / OpenRV review window and
  reads the state back to verify the load.
- `rv-review/scripts/sheet_panels.py`: splits stacked sheets into labelled, equal-size frames
  and writes `frames.json`; labels unstacked renders.
- `rv-review/scripts/rv_review.py`: cross-platform launcher (Windows, macOS, Linux, standard
  library only). Finds RV through `--rv-bin`, `RV_BIN`, RV's own environment variables, `PATH`,
  the Windows registry and the usual install folders; reuses the tagged window through rvpush;
  layouts (sequence, wipe, difference, over, replace, tile), multi-view selection and
  expansion, stereo modes, the lat-long viewer, automatic marks from the sequence EDL, and a
  verified JSON result.
- References: media types, rv / rvpush by hand, the rv command line and bundled tools, stacked sheet layout.
- `rvio` skill: converting and transcoding with rvio (image sequences, stills and movies;
  EXR, DPX, TIFF, PNG, JPEG; MOV / MP4 / MXF; resize, crop, ranges, fps, audio; sRGB / log /
  Rec.709 / ACES, LUTs and baked OCIO; slates, frame burn-ins, watermarks, mattes, logos;
  EXR headers and stereo / multi-view). `rvio_cmd.py` builds and checks commands before they
  run (missing inputs, wildcards, frame notation, codecs the build cannot write, output
  folder) and counts the written files; `rvio_codecs.py` probes the movie codecs a build can
  write. References include commands and codec results verified on OpenRV 3.1.
- `rvls` skill: listing and inspecting sequences and movies with rvls; `rvls_check.py` reports
  frames, missing frames, size, pixel type and audio as JSON and checks renders and
  conversions against an expected range, count, size and type.
- `rvpkg` skill: listing, adding, installing, uninstalling, removing and opting in RV
  packages; `rvpkg_list.py` reports packages and support areas as JSON; references on
  support areas, `RV_SUPPORT_PATH` and the `.rvpkg` format.
- `rv_find.py` and `rv_tool.py` in each command-line skill: find RV's tools on Windows, macOS
  and Linux, and run them with argument lists, closed stdin and timeouts, failing when a tool
  prints errors but exits 0.
- Tests and trigger evals for the three command-line skills.
- Claude Code plugin marketplace (`.claude-plugin/marketplace.json`), trigger evals, tests and
  CI on Windows, macOS and Linux.

[0.2.2]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.2.2
[0.2.1]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.2.1
[0.2.0]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.2.0
[0.1.0]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.1.0
