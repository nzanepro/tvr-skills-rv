# rv and rvpush by hand

Read this when driving RV without `scripts/rv_review.py` (no Python, debugging a load, or a
setup the script does not cover), when rvpush returns something unexpected, or when the user
asks for wipe / difference / tile directly. The script does all of this for you.

Contents: [Where rv lives](#where-rv-lives) · [Start a review window](#start-a-review-window) ·
[Replace or add media](#replace-or-add-media) · [After loading](#after-loading) ·
[Read the state back](#read-the-state-back) · [Layouts and flags](#layouts-and-flags) ·
[Wipe position](#wipe-position) · [Check the frame keys](#check-the-frame-keys) ·
[rvpush exit codes](#rvpush-exit-codes) · [Environment variables](#environment-variables)

## Where rv lives

| OS | rv | rvpush |
|---|---|---|
| Windows | `<install>\bin\rv.exe` (OpenRV often `Program Files\OpenRV\bin`) | `rvpush.exe` beside it |
| macOS | `/Applications/RV.app/Contents/MacOS/RV` (capital `RV`; older bundles `RV64.app/.../RV64`) | `rvpush` beside it |
| Linux | `<install>/bin/rv`, a wrapper script around `rv.bin` that sets `RV_HOME` | `<install>/bin/rvpush` |

Check an install with `rv -help` (or `rvls -help`, which starts faster).

## Start a review window

RV must listen on the network under a tag, and must not belong to the calling shell:

```bash
# macOS / Linux: detached, survives the shell
nohup rv -network -networkTag rv-review before.png after.png v2.png >/dev/null 2>&1 &
```

```powershell
# Windows PowerShell: wrap every path in literal double quotes (spaces, & and () are fine)
Start-Process -FilePath "$rvbin\rv.exe" -ArgumentList '-network','-networkTag','rv-review','"C:\review\before.png"','"C:\review\after.png"'
```

Every `rv` launch opens a new window; reuse one only through rvpush. Wait until RV answers
before pushing: `rvpush -tag rv-review py-eval-return "len(rv.commands.sources())"` prints a
number greater than 0 once the sources are in.

## Replace or add media

```bash
export RVPUSH_RV_EXECUTABLE_PATH=none      # never let rvpush start an RV of its own
rvpush -tag rv-review set   before.png after.png v2.png      # replace everything
rvpush -tag rv-review merge extra.png                        # append
rvpush -tag rv-review set [ left.exr right.exr ] [ shot.mov -in 101 -out 120 ]
```

- `-tag` must be the first argument.
- `set` replaces the session (frame 1, marks cleared; display settings such as the stereo mode are kept); `merge`
  appends.
- Brackets are separate arguments with spaces around them.
- On Windows `set` / `merge` paths may use backslashes; rvpush converts them.
- rvpush finds RV through port files RV writes in the temp folder (`tweak_rv_proc`), so run
  rvpush with the same TEMP / TMPDIR as RV.

## After loading

`py-exec` runs Python inside RV. It exits 0 even when that Python raises, so always read back.
Its globals and locals are separate: comprehensions cannot see local names or imports, so
spell out `rv.commands.` and inline the lists.

```bash
rvpush -tag rv-review py-exec "rv.commands.setViewNode('defaultSequence'); rv.commands.stop(); rv.commands.setFPS(1.0); rv.commands.setFrame(rv.commands.frameStart()); [rv.commands.markFrame(f, True) for f in [1,4]]"
```

In PowerShell 5.1 keep the Python in double quotes and use only single quotes inside it.

## Read the state back

```bash
rvpush -tag rv-review py-eval-return "(rv.commands.frame(), rv.commands.frameStart(), rv.commands.frameEnd(), rv.commands.markedFrames(), rv.commands.getIntProperty('defaultSequence_sequence.edl.frame'), len(rv.commands.nodesOfType('RVFileSource')), rv.commands.viewNode(), rv.commands.getStringProperty('@RVDisplayStereo.stereo.type'), rv.commands.fps())"
```

Compare with what was loaded: `frameEnd - frameStart + 1` frames, one `RVFileSource` per
source or bracket group, the expected marks, view node and stereo mode. `edl.frame` holds the
global start frame of each source plus an end + 1 terminator.

For a stack (wipe, difference, over, replace) also read its composite, whether the wipes mode
is on, and the visible box of the top source (next section):

```bash
rvpush -tag rv-review py-eval-return "(rv.commands.getStringProperty('defaultStack_stack.composite.type'), rv.runtime.eval('rvui.wipeShown()', ['rvui']), rv.commands.getFloatProperty('defaultStack_t_' + rv.commands.nodeConnections('defaultStack', False)[0][0] + '.stencil.visibleBox'))"
```

`(['over'], '2', [0.0, 0.5, 0.0, 1.0])` is a wipe split down the middle. `wipeShown()` returns
a menu state as text: `'2'` checked (wipes on), `'1'` unchecked, `'-1'` disabled (the view is
not a stack). `--state` and every load report these as `composite`, `wipe` and `wipeBox`.

Per-source detail: `rv.commands.sourceMediaInfo(src)` (keys `file`, `startFrame`, `endFrame`,
`fps`, `width`, `height`, `hasAudio`, `viewInfos`, `defaultView`).

## Layouts and flags

| rv flag | Python after loading | Effect |
|---|---|---|
| (default) | `setViewNode('defaultSequence')` | sources back to back |
| `-wipe` | stack + composite `over` + `rv.runtime.eval('rvui.toggleWipe();', ['rvui'])` + the top source's `stencil.visibleBox` (below) | wipe between the first two |
| `-diff` | `setStringProperty('defaultStack_stack.composite.type', ['difference'], True); setViewNode('defaultStack')` | difference |
| (menu: Difference (Inverted)) | composite `-difference` on defaultStack | B minus A: the other direction of the one-sided difference (`--compare difference-inverted`) |
| `-over`, `-replace`, `-topmost` | composite `over` / `replace` / `topmost` on defaultStack | stacked |
| `-tile` | `setViewNode('defaultLayout')` | side by side (layout `packed`) |
| `-stereo pair`, `-stereoSwap 1` | `setStringProperty('@RVDisplayStereo.stereo.type', ['pair'], True)` | stereo display |
| `-fps 24` | `setFPS(24.0)` | playback rate |
| `-noaudio` | `setIntProperty('#RVSoundTrack.audio.mute', [1], True)` | silence |
| `-flags ModeManagerPreload=lat_long_viewer` | (launch only) | loads the 360 viewer package; put it before `-network`, it takes the following arguments |

Per-source options inside brackets: `-in N`, `-out N`, `-fps N`, `-noMovieAudio`,
`-select view NAME`. Full lists: `rv -help` and the OpenRV manual below.

## Wipe position

Turning the wipes mode on (`-wipe`, F6, `rvui.toggleWipe()`) only adds the drag handle: the top
source still covers the whole frame until someone drags its edge in from the side. The edge
is the `stencil.visibleBox` property, `[x0, x1, y0, y1]` in 0-1 of the image, on the stack's
per-input transform `<stack>_t_<input>` (RVTransform2D); RV's wipes mode (`wipes.mu`) edits the
same property while dragging. Show the first source on the left half and the second on the
right:

```bash
rvpush -tag rv-review py-exec "rv.commands.setFloatProperty('defaultStack_t_' + rv.commands.nodeConnections('defaultStack', False)[0][0] + '.stencil.visibleBox', [0.0, 0.5, 0.0, 1.0], True)"
```

`[0.0, 1.0, 0.0, 1.0]` is the whole image again (also Wipes > Reset All Wipes). A saved `.rv`
keeps the box (`float visibleBox = [ 0 0.5 0 1 ]` in the `stencil` component), and rvio then
renders the split too. Checked with OpenRV 3.1.0 on Windows; the input's transform is
`defaultStack_t_sourceGroup000000`.

## Check the frame keys

`python scripts/rv_review.py --selftest` checks that the review window's key bindings move
the frame as documented, without touching the keyboard: it needs no window focus and, on
macOS, no Accessibility permission. It stops playback, goes to the first frame, sends Right,
Right, Left, Alt+Right, Alt+Right, Alt+Left, Alt+Left, reads the frame after each, and goes
back to the frame it started on. The window needs two or more frames (a sequence; a wipe of
two stills is one frame). By hand:

```bash
rvpush -tag rv-review py-eval-return "[b for b in rv.commands.bindings() if b[0] in ['key-down--right', 'key-down--left', 'key-down--alt--right', 'key-down--alt--left']]"
rvpush -tag rv-review py-exec "rv.commands.sendInternalEvent('key-down--right', '', '')"
rvpush -tag rv-review py-eval-return "rv.commands.frame()"
```

| Key | Event | RV's action (`rvui.mu`, `extra_commands.mu`) |
|---|---|---|
| Right | `key-down--right` | next frame; past the last frame it wraps to the first |
| Left | `key-down--left` | previous frame; before the first it wraps to the last |
| Alt+Right (Option on macOS) | `key-down--alt--right` | next mark; with no later mark, the last frame |
| Alt+Left | `key-down--alt--left` | previous mark; with none before, the first frame |

Without marks, Alt+Left / Alt+Right jump between source boundaries instead. Result (shortened):

```json
{"schema": "rv-review.result", "ok": true, "exit_code": 0, "action": "selftest",
 "tag": "rv-review", "frame": 3, "restored": true, "range": [1, 5], "marks": [1, 4],
 "bindings": {"Right": {"event": "key-down--right", "action": "Step Forward 1 Frame"}, "...": {}},
 "steps": [{"key": "Right", "event": "key-down--right", "from": 1, "to": 2, "expected": 2, "ok": true},
           {"key": "Alt+Right", "event": "key-down--alt--right", "from": 4, "to": 5, "expected": 5, "ok": true}],
 "problems": []}
```

Exit 0 when every step moved as expected and the starting frame was restored, 3 otherwise
(`problems` says which key), 1 when no RV answered or the window has a single frame. When
the self-test passes but the physical keys do nothing, the key presses are not reaching RV:
click into the RV window, and note that remote-desktop and screen-sharing clients can keep
Alt / Option for themselves.

## rvpush exit codes

| Code | Meaning |
|---|---|
| 0 | delivered to a running RV (not proof that the Python inside worked) |
| 1 | usage error or `-help` |
| 2 | missing tag |
| 3 | unknown command |
| 4 | connection to the running RV failed |
| 11 | no RV with this tag, and none started (`RVPUSH_RV_EXECUTABLE_PATH=none`) |
| 15 | no RV was running, so rvpush started one (avoid: it is tied to the calling shell) |

## Environment variables

| Variable | Set by | Meaning |
|---|---|---|
| `RV_BIN` | you | this skill's own: folder holding rv and rvpush |
| `RVPUSH_RV_EXECUTABLE_PATH` | you | rv executable rvpush starts when no RV answers; `none` = never start one |
| `RV_PATH` | you | rv executable; read by RV's Nuke integration |
| `RV_HOME` | Linux `rv` / `rvpush` wrapper scripts, or you | install root (`$RV_HOME/bin`); not set on Windows or macOS |
| `RV_APP_RV` | RV, for processes it starts | path of the running rv executable |
| `RV_SUPPORT_PATH` | you | support folders (packages, Mu, Python); not the executable |
| `RV_PREFS_OVERRIDE_PATH`, `RV_PREFS_CLOBBER_PATH` | you | default / forced preference files |

Sources: OpenRV `src/bin/apps/rvpush/RvPusher.cpp` and `main.cpp` (rvpush lookup and exit
codes), `src/bin/apps/rv/rv.wrapper` (`RV_HOME`), `src/bin/apps/rv/main.cpp` (`RV_APP_RV`),
`src/lib/app/QTBundle/QTBundle.cpp` (`RV_SUPPORT_PATH`), and the manuals:

- command line: https://aswf-openrv.readthedocs.io/en/latest/rv-manuals/rv-user-manual/rv-user-manual-chapter-three.html
- networking and rvpush: https://aswf-openrv.readthedocs.io/en/latest/rv-manuals/rv-user-manual/rv-user-manual-chapter-eighteen.html
- preferences: https://aswf-openrv.readthedocs.io/en/latest/rv-manuals/rv-reference-manual/rv-reference-manual-chapter-fifteen.html
- OpenRV: https://github.com/AcademySoftwareFoundation/OpenRV
