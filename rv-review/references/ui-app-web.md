# UI, app and web review in RV

Read this when the images are screenshots of an app or web page: visual regression failures,
responsive breakpoints, design vs build, SVG icons or illustrations, mobile simulator or device
captures, desktop app windows, or accessibility variants (dark mode, high contrast, text size,
RTL, locales, states). RV is the viewer here, not the test runner: capture with the tools
below, compare with `compare_dirs.py` or group with `review_set.py`, then load `frames.json`.

Contents: [Folder layout](#folder-layout) · [Visual regression triage](#visual-regression-triage) ·
[Web pages](#web-pages-web_capturepy) · [Responsive review](#responsive-review) ·
[Design vs build](#design-vs-build) · [SVG](#svg-icons-and-illustrations) ·
[Mobile](#mobile-apps) · [Desktop apps](#desktop-apps) ·
[Review sets and accessibility](#review-sets-and-accessibility) · [In RV](#in-rv)

## Folder layout

Everything below writes, and `compare_dirs.py` / `review_set.py` read, one layout:

```
caps/<variant>/<screen>.png            variant = version, device, appearance, locale, state...
caps/before/checkout__mobile.png       web: <page>__<breakpoint>.png
caps/after/checkout__mobile.png
```

Two variants with differences: `compare_dirs.py caps/before caps/after`. Any number of
variants back to back: `review_set.py caps`. Keep names stable across variants (the same
screen must have the same relative path) and keep variant names short: they become labels.

## Visual regression triage

1. Run the tests as usual; let them fail.
2. `python scripts/compare_dirs.py --adapter playwright . --out review/visual` (or `jest`,
   `unity`, `unreal`, `flutter`, or two folders). Adapters and their layouts:
   `compare-dirs.md`.
3. `python scripts/rv_review.py --frames-json review/visual/frames.json`.
4. The reviewer flips expected / actual / diff per failure (most changed first) and says which
   changes are intended.
5. Update baselines only for the ones the user approved (for Playwright the project's own
   `playwright test --update-snapshots`, limited to those tests; for the others their own
   update flag), never all at once on your own.

`compare_report.md` is ready to paste into a PR comment. RV does not replace hosted review
services (approval workflows, CI gating); it is for looking closely, locally.

## Web pages: web_capture.py

```bash
python scripts/web_capture.py https://staging.example.com/ https://staging.example.com/pricing --version before --out caps
python scripts/web_capture.py http://localhost:3000/ http://localhost:3000/pricing --version after --out caps
python scripts/web_capture.py site_v1/index.html --version v1 --out caps --full-page --tile-height 3000
```

- Breakpoints default to `mobile=375,tablet=768,desktop=1440` (viewport height 900);
  `--breakpoints phone=360x740,wide=1920x1080` sets names and sizes. `--scale 2` for
  high-density captures.
- Page names come from the URL path (`/` is `index`) or the file name, so the same page gets
  the same name on another host; `--names a,b` overrides.
- `--browsers chromium,firefox,webkit` writes one folder per browser
  (`caps/<version>-<browser>/`), for `review_set.py caps` across browsers.
- Wait for the page: `--wait-until networkidle` (default), `--wait-for "main .loaded"`,
  `--wait-ms 500`. Hide or freeze dynamic content (clocks, carousels, ads) in the page or a
  test build; `--reduced-motion` asks pages to stop animating.
- `--color-scheme dark`, `--locale ar-EG` emulate user preferences (Playwright).
- Backends: Playwright for Python, Playwright for Node (from the current project), else a
  headless browser from its command line (viewport only: no `--full-page`, no `--wait-for`,
  Chromium only). `--list-backends` shows what is present and which browser was picked;
  nothing is installed. `--dry-run` prints the plan.
- Command-line browser: `--chrome PATH` (an executable; default `"chrome"` in
  `~/.config/tvr-skills-rv/config.json`) always wins. Otherwise a `chrome-headless-shell` is
  preferred: on PATH, then Playwright's copy (newest first, in `--playwright-browsers DIR`
  or `"playwright_browsers"` in the config file, then Playwright's own cache folder; the
  user's own Playwright setup puts it there), then an installed Chrome, Chromium or Edge. Some Chrome builds write the screenshot
  and never exit, so the script stops the browser once the PNG is complete and reports it in
  the JSON `notes`; each capture uses a temporary profile that is removed afterwards.
- **Tall pages.** `--full-page` keeps one tall frame per page: in RV press F to fit it, 1 for
  1:1 pixels, and Alt+drag (or middle-drag) to pan. `--tile-height 3000` splits it into tiles
  (`__t01`, `__t02` ...) that flip like frames; two versions of different heights then show
  added / removed tiles at the end.

## Responsive review

To flip one page through its breakpoints, make the breakpoints the variants:

```bash
python scripts/web_capture.py http://localhost:3000/ http://localhost:3000/pricing   --version after --group-by breakpoint --out caps
python scripts/review_set.py caps/after --out review/responsive
```

Breakpoint folders sort by width (mobile, tablet, desktop; or `w375`, `w1440`); give
`--order` for any other order.

`--group-by breakpoint` writes `caps/<version>/<breakpoint>/<page>.png`; each page becomes a
screen with one frame per breakpoint, padded to the widest (top-left), so layouts line up at
the left edge. To compare versions at every breakpoint, keep the default layout and use
`compare_dirs.py caps/before caps/after`.

## Design vs build

Export the design frame at the build's pixel size (or its SVG) and compare it with the
screenshot: `compare_dirs.py design.png build.png --labels design,build --overlay
--threshold 8 --out review/x` (details in `compare-dirs.md`). The overlay frame shows spacing
offsets as ghosting; the diff shows colour and size drift. For a wipe, load the two frames
with `rv_review.py A B --compare wipe`.

## SVG: icons and illustrations

RV does not read SVG. `rasterize.py` renders with the first backend found: resvg,
rsvg-convert, CairoSVG, Inkscape, Playwright's Chromium, then a headless browser picked as
for `web_capture.py` (`--chrome`, a `chrome-headless-shell`, then Chrome / Edge).
Renderers differ (fonts, filters, text), so render every version with one backend; the JSON
says which was used.

```bash
python scripts/rasterize.py icons/v1/*.svg --out review/icons_v1 --scale 4 --background checker
python scripts/compare_dirs.py icons/v1 icons/v2 --ext svg --svg-scale 4 --out review/icons
```

- Small icons: `--scale 4` or `--width 512` so single-pixel changes are visible; review at 1:1
  too (press 1 in RV), since the shipped size is what users see.
- `--background checker` shows transparency, `#ffffff` / `#000000` checks the icon on light
  and dark UI; `compare_dirs.py` always shows transparency over a checker.
- `--same-size` renders all files at one size so versions line up.

## Mobile apps

Navigate to the screen first (by hand, a UI test or a deep link), then capture every variant
of it with `app_capture.py`; repeat per screen, then `review_set.py`.

**iOS Simulator** (macOS with Xcode only):

```bash
python scripts/app_capture.py ios --screen checkout --out caps --appearance light,dark \
  --content-size large,accessibility-extra-large --clean-status-bar
```

It runs `xcrun simctl ui <device> appearance light|dark`, `xcrun simctl ui <device>
content_size <size>` (Dynamic Type: extra-small ... extra-extra-extra-large,
accessibility-medium ... accessibility-extra-extra-extra-large), optionally `xcrun simctl
status_bar <device> override` for a fixed 9:41 status bar, and `xcrun simctl io <device>
screenshot --type=png <file>`, then restores the appearance and text size (matched
case-insensitively against what the simulator reports back, since simctl has been seen
answering `content_size` with mixed case such as "extra-Small"). `--out` is resolved to an
absolute path before any of this runs, because simctl's screenshot needs an absolute path and
will not resolve a relative one against this script's working directory. If a setting cannot
be read or put back afterwards, that goes into a "warnings" list in the JSON output instead of
being skipped silently; `--dry-run` previews the restore commands in a "restore" list, with a
`<current>` placeholder for the value (nothing is queried from the simulator in a dry run).
`--locales en-US,ar-SA --bundle <id>` relaunches the app with the standard `-AppleLanguages
(ar) -AppleLocale ar_SA` launch arguments. Several device sizes: boot each simulator and give
`--device <UDID>` (`xcrun simctl list devices available`); name the variants by device with
separate `--out` folders or rename the variant folders.

**Android** (emulator or device; adb from `--adb PATH`, `"adb"` in the config file, PATH, or
the SDK's default folder: `~/Library/Android/sdk` on macOS, `~/Android/Sdk` on Linux,
`~\AppData\Local\Android\Sdk` on Windows, then `platform-tools`):

```bash
python scripts/app_capture.py android --screen checkout --out caps --night no,yes \
  --font-scale 1.0,1.3,2.0 --display 1080x2400@420,720x1600@320 --demo-mode
```

It runs `adb shell cmd uimode night yes|no`, `adb shell settings put system font_scale N`,
`adb shell wm size WxH` / `wm density D` (other device sizes on one emulator), SystemUI demo
mode for a fixed status bar (`sysui_demo_allowed` and `com.android.systemui.demo` broadcasts,
AOSP `packages/SystemUI/docs/demo_mode.md`), and `adb exec-out screencap -p`, then restores
night mode, font scale and display size. Per-app language on Android is set by the app or its
test harness (there is no documented adb command for it); capture each locale into its own
folder.

Both print the commands they run; `--dry-run` prints them without a device, on any OS.
Framework screenshot tests (Flutter goldens, and others that keep baseline and new images in
two folders) go through `compare_dirs.py` directly.

## Desktop apps

- **Electron:** `python scripts/app_capture.py electron --main . --screen home --out caps
  --size 1280x800,1920x1080 --color-scheme light,dark` launches the app through the project's
  own Playwright (Node; Playwright marks Electron support experimental) and captures the first
  window.
- **Tauri:** its docs recommend WebDriver through `tauri-driver` (Windows and Linux); take
  screenshots from those tests into the folder layout above. Playwright does not drive Tauri's
  webview.
- **Anything else:** the app's own screenshot tests, or the OS tool into the layout by hand:
  macOS `screencapture -l <windowid> file.png` or `-w` (interactive), Windows Snipping Tool
  (Win+Shift+S) or the test framework's capture, Linux `gnome-screenshot -w -f file.png` or
  `import -window <id> file.png` (ImageMagick). Keep window size and scale fixed between
  variants.

## Review sets and accessibility

Load back to back with a mark per screen (`review_set.py`), flip variants with Left / Right:

| Set | Variants | Look for |
|---|---|---|
| Devices | small phone, large phone, tablet, desktop widths | wrapping, clipped text, off-screen actions |
| Appearance | light, dark, high contrast / increased contrast | unreadable text, invisible borders or icons, hard-coded colours |
| Text size | 100 %, 130 %, 200 % (Dynamic Type accessibility sizes, Android font scale 2.0) | truncation, overlap, buttons that grow off screen |
| Locales | base, long (de-DE), RTL (ar-SA / he-IL), pseudo-locale | overflow, untranslated strings, mirrored layout and icons in RTL |
| States | empty, loading, error, long content, offline | missing states, layout jumps between states |
| Design | mock, build | spacing, colour, type size drift |

Screenshots cannot judge everything: colour contrast ratios, focus order, screen-reader labels
and hit-target sizes need the platform's accessibility inspector or an automated checker. Say
so in the report instead of approving accessibility from images alone.

## In RV

| Key | Action |
|---|---|
| Left / Right | previous / next variant of this screen or pair |
| Alt+Left / Alt+Right | previous / next screen or pair (marks) |
| F | fit the frame to the window (tall pages) |
| 1, 2 | 1:1 and 2:1 pixels |
| Alt+drag, middle-drag | pan |

Frames are PNG at the captured size; RV shows them in sRGB like a browser. Colour-managed
comparisons of design colours need the design tool's exported values, not screenshots.
