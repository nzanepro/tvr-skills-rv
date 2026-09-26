# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). The version matches `metadata.version` in each
skill's `SKILL.md` and the marketplace entry.

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

[0.1.0]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.1.0
