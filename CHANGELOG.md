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
- References: media types, rv / rvpush by hand, stacked sheet layout.
- Claude Code plugin marketplace (`.claude-plugin/marketplace.json`), trigger evals, tests and
  CI on Windows, macOS and Linux.

[0.1.0]: https://github.com/nzanepro/tvr-skills-rv/releases/tag/v0.1.0
