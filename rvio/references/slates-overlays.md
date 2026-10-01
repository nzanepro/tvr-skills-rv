# Slates, burn-ins, watermarks, mattes and logos

Read this when a render needs a slate (leader frame) or anything drawn on top of the frames:
frame numbers, a watermark, an aspect-ratio matte or a corner logo, or when you want to write
your own overlay script.

The scripts come from RV's `rvio_basic_scripts` package (installed and loaded by default):
`simpleslate`, `frameburn`, `watermark`, `matte` and `bug`. OpenRV ships no stereo tag script.

## Syntax

```text
-leader  SCRIPT ARG ARG ...     insert leader frames before the first source frame
-overlay SCRIPT ARG ARG ...     draw on every frame (repeat for several overlays)
-leaderframes N                 how long the leader is held (default 1)
```

Each list of arguments runs until the next word that starts with `-`, so no value may start
with `-`. Quote values with spaces as single arguments. Leaders and overlays are ignored when
the output is an `.rv` session file.

## simpleslate (leader)

```bash
rvio "shot.#.exr" -outsrgb -o review.mov \
  -leader simpleslate "Studio" "Show=Demo" "Shot=sh010" "Version=v001" "Artist=A. Artist" \
          "Comments=first take" -leaderframes 2
```

- First argument: text printed vertically down the left side.
- An optional background TIFF may follow the side text (it must exist); otherwise the slate
  shows colour bars and a grey ramp.
- Every `Name=Value` becomes one line; `"Artists=A=B"` gives one name with two values. The text
  is scaled to fit.
- The slate frames come before the first frame: a 1001-1024 sequence with one leader frame
  writes frames 1000-1024; with `-t 1005-1010 -leaderframes 2` the output held 1003-1010.

## frameburn (overlay)

```bash
-overlay frameburn OPACITY GREY POINT_SIZE        # e.g. .4 1.0 30.0
```

Draws the frame number (`%04d`) in the lower right corner at the given opacity; GREY is the
text brightness (1.0 = white). Give all three values: the script unpacks exactly three. It
marks missing frames differently. Changing the font means editing the script to call
`gltext.init("font.ttf")`.

## watermark (overlay)

```bash
-overlay watermark "Internal Review" .25          # text, opacity
```

Centred text scaled to the frame width.

## matte (overlay)

```bash
-overlay matte 2.39 0.8                           # aspect ratio, opacity
```

Darkens the frame outside the given aspect ratio and prints the ratio in the corner. Use
`[ -crop ... ]` or `-outres` instead when the matte must remove pixels.

## bug (overlay)

```bash
-overlay bug logo.tif 0.4 64 20 20                # file, opacity, height, x, y
```

- The logo must be a TIFF: a PNG was silently ignored. Convert it first:
  `rvio logo.png -o logo.tif`.
- Defaults: opacity 0.3, height = logo height, 10 pixels in from the corner. The logo is
  resized to a power-of-two texture.

## Verified combination (OpenRV 3.1)

```bash
rvio "src/shot_v001.#.png" -o all.mov \
  -leader simpleslate "Studio" "Shot=sh010" \
  -overlay frameburn .4 1.0 20.0 -overlay watermark "Internal" .2
```

24 source frames + 1 leader = 25 frames, 1.35 s. The slate, burn-in, watermark and matte were
checked by viewing extracted frames.

## Writing your own overlay or leader

A leader or overlay is a Mu module on the support path (`<area>/Mu/NAME.mu`, usually shipped in
a package; see the rvpkg skill). rvio calls its `main` for every frame, and an optional
`init` with the same arguments once:

```text
\: main (void; int w, int h, int tx, int ty, int tw, int th,
         bool stereo, bool rightEye, int frame, [string] argv)
```

`argv` starts with the script name, followed by the words given after it on the command line.
A variant with an extra `[(string,string)] keyvals` argument receives the source's attributes.
The drawing is done with Mu's OpenGL bindings; copy `frameburn.mu` from the package as a
starting point (the files are inside `rvio_basic_scripts-*.rvpkg`, a zip archive, in the
install's `Packages` folder).
