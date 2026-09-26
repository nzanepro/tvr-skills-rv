# RV session files (.rv): write, check, open, render

Read this when a review should be saved and reopened, rendered headless into a movie or
images with rvio, or built with a wipe / difference stack, per-source ranges, view selection
or text annotations. Paths are relative to the skill folder.

Contents: [Write](#write) · [Check](#check) · [Open](#open) · [Render](#render-with-rvio) ·
[What goes in the file](#what-goes-in-the-file) · [Annotations](#text-annotations) ·
[Gotchas](#gotchas) · [Sources](#sources)

## Write

```bash
python scripts/rv_session.py write review.json -o review.rv                 # from a manifest
python scripts/rv_session.py write rv_frames/frames.json -o review.rv       # from frames.json
python scripts/rv_session.py write review.json -o wipe.rv --layout wipe     # A/B wipe
python scripts/rv_review.py --manifest review.json --save-session review.rv # what was loaded
```

`rv_session.py` uses only the Python standard library (RV's own `rvSession.py` needs RV's
Python). `--save-session` writes the marks read back from RV, so movies without in / out get
correct marks too; offline, marks need every item's length (stills, sequences with a range,
or `in` / `out`), otherwise they are left out.

## Check

```bash
python scripts/rv_session.py check review.rv
```

Runs RV's `gtoinfo` (installed next to rv) when RV is found, else a structural check (header,
braces, node names, `type[]` syntax). **Check every hand-edited or generated session before
opening it**: a syntax error makes RV show an error console and load nothing useful, and
`gtoinfo` reports the same line and column without opening a window (it exits 0 even on
errors; the script reads its text). Example: `int[] marks = [ 1 3 ]` fails at the `[` of
`int[]`; GTO arrays are written `int marks = [ 1 3 ]`.

## Open

`python scripts/rv_review.py review.rv` opens the session in the review window (replacing its
contents), keeps the session's own view, layout and marks, and reads it back: source count and
marks are compared with the file. A `.rv` must be the only source. `rv review.rv` also works
but opens a new, untagged window.

## Render with rvio

```bash
python scripts/rv_session.py render review.rv -o review.mov
python scripts/rv_session.py render review.rv -o frames/review.#.png -- -t 1-10
python scripts/rv_session.py render review.rv -o notes/review.#.png -- -t 3,7,12
```

Everything after `--` goes to rvio unchanged (frame ranges and lists with `-t`, `-outres W H`,
`-leader simpleslate ...`, `-overlay frameburn ...`; see the rvio skill for codecs and
overlays). rvio renders the session's view node: the sequence, the wipe / difference stack or
the tile layout, with text annotations burnt in. A wipe is interactive: rvio renders a
wipe stack as its top source, so render `tile` for side by side or `difference` for a
difference movie. Output images are numbered by global frame
(`-t 3,7` writes `review.0003.png`, `review.0007.png`). A difference render has alpha
differenced too: write JPEG or drop alpha, or the result looks blank in viewers that honour
alpha. rvio cannot read OTIO; convert timelines to `.rv` first.

## What goes in the file

| In the manifest | In the .rv |
|---|---|
| items | `sourceGroupNNNNNN` (RVSourceGroup, `ui.name` = label) and `sourceGroupNNNNNN_source` (RVFileSource, `media.movie`) |
| `in` / `out` | `cut.in` / `cut.out` on the source |
| `fps` (item) | `group.fps` on the source (the media's rate, not playback) |
| `view` | `request.imageComponent = [ "view" "NAME" ]` |
| `stereo_views` | `request.stereoViews = [ "L" "R" ]` |
| order, marks, `fps`, `title` | `review_sequence` (RVSequenceGroup): inputs in order, `session.marks`, `session.fps`, `ui.name` |
| `layout` wipe / difference / difference-inverted / over / replace | `review_stack` (RVStackGroup) of the first two items, `ui.wipes`, `review_stack_stack.composite.type` (`over`, `difference`, `-difference`, `replace`) |
| `layout` tile | `review_layout` (RVLayoutGroup, `layout.mode = "packed"`) |
| `stereo` | `defaultOutputGroup_stereo.stereo.type` |
| labels, groups, `meta` | component `review` on each RVFileSource (`item`, `label`, `title`, `group`, `view`, `meta` as JSON) and on the `rv` RVSession object (`title`, `layout`, `meta`, `groups`, `schema_version`) |
| annotations | `sourceGroupNNNNNN_paint` (RVPaint): `text:ID:FRAME:review` components and `frame:FRAME.order` |

RV keeps unknown components such as `review` when it loads and when it saves a session
itself, so a review saved again from RV's File menu still maps back to its items.

## Text annotations

`items[].annotations: [{"frame": 1, "text": "check the edge"}]` (`frame` counts from the
item's first frame; `source_frame` gives the media frame directly). Optional:

- `position` `[x, y]`: RV paint coordinates, in image heights, origin at the image centre,
  y up (the top edge is 0.5, the left edge is -width / height / 2). Default: top-left corner.
- `size`: text size (default 0.003; glyphs are about ten times that in image heights, so
  0.05 is already huge).
- `color` `[r, g, b]` or `[r, g, b, a]`, 0-1 (default amber).

In a live window the launcher creates the same properties through rvpush, so a manifest's
annotations appear straight away; `--notes` reads them back with the reviewer's own.

## Gotchas

- **Node names:** unique, at least two characters, only `[A-Za-z0-9_]`, starting with a
  letter or `_`. RV cuts names at other characters and one-character names break property
  creation ("malformed property name"). Human text goes in `ui.name` and `review.label`.
- **Source groups** must be named `sourceGroup` plus six digits; RV relies on it.
- **Annotations need `frame:N.order`.** Text components without an order entry exist but are
  never drawn by RV or rvio. RV's own `rvSession.py` also fails if a text's position or size
  is set before the text itself; this writer always writes the text first.
- **Paint frame numbers are source frames** (a still named `x__before__3.png` is frame 3).
- **Strings** are double-quoted with `\\`, `\"` and `\n` escapes; UTF-8 is fine.
- **Marks** belong on the viewed group's `session` component; RV ignores `session.marks` on
  the RVSession object when the view node has its own.
- **Paths** are written with forward slashes on every OS.

## Sources

- OpenRV reference manual, the .rv file format (chapter 6):
  <https://aswf-openrv.readthedocs.io/en/latest/rv-manuals/rv-reference-manual/rv-reference-manual-chapter-six.html>
- rvio (user manual chapter 16):
  <https://aswf-openrv.readthedocs.io/en/latest/rv-manuals/rv-user-manual/rv-user-manual-chapter-sixteen.html>
- Behaviour checked live on OpenRV 3.1: gtoinfo messages, marks on the view group, `review`
  components surviving load and save, annotation rendering in rvio, `-t` frame lists, and the
  node-name rules above. RV's bundled `plugins/Python/rvSession.py` and `Mu/annotate_mode.mu`
  show the property layout; this writer is an independent implementation.
