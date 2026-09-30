# RV command line, scripting and the other bin tools

Read this when RV has to be driven from a script rather than clicked: starting `rv` with
flags, sending commands to a running RV with `rvpush`, rvlink URLs, running Mu or Python
outside RV, the environment variables RV reads, or when you meet an unfamiliar executable in
the RV bin folder. Converting media is the `rvio` skill; listing sequences is `rvls`;
packages are `rvpkg`.

Sources: the OpenRV source (`src/lib/app/RvApp/RvApp/Options.h`, `src/bin/apps/rv/main.cpp`,
`src/bin/apps/rvpush/RvPusher.cpp`) and the OpenRV manuals at aswf-openrv.readthedocs.io
(RV User Manual chapters 3, 10, 13, 18, C and K; Reference Manual chapter 15). Items marked
*verified* were run against OpenRV 3.1; the rest come from the docs and source. `rv` opens a
window every time, so do not start it just to read its help.

## rv flags for scripted sessions

| Need | Flags |
|---|---|
| Clean start, no user prefs | `-noPrefs` (ignore prefs), `-resetPrefs`, `-prefsPath DIR`, `-nopackages` (skip all packages) |
| Run code at session start | `-eval 'MU'`, `-pyeval 'PYTHON'` (run in every new session), `-flags name=value ...` (read by scripts), `-init FILE.mu` |
| Caching | `-l` look-ahead cache, `-c` region cache, `-nc` no cache, `-lram GB`, `-cram GB`, `-vram MB` |
| Playback | `-play`, `-playMode 0/1`, `-loopMode 0/1/2`, `-fps F`, `-fullscreen`, `-present`, `-screen N`, `-geometry X Y W H` |
| Layout | `-over`, `-diff`, `-wipe`, `-tile`, `-replace`, `-layer`, `-topmost`, `-comp MODE`, `-layout MODE`, `-stereo MODE`, `-view NODE`, `-bg COLOUR` |
| Networking | `-network`, `-networkPort N` (default 45124), `-networkHost H`, `-networkTag T`, `-networkConnect HOST [PORT]`, `-networkUser NAME`, `-networkPerm 0/1/2` |
| Reuse a window | `-reuse 1` (default) sends the media to the running RV; `-reuse 0` always opens a new one |
| URLs | `-encodeURL ARGS` prints an `rvlink://` URL for the rest of the command line and exits; `-bakeURL` prints the hex ("baked") form |
| Events | `-sendEvent NAME CONTENT` sends a user event to the session after start |
| Sequences | `-ns` is accepted but ignored (Nuke-style `####` notation always works) |

Media arguments follow the same rules as rvio: sequences as `name.#.exr`, `name.@@@@.exr`,
`name.%04d.exr` or `name.1001-1100#.exr`, and per-source options inside `[ ... ]` with a
space on each side of each bracket, for example `[ left.#.exr right.#.exr ]` for a stereo
pair or `[ -in 1010 -out 1050 plate.mov ]`.

Init scripts are looked up in this order: `-init`, the `RV_INIT` environment variable,
`~/.rvrc.mu`, then `<install>/scripts/rv/rvrc.mu` (and the matching `rvrc.py`).

## rvpush: talk to a running RV

`rvpush` finds an RV that was started with networking on, by reading the port files RV writes
to `<system temp>/tweak_rv_proc/<pid>[_<tag>]`. If none answers, it starts `rv -network`
from its own bin folder (or `RVPUSH_RV_EXECUTABLE_PATH`; set that to `none` to never start
one). *verified: help text and exit codes below.*

Run every command below with `RVPUSH_RV_EXECUTABLE_PATH=none` set (bash:
`RVPUSH_RV_EXECUTABLE_PATH=none rvpush ...`; PowerShell:
`Set-Item Env:RVPUSH_RV_EXECUTABLE_PATH none` once), or a plain rvpush starts a new RV
whenever none answers the tag.

```text
rvpush [-tag T] set   <media args>        replace the session's media
rvpush [-tag T] merge <media args>        add media to the session
rvpush [-tag T] mu-eval 'play()'          run Mu
rvpush [-tag T] mu-eval-return 'frame()'  run Mu and print the result
rvpush [-tag T] py-eval 'rv.commands.play()'
rvpush [-tag T] py-eval-return 'rv.commands.frame()'
rvpush [-tag T] py-exec 'from rv import commands; commands.play()'
```

