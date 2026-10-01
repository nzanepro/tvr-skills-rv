# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). A release is numbered by the plugin version,
`version` in `.claude-plugin/plugin.json` (the top-level `version` in
`.claude-plugin/marketplace.json` matches it), and tagged `vX.Y.Z`. Each skill also has its
own version, `metadata.version` in its `SKILL.md`, which changes only when that skill changes,
so a skill's version can be lower than the plugin's; each release's "Versions" line lists the
skill versions it changed.

## [0.3.1] - 2026-10-01

Wording only, for Anthropic's plugin directory scan; no behaviour changed.

### Changed

- The skills, references, script messages and tests say "give" or "use" where they used a verb that the directory's scanner reads as a credential name when an option or placeholder follows it. `tests/test_scanner_words.py` keeps it out of every repository file.
- Tests name result objects `res`, and `rv_tool.py`'s `run()` lost an argument for child-process variables that no caller used.
- Versions: plugin 0.3.1; rv-review 0.3.1; rvio, rvls and rvpkg 0.2.1 (wording in each `SKILL.md` and in script messages).

## [0.3.0] - 2026-09-30

For Anthropic's plugin directory: no file in the repository reads shell or system variables any more, and the plugin has a new name. Upgrading from 0.2.x needs a reinstall (below).

### Added

- The README (a "Communicate visually with Claude" section, a feature bullet and the Skills table) and the rv-review skill (its description and step 5) now describe drawing in RV for Claude: the reviewer draws or types on frames with RV's annotation tool (F10), and `rv_review.py --notes --export-annotated DIR` brings the annotated frames back as PNGs that Claude opens and looks at. The feature itself is unchanged; only the documentation and the skill's description changed.

### Changed

