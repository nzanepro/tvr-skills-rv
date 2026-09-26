# Baseline vs candidate: compare_dirs.py and review_set.py

Read this when comparing two folders of images (renders, screenshots, test baselines), a
test tool's failure output, a design export against a build, or one screen across many
variants. Paths are relative to the skill folder.

Contents: [Two folders](#two-folders) · [What is measured](#what-is-measured) ·
[The frames](#the-frames) · [Test tool adapters](#test-tool-adapters) ·
[Design vs build](#design-vs-build) · [Many variants](#many-variants-review_setpy) ·
[Outputs](#outputs) · [Exit codes and CI](#exit-codes-and-ci) · [Limits](#limits)

## Two folders

```bash
python scripts/compare_dirs.py shots/baseline shots/candidate --out review/compare
python scripts/rv_review.py --frames-json review/compare/frames.json
```

- Files pair by relative path (`screens/home.png` with `screens/home.png`). `--match stem`
  ignores the extension (`design/home.svg` with `build/home.png`).
- `--ext png,jpg` limits the file types (default: png, jpg, jpeg, webp, tif, tiff, bmp, gif,
  svg). `--include GLOB` / `--exclude GLOB` filter relative paths (repeatable).
- Two files instead of two folders compare as one pair.
- Status per pair: `changed`, `added` (candidate only), `removed` (baseline only),
  `within_tolerance`, `identical`. Only changed, added and removed pairs get frames unless
  `--all`; `--skip-added-removed` drops those two.
- Order: changed pairs first, largest mean difference first (a moved button ranks above a
  one-level colour shift across the page), then added, removed. `--order name` keeps names.
  `--max-pairs N` writes frames for the first N only; the report still lists every pair.

## What is measured

On 8-bit RGBA with colour premultiplied by alpha (colour under fully transparent pixels does
not count). 16-bit PNG / TIFF is scaled to 8 bits, not clipped. EXR and DPX are not read here:
convert them with rvio first, or review them with RV's own wipe and difference.

| Metric | Meaning |
|---|---|
| `mean_abs` | mean absolute difference over all pixels and channels, 0-1 |
| `max_abs` | largest single-channel difference, 0-1 |
| `changed_fraction`, `changed_pixels` | pixels whose largest channel difference is above `--threshold` |
| `bbox` | `[x0, y0, x1, y1]` around the changed pixels |
| `size_mismatch` | the two images differ in size (padding counts as changed) |

`--threshold LEVELS` (default 0) is the per-pixel tolerance in 8-bit levels: 2-4 absorbs
dithering and GPU rounding, 8-16 JPEG noise. `--min-changed FRACTION` calls pairs with at most
that share of changed pixels "within tolerance" (for example 0.0005 for anti-aliasing noise).

## The frames

For each pair in the review, in this order, all the same size and with the `sheet_panels.py`
title band (the pair's relative path) and label box:

1. **baseline** (label from `--labels`, default `baseline,candidate`)
2. **candidate**
3. **diff**: the absolute difference, so changes in both directions show (RV's own
   `--compare difference` is A minus B clamped at zero and hides where B is brighter). The
   label carries the numbers (`diff x8  0.59% px, max 0.84`). Unchanged pixels show the
   candidate dimmed to grey (`--context`, default 0.3; 0 for black); changed pixels are
   false-coloured from purple (tiny) through red and orange to pale yellow (large), and every
   changed pixel starts a quarter up that ramp, so a one-level change is still visible.
   `--gain` (default 8) sets how fast the ramp saturates. `--diff-mode signed` colours cyan
   where the candidate is brighter and magenta where it is darker. `--no-diff` skips it.
4. `--overlay`: a 50 % blend of both (ghosting shows offsets).
5. `--tool-diff`: the test tool's own diff image, when the adapter found one.

Mismatched sizes are padded with a dark grey checker (`--anchor top-left`, right for UI where
content grows downwards, or `center` for renders) and the pair is flagged. A missing side is a
checker placeholder saying "no baseline" / "no candidate". Transparent pixels show over a light
checker.

In RV: Left / Right flips baseline / candidate / diff in place, Alt+Left / Alt+Right jumps to
the next pair (one mark per pair). To wipe one pair, load its two frames with
`--compare wipe` (paths are in `compare_report.json` under `pairs[].frames`).

## Test tool adapters

`--adapter NAME ROOT` reads what a test tool wrote instead of two folders. Keys are relative
to ROOT; `--include` / `--exclude` filter them.

| Adapter | Reads | Pairs |
|---|---|---|
| `playwright` | `test-results/**/` after a failing `toHaveScreenshot` / `toMatchSnapshot` | `<name>-expected.png` vs `<name>-actual.png`; `<name>-diff.png` as tool diff. An actual without an expected (new snapshot) is `added`. Baselines live in `<spec>-snapshots/<name>-<project>-<platform>.png`; compare those folders directly with two paths if needed |
| `jest` | jest-image-snapshot output | `__image_snapshots__/<id>.png` vs `__received_output__/<id>-received.png` (written with `storeReceivedOnFailure: true`). Without it, the third panel of the composite `__diff_output__/<id>-diff.png` (baseline, diff, received; side by side, or stacked with `diffDirection: 'vertical'`) is cut out when it is exactly three baseline-sized panels. Passing snapshots are skipped |
| `unity` | Unity Graphics Test Framework (ROOT = project folder) | `Assets/ActualImages/<colour space>/<platform>/<graphics API>/.../<test>.png` vs the same path under `Assets/ReferenceImages/`, falling back to a reference with the same file name; `<test>.diff.png` as tool diff |
| `unreal` | the automation report (`-ReportExportPath=<dir>`, ROOT = that folder) | every object in `index.json` (or other JSON under ROOT) that names an approved / ground-truth image and an unapproved / incoming one, with difference / delta as tool diff; paths relative to the JSON's folder, then ROOT |
| `flutter` | golden test failures | `failures/<name>_masterImage.png` vs `<name>_testImage.png`; `_isolatedDiff.png` as tool diff. Flutter writes no failure images when the sizes differ |

Sources: Playwright `packages/playwright/src/matchers/toMatchSnapshot.ts` and
<https://playwright.dev/docs/test-snapshots>; jest-image-snapshot `src/diff-snapshot.js`
(<https://github.com/americanexpress/jest-image-snapshot>); Unity Graphics Test Framework
`Runtime/ImageAssert.cs` and
<https://docs.unity3d.com/Packages/com.unity.testframework.graphics@8.9/manual/index.html>;
Flutter `packages/flutter_test/lib/src/_goldens_io.dart`; Unreal
<https://dev.epicgames.com/documentation/en-us/unreal-engine/screenshot-comparison-tool-in-unreal-engine>.

**Unreal caveat:** the report JSON format is not publicly documented. The adapter searches for
the key names used by the report viewer (`approved`, `unapproved`, `difference`, also
`groundtruth`, `incoming`, `delta`) at any depth; it was checked on synthetic JSON only. If it
finds nothing, point two-folder mode at the approved and incoming screenshot folders under
`Saved/Automation/`.

Other tools (Roborazzi, Paparazzi, swift-snapshot-testing, react-native-owl, fastlane) have no
adapter because their failure layouts could not be confirmed from their documentation; when a
tool keeps baselines and new captures in two parallel folders, two-folder mode works as is.

## Design vs build

A design export (PNG from the design tool, or its SVG) against a screenshot of the build:

```bash
python scripts/compare_dirs.py design/checkout.png build/checkout.png \
  --labels design,build --overlay --resize candidate --out review/checkout
```

- Export the design at the same pixel size as the screenshot, or use `--resize candidate`
  (or `baseline`) to scale one to the other; say which in the report, scaling softens edges.
- SVG designs are rasterised at the other side's size (see `rasterize.py`).
- Use `--threshold 8` or more: fonts and anti-aliasing never match a design tool exactly.
- For a live wipe in RV: `python scripts/rv_review.py review/checkout/<design frame> review/checkout/<build frame> --compare wipe`.

## Many variants: review_set.py

The same screen across devices, appearances, text sizes, locales or states, one mark per
screen and one frame per variant:

```bash
python scripts/review_set.py caps --out review/devices                 # sub-folders = variants
python scripts/review_set.py caps/light caps/dark caps/high-contrast --out review/appearance
python scripts/review_set.py screenshots --order en-US,de-DE,ar-SA --out review/locales
```

- Each variant is a folder; files pair by relative path. Missing screens get a "missing in
  <variant>" placeholder so frames never shift; `--skip-incomplete` drops such screens.
- Frames of one screen are padded to that screen's largest variant (`--anchor`).
- `--labels` renames variants in the label boxes; `--order` picks and orders them.
- No difference images: for two variants with diffs use `compare_dirs.py`.

## Outputs

In `--out`:

- the frames, `<pair>__<role>__<N>.png`, N running across the whole review (last number in
  the name, which RV reads as the frame number);
- `frames.json`: a review manifest (`schema_version` 1, one group per pair or screen, items
  with `meta.role`, `meta.source`) that also carries the legacy `frames` / `views` keys, so
  `rv_review.py --frames-json` or `--manifest` loads it with a mark at each group;
- `compare_report.json` (`schema` `rv-review.compare`): inputs, threshold, counts, and per
  pair the status, metrics, sizes, source paths, frame paths and first frame;
- `compare_report.md`: the same as a table, for a PR comment or a note.

Stdout is one JSON line: `ok`, `exit_code`, `counts`, `frames`, `frames_json`, `report`,
`most_changed` (first five keys).

## Exit codes and CI

| Code | Meaning |
|---|---|
| 0 | no changed, added or removed pairs |
| 1 | changes found (frames written) |
| 2 | error: missing folder, unreadable image, no SVG rasteriser, bad arguments |

In CI, run it after the tests and upload `--out` as an artifact; reviewers open
`frames.json` locally. Update baselines only when the user says the change is intended.

## Limits

- Pillow formats only (PNG, JPEG, WebP, TIFF, BMP, GIF, plus SVG through `rasterize.py`).
- Whole images are compared; masks for dynamic regions (clocks, ads, carets) belong in the
  capture (hide them) or in the test tool.
- Large full-page captures work, but keep the review to the most changed pairs with
  `--max-pairs` when there are hundreds.