- `rvpush [-tag T] url LINK` hands an rvlink (see [rvlink URLs](#rvlink-urls)) to the
  running RV.
- `-tag T` addresses the RV started with `-networkTag T`, so a script can own one window and
  leave the user's other RV sessions alone.
- Exit status: 0 done; 4 connection to the running RV failed; 11 could not connect and could
  not start RV; 15 could not connect, so a new RV was started (the command was sent to it).
- `py-eval-return` / `mu-eval-return` print the value on stdout; use them to read state back
  (current frame, sources, marks) after a change.
- Networking has to be allowed: start RV with `-network` (optionally `-networkPort`), or turn
  networking on in RV's preferences.

## rvlink URLs

An rvlink is an rv command line written as a link, so a review can be shared in a message:
the `rvlink://` scheme, a space, then the flags and media paths, such as
`-l -play /path/shot.mov`. Opening the link gives those arguments to RV. Arguments with
spaces go in single quotes inside the link. `rv -encodeURL ...` builds one; the `baked/<hex>`
form after the scheme is the encoded one. The OS only passes rvlinks to RV after the
protocol handler is registered (RV's `.reg` / `.bat` files on Windows; the app bundle on
macOS; a desktop file on Linux).

## Running Mu and Python outside RV

- `mu-interp FILE.mu` runs a Mu file; `mu-interp` alone is a REPL. `-main` calls `main()`,
  `-stdin` takes the program on standard input without a prompt, `-compile` compiles `.muc` files on demand.
  *verified:* `mu-interp t.mu` with `print("mu %d\n" % (2+3));` printed `mu 5`.
- `py-interp` is RV's bundled Python (3.11 in OpenRV 3.1) as a standalone interpreter:
  `py-interp script.py`, `py-interp -c "..."`. The `rv` module is only usable inside a running
  RV; outside RV this is plain Python with RV's site-packages. On Windows, running it with no
  arguments starts an interactive prompt. *verified.*
- Inside RV, the same code runs through `-eval` / `-pyeval` at start-up or `rvpush` later.

## Other executables in the bin folder

| Tool | What it is | Use from a script? |
|---|---|---|
| `rvio` / `rvio_sw` | movie and image conversion (see the rvio skill); `rvio_sw` is a software-rendering build that only Linux installs have; Autodesk RV calls its GPU build `rvio_hw` | yes |
| `rvls` | list sequences and read headers (rvls skill) | yes |
| `rvpkg` | manage packages (rvpkg skill) | yes |
| `gtoinfo FILE` | print the structure of a GTO file, which is what `.rv` session files are: `-a` everything, `-d` data only, `-h` header (default), `-f EXPR` filter. *verified on a `.rv` written by `rvio ... -o session.rv`* | yes |
| `gtofilter -o OUT IN` | copy a GTO / `.rv` file keeping (`-ie REGEX`) or dropping (`-ee REGEX`) properties; `-t` writes text | yes |
| `gtomerge -o OUT IN1 IN2 ...` | merge GTO property data; `-sp PREFIX` strips a prefix | yes |
| `gtoimage IN.tif OUT.gto` | store a TIFF as a GTO image | rarely |
| `makeFBIOformats DIR`, `makeMovieIOformats DIR` | rebuild the image / movie plugin format caches (`formats.gto`, `movieformats.gto`) for a plugin folder; only needed after adding reader plugins | maintenance only |
| `rmsImageDiff [-f] [-m] [-cmp] [-dmax V] A B` | RMS difference per channel between two images of the same size and channel count; `-m` prints the pixel with the largest difference, `-cmp -dmax V` prints "Images are matched" or "Images are NOT matched". *verified: the exit code was 0 in both cases, so read the text; JPEG and PNG, or RGB and RGBA, count as incompatible* | yes, with care |
| `ojph_compress`, `ojph_expand` | OpenJPH command-line encoder / decoder for High-Throughput JPEG 2000 (HTJ2K), shipped next to RV's HTJ2K reader | yes |
| `rvprof FILE.rvprof` | GUI viewer for playback profiles written by `rv -debug profile` | no: opens a window |
| `rvshell NAME HOST [PORT]` | sample network client with a window, a demo of RV's remote-control protocol | no: opens a window |
| `python`, `pythonw` | the Python runtime RV embeds (on Windows); prefer `py-interp` | rarely |
| `QtWebEngineProcess` | helper process for RV's web views | never run directly |

## Environment variables

| Variable | Effect |
|---|---|
| `RV_SUPPORT_PATH` | support areas, `;`-separated on Windows and `:` elsewhere; replaces the default user area (`%APPDATA%\RV`, `~/Library/Application Support/RV`, `~/.rv`), while the install's own plugin area is still added. Include the user area yourself when you set it. |
| `RV_PREFS_OVERRIDE_PATH`, `RV_PREFS_CLOBBER_PATH` | folders with initial (override) or forced (clobber) preference files, for studio-wide defaults |
| `RV_INIT`, `RVIO_INIT` | init script for rv / rvio instead of `~/.rvrc.mu` / `~/.rviorc.mu` |
| `RVIO_OUTPARAMS` | default `-outparams` values for every rvio run (space-separated) |
| `RVPUSH_RV_EXECUTABLE_PATH` | the RV (or wrapper) rvpush starts when none is running; `none` never starts one |
| `RV_HOME` | install root; the Linux wrapper scripts set it, the binaries themselves do not read it; scripts and integrations use it to find RV |
| `OCIO` | OpenColorIO config used by the optional `ocio_source_setup` package inside RV; `RV_OCIO_3D_LUT_SIZE` sets its GPU LUT size |
| `RV_LUT_PATH` | extra folders searched for LUT files |
| `RV_OS_PATH_<OS>[_TAG]`, `RV_PATHSWAP_<NAME>` | path remapping between operating systems for sessions shared across machines (with the "OS Dependent Path Conversion" package) |
| `RV_IOEXR_ARGS`, `RV_IODPX_ARGS`, `RV_IOTIFF_ARGS`, `RV_IOJPEG_ARGS`, `RV_MOVIEFFMPEG_ARGS` ... | default reader options (for example `--codecThreads N` for FFmpeg) |

Log files: `%APPDATA%\ASWF\OpenRV\OpenRV.log` on Windows, `~/Library/Logs/ASWF/OpenRV.log`
on macOS, `~/.local/share/ASWF/OpenRV/OpenRV.log` on Linux (OpenRV 3.x; Autodesk RV uses its
own folders).

## OpenRV and Autodesk RV

- OpenRV needs no licence; `-lic` / `-strictlicense` are accepted and ignored.
- Autodesk RV adds H.264, AAC, DNxHD/DNxHR, Apple ProRes and ARRI / RED camera formats, SDI
  output, and Flow Production Tracking (ShotGrid) integration; OpenRV leaves these out unless
  a build turns individual FFmpeg encoders back on.
- Autodesk RV's GPU converter is `rvio_hw` and its `rvio` is software-only (Linux); in
  OpenRV `rvio` is the GPU build and `rvio_sw` the Linux software build.