- **The plugin is renamed from `rv` to `rv-tools`**, because the directory held the two-letter name as too close to another listing's (`NAME_CONFUSABLE`). Install it as `rv-tools@tvr-skills-rv`; the skills are now `/rv-tools:rv-review`, `/rv-tools:rvio`, `/rv-tools:rvls` and `/rv-tools:rvpkg`. The marketplace (`tvr-skills-rv`), the repository and the skill folders keep their names. `plugin.json` also has a `displayName`, "RV and OpenRV Media Review".
- **Moving from `rv@tvr-skills-rv`:** in your shell run `claude plugin uninstall rv@tvr-skills-rv`, `claude plugin marketplace update tvr-skills-rv` and `claude plugin install rv-tools@tvr-skills-rv` (or, inside Claude Code, `/plugin uninstall rv@tvr-skills-rv`, `/plugin marketplace update tvr-skills-rv` and `/plugin install rv-tools@tvr-skills-rv`), then start a new session. If you relied on `RV_BIN`, give that folder as the new RV bin folder setting or as `"rv_bin"` in `~/.config/tvr-skills-rv/config.json`.
- `plugin.json` declares one optional, non-sensitive user option, `rv_bin` ("RV bin folder"). Claude Code writes its value into each `SKILL.md`, and the skills give it as `--rv-bin` only when it reads as a path (an unset option stays a placeholder, which the skills ignore, and which the scripts also ignore if it is passed as `--rv-bin`).
- RV and its tools are found from `--rv-bin`, then `"rv_bin"` in `~/.config/tvr-skills-rv/config.json` (a JSON object of paths; a leading `~` is the home folder), `PATH`, the Windows registry, the usual install folders, and last an OpenRV built from source with the openrv-build plugin: a checkout holding `rvcmds.sh` in the current folder or one above it, `~/OpenRV` or `C:\OpenRV`, with the build in `_build/stage/app`. A wrong `--rv-bin` or config `rv_bin`, or a config file that is not a JSON object of strings, is an error that names it.
- rv-review runs rvpush only while an RV with the review tag is alive (its port file in the system temp folder names a running process whose executable is RV, so a leftover file from a crash whose process id was handed to another program does not count), so a closed window is reported instead of rvpush starting a stray RV. On macOS and Linux rvpush also runs under `/usr/bin/env RVPUSH_RV_EXECUTABLE_PATH=none`, which sets that one variable for rvpush alone; Windows has no such launcher and relies on the check, so an RV that quits between the check and the push can still let rvpush start one. On Windows the check reads the port folder the way RV does (GetTempPath), which can differ from Python's temp folder when TMP, TEMP and TMPDIR disagree. `rv_review.py --push COMMAND ARG ...` sends one guarded rvpush command and prints its output, and the rv-review references use it instead of rvpush with a shell variable in front.
- `web_capture.py` and `rasterize.py` take `--chrome PATH` and `--playwright-browsers DIR` (config `"chrome"` and `"playwright_browsers"`); a `--chrome` that is not a file is an error. `app_capture.py android` takes `--adb PATH` (config `"adb"`), then `PATH`, then the Android SDK's default folder.
- The Node runs of Playwright (web capture and Electron) load the project's own Playwright with `createRequire` from the project folder instead of an extended module search path.
- `rv-review/references/rv-command-line.md` keeps rvlinks, rvpush's `url` command and the manual links apart from the eval and interpreter commands, for the directory's download-and-execute warning (`RUNTIME_FETCH_EXEC`); no reference text was removed.
- The docs describe variables in words: no shell-variable syntax, PowerShell variable drive or percent-sign folder names anywhere (the Windows folders are written as `~\AppData\...`, and rvpkg's package placeholder as "a dollar sign, then PACKAGE").
- `scripts/check_repo.py` has a `no-env-reads` check, and `tests/test_no_env_reads.py` the same guard: no tracked file outside `.github/workflows`, tests and dev scripts included, reads shell or system variables or holds a shell-variable token (Claude Code's `user_config` substitution of `rv_bin` is the one allowed). The tests that use a real RV take `pytest --rv-bin DIR`.
- Versions: plugin 0.3.0; rv-review 0.3.0, rvio 0.2.0, rvls 0.2.0 and rvpkg 0.2.0.

### Removed

Every lookup through a shell or system variable. What replaces each:

- `RV_BIN`, `RVPUSH_RV_EXECUTABLE_PATH`, `RV_PATH`, `RV_APP_RV` and `RV_HOME` (as ways to find RV): `--rv-bin`, the plugin's RV bin folder option, or `"rv_bin"` in the config file; an RV on `PATH`, in its usual install folder or in an OpenRV build folder needs none of them.
- `ProgramFiles`, `ProgramW6432`, `ProgramFiles(x86)`: the Windows Program Files known folders, else `C:\Program Files` and `C:\Program Files (x86)` (RV, Chrome / Edge, Inkscape).
- `CHROME_PATH`: `--chrome` or `"chrome"` in the config file.
- `PLAYWRIGHT_BROWSERS_PATH` (for the command-line browser lookup): `--playwright-browsers` or `"playwright_browsers"`; Playwright itself still reads its own setting when it runs.
- `LOCALAPPDATA`: the Local AppData known folder, else `~\AppData\Local`; `XDG_CACHE_HOME`: `~/.cache`.
- `ANDROID_HOME`, `ANDROID_SDK_ROOT`: `--adb` or `"adb"` in the config file, `PATH`, then `~/Library/Android/sdk` (macOS), `~/Android/Sdk` (Linux) or `~\AppData\Local\Android\Sdk` (Windows).
- `APPDATA` and `XDG_DATA_HOME` (OpenRV's log): the Roaming AppData known folder (else `~\AppData\Roaming`) and `~/.local/share`.
- The `NODE_PATH` override for the Node children: `createRequire` from the project folder.
- `REPO_CHECK_WORDS` for `check_repo.py`: the git-ignored `.private-words` file only.
- `RV_BIN` for the test suite: `pytest --rv-bin DIR`.

## [0.2.4] - 2026-09-29

Fixes for two findings of Anthropic's plugin directory, and documentation; no skill script changed.

### Added

- A plugin icon, icon.svg in `.claude-plugin/`, which `icon` in `plugin.json` also names (the directory reported `ICON_MISSING`): a hand-written 256 x 256 SVG in the README demo's colours, showing stacked review frames, the front one split by a wipe between a grey and a copper sphere, over a green timeline bar. `tests/test_plugin_icon.py` checks that it is a square SVG of at least 128 px with no text, scripts, raster images or external references.
- A "What it runs and what it sends" section in the README: what the scripts start, which environment variables they read (only to find programs), where they write, and that `web_capture.py` loads the pages you give it in a local headless browser. `SECURITY.md` now names that exception to "no network calls".
- CI runs `claude plugin validate --strict` on the repository and on `plugin.json` with a pinned Claude Code (2.1.284).
- `scripts/check_repo.py` has a `readme-listing` check: no shell variable or command substitution in the README, and no repository image path in backticks or a code block in any Markdown file.

### Fixed

- The manual install in the README used shell variables for the current folder and the Windows user profile next to the `git clone` URL, which the directory's scanner holds as a local value, possibly a credential, sent off the machine (`MCP_FORWARDS_CREDENTIAL_ENV`). The clone now goes to `~/tvr-skills-rv`, the copy and link use `~` paths, and PowerShell works from the home folder with relative paths. The bash steps also create `~/.claude/skills` first, so `cp -r` no longer turns a missing skills folder into a copy of rv-review.
- The same kind of wording in the references: `rv-review`'s PowerShell examples start RV from a literal install path (the old one used an undefined variable) and set `RVPUSH_RV_EXECUTABLE_PATH` with `Set-Item Env:`; `RV_INIT` and `RV_HOME` are named instead of written as shell variables; the Playwright baseline update runs the project's own `playwright test` instead of `npx`; and `rvpkg`'s `RV_SUPPORT_PATH` example uses `~/.rv` instead of the home-folder variable.
- The README's worked example shows commands and their output in separate blocks instead of after shell prompts, and the notes on the README images link to them instead of writing their paths in backticks, which the directory holds for a reviewer.

### Changed

- Versions: plugin 0.2.4; rv-review 0.2.4 and rvpkg 0.1.3 (reference wording only); rvio and rvls stay 0.1.2.

## [0.2.3] - 2026-09-29

Packaging and documentation; no script or skill behaviour changed.

### Added

- `.claude-plugin/plugin.json`, the plugin's own manifest (name, version, description, author, homepage, repository, license, keywords and the four skill folders), which Anthropic's plugin directory requires. The marketplace entry now defers to it and keeps only its name, source, description, category and tags. Install and skill names are unchanged: `rv@tvr-skills-rv`, `/rv:rv-review`, `/rv:rvio`, `/rv:rvls`, `/rv:rvpkg`.
- Releases are published by `.github/workflows/release.yml` when a `v*` tag is pushed: `scripts/build_release.py` builds the per-skill zips from the tagged commit (LF line endings) and the release notes from this changelog.
- More checks in `scripts/check_repo.py`: every `SKILL.md` frontmatter must be valid YAML (for example, no unquoted value containing `: `) and fit the Agent Skills length limits for `name`, `description` and `compatibility`; `plugin.json`, the marketplace file and this changelog must agree on the version; and the marketplace entry and `plugin.json` must not conflict. The tests also parse each frontmatter with PyYAML and strictyaml.
- A code of conduct (Contributor Covenant 3.0), with reports through GitHub's reporting tools.
- Where the plugin works, stated up front: Claude Code on a Windows, macOS or Linux computer (CLI, desktop app or IDE extension) with RV or OpenRV installed, not claude.ai in a web browser or the iOS and Android apps, which cannot start RV. It is in a "Where it works" note at the top of the README (with a pointer to openrv-build-plugin for building OpenRV), in the plugin description and in each skill's `compatibility` field.

### Fixed

- The issue chooser linked to GitHub Discussions, which is not enabled. The link is gone, and `SECURITY.md` links straight to GitHub's private "Report a vulnerability" form.
- The README said to update the marketplace and then reinstall. An installed plugin updates with `claude plugin update rv@tvr-skills-rv` in a shell or **Update now** in `/plugin`; the README also gives the one-command `/plugin install rv --marketplace nzanepro/tvr-skills-rv` (Claude Code v2.1.275 or later).
- This changelog's header said every version matched each skill's `metadata.version`. It now describes the scheme: the plugin version numbers releases, and a skill's version changes only when that skill does.

### Changed

- Versions: plugin 0.2.3; rv-review 0.2.3; rvio, rvls and rvpkg 0.1.2 (only their `compatibility` text changed).

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

[0.3.1]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.3.1
[0.3.0]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.3.0
[0.2.4]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.2.4
[0.2.3]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.2.3
[0.2.2]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.2.2
[0.2.1]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.2.1
[0.2.0]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.2.0
[0.1.0]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.1.0
