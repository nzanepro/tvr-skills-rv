#!/usr/bin/env python3
"""Build (and optionally run) an rvio command for a common conversion, with checks.

    python rvio_cmd.py INPUT [INPUT ...] -o OUTPUT [options]            # print the command
    python rvio_cmd.py INPUT [INPUT ...] -o OUTPUT [options] --run      # run it and check

Builds the argument list in the order rvio expects (sources, per-source brackets, global
options, leaders and overlays, -o, then -outparams), so slate fields with spaces, brackets and
# sequence notation survive any shell. Before anything runs it catches the mistakes rvio
itself lets through with exit code 0:
  - an input that does not exist (rvio renders a placeholder movie instead of failing)
  - a "*" wildcard in an input (not expanded by rvio on Windows; use # or a folder)
  - an image output without # / @@@@ / %04d when more than one frame will be written
    (rvio then overwrites one file per frame)
  - a video codec OpenRV cannot write (H.264, HEVC, VP9, AV1, AAC audio), and rawvideo
    in .mov (unreadable); build-dependent codecs such as ProRes and DNxHD get a warning
  - an EXR compression name rvio does not know
  - a bug logo that is not a TIFF (rvio silently draws nothing)
  - a slate or overlay value that starts with "-" (rvio would read it as an option)
  - a missing output folder (rvio does not create it and writes nothing)

Sequence notation: name.#.exr (4-digit padding), name.####.exr, name.@@@.exr (one @ per digit),
name.%04d.exr, name.1001-1100#.exr (explicit range), name.1-5,8-10#.exr (list).

Printing: --print json (default: argv list plus ready-to-paste posix and powershell lines),
posix, powershell or cmd. With --run the command goes through rv_tool.py (argument list, no
shell, timeout, ERROR lines treated as failure) and the written files are counted.

Exit status: 0 OK; 2 a check failed (nothing was run); with --run, rv_tool.py's codes
(1 rvio failed, 3 rvio printed errors, 124 timeout, 127 rvio not found) and 4 when rvio
exited cleanly but the expected output files are missing.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rv_tool  # noqa: E402  (sibling script)

MOVIE_EXTS = {"mov", "mp4", "m4v", "avi", "mkv", "mxf", "mpg", "mpeg", "flv"}
AUDIO_EXTS = {"wav", "aif", "aiff", "aifc", "au", "snd", "mp3", "ogg"}
EXR_EXTS = {"exr", "sxr", "aces", "txr", "openexr"}
EXR_CODECS = ("PIZ", "ZIP", "ZIPS", "RLE", "PXR24", "B44", "B44A", "DWAA", "DWAB", "NONE")
# Video encoders: the first three work in every OpenRV build; the rest were accepted by one
# OpenRV 3.1 build but depend on how its FFmpeg was configured (probe with rvio_codecs.py)
SAFE_VIDEO_CODECS = ("mjpeg", "mpeg4", "png")
BUILD_DEPENDENT_CODECS = ("prores_ks", "prores_aw", "dnxhd", "mpeg2video", "mpeg1video",
                          "dvvideo", "svq1", "cfhd", "v210", "v410", "jpeg2000", "tiff", "gif",
                          "cinepak")
# Names rvio 3.1 rejected ("Invalid video codec") and what to use instead
UNAVAILABLE_CODECS = {
    "libx264": "mjpeg or mpeg4 (H.264 is not in the OpenRV FFmpeg build; transcode with ffmpeg)",
    "h264": "mjpeg or mpeg4 (H.264 is not in the OpenRV FFmpeg build; transcode with ffmpeg)",
    "libopenh264": "mjpeg or mpeg4", "libx265": "prores_ks or mjpeg", "hevc": "prores_ks",
    "libvpx": "mjpeg", "libvpx-vp9": "mjpeg", "libaom-av1": "mjpeg", "libsvtav1": "mjpeg",
    "prores": "prores_ks (profile via -outparams vc:profile=hq) where the build has it",
    "qtrle": "png (lossless, alpha) or rawvideo", "libopenjpeg": "jpeg2000",
    "flv1": "mjpeg", "ffv1": "png or rawvideo", "huffyuv": "png", "utvideo": "png",
}
IN_COLOUR = {"linear": [], "srgb": ["-insrgb"], "log": ["-inlog"], "cineon": ["-inlog"],
             "709": ["-in709"], "rec709": ["-in709"], "redlog": ["-inredlog"],
             "redlogfilm": ["-inredlogfilm"]}
OUT_COLOUR = {"linear": [], "srgb": ["-outsrgb"], "log": ["-outlog"], "cineon": ["-outlog"],
              "709": ["-out709"], "rec709": ["-out709"], "redlog": ["-outredlog"],
              "redlogfilm": ["-outredlogfilm"], "aces": ["-outaces"]}
STEREO_MODES = ("separate", "checker", "scanline", "anaglyph", "left", "right", "pair",
                "mirror", "hsqueezed", "vsqueezed")

# a frame token: optional range list followed by padding (#, ####, @@@, %04d, %d)
_RANGE_ITEM = r"-?\d+(?:--?\d+(?:x\d+)?)?"
SEQ_RE = re.compile(r"(?P<range>" + _RANGE_ITEM + r"(?:," + _RANGE_ITEM + r")*)?"
                    r"(?P<pad>#+|@+|%0?(?P<w>\d*)d)")


class CheckError(ValueError):
    """A problem found before running rvio; the message says how to fix it."""


# --- sequence notation ----------------------------------------------------------------

def split_sequence(spec):
    """(prefix, range_text or None, pad_token, suffix) for the last frame token, or None."""
    name = os.path.basename(spec)
    matches = list(SEQ_RE.finditer(name))
    if not matches:
        return None
    m = matches[-1]
    head = spec[: len(spec) - len(name)]
    return head + name[: m.start()], m.group("range"), m.group("pad"), name[m.end():]


def is_sequence(spec):
    """True when spec uses rvio frame notation."""
    return split_sequence(spec) is not None


def padding(pad_token):
    """Digits of zero padding for a pad token: # -> 4, #### -> 4, @@@ -> 3, %05d -> 5, %d -> 0."""
    if pad_token == "#":
        return 4
    if pad_token.startswith("#") or pad_token.startswith("@"):
        return len(pad_token) if pad_token != "@" else 1
    w = re.fullmatch(r"%0?(\d*)d", pad_token).group(1)
    return int(w) if w else 0


def parse_ranges(text):
    """Frames listed by an rvio / rvls range list such as '1-5,8-10', '1-9x2' or '-2-1'."""
    frames = []
    for item in text.split(","):
        m = re.fullmatch(r"(-?\d+)(?:-(-?\d+)(?:x(\d+))?)?", item.strip())
        if not m:
            raise CheckError(f"cannot read frame range '{item}' in '{text}'")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) is not None else a
        step = int(m.group(3)) if m.group(3) else 1
        stepdir = step if b >= a else -step
        frames.extend(range(a, b + (1 if b >= a else -1), stepdir))
    return frames


def frames_on_disk(spec):
    """Sorted frame numbers of existing files matching a sequence spec (range respected)."""
    parts = split_sequence(spec)
    if parts is None:
        return []
    prefix, rng, pad, suffix = parts
    folder = os.path.dirname(prefix) or "."
    base = os.path.basename(prefix)
    n = padding(pad)
    num = r"(-?\d{%d,})" % n if n > 1 else r"(-?\d+)"
    rx = re.compile(re.escape(base) + num + re.escape(suffix) + r"$")
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        m = rx.match(name)
        if m:
            found.append(int(m.group(1)))
    found = sorted(set(found))
    if rng:
        wanted = set(parse_ranges(rng))
        found = [f for f in found if f in wanted]
    return found


def ext_of(path):
    """Lower-case extension without the dot."""
    return Path(path).suffix.lower().lstrip(".")


# --- building --------------------------------------------------------------------------

def per_source_args(a):
    """Options rvio only accepts inside [ ] for one source."""
    out = []
    if a.crop:
        out += ["-crop"] + [str(v) for v in a.crop]
    if a.uncrop:
        out += ["-uncrop"] + [str(v) for v in a.uncrop]
    if a.pa is not None:
        out += ["-pa", str(a.pa)]
    if a.llut:
        out += ["-llut", a.llut]
    if a.range_start is not None:
        out += ["-rs", str(a.range_start)]
    if a.range_offset is not None:
        out += ["-ro", str(a.range_offset)]
    if a.cut_in is not None:
        out += ["-in", str(a.cut_in)]
    if a.cut_out is not None:
        out += ["-out", str(a.cut_out)]
    if a.audio_offset is not None:
        out += ["-ao", str(a.audio_offset)]
    if a.no_movie_audio:
        out += ["-noMovieAudio"]
    return out


def build(a):
    """(argv without the rvio path, warnings). Raises CheckError on a blocking problem."""
    warnings = []
    out_ext = ext_of(a.output)
    is_movie = out_ext in MOVIE_EXTS
    is_exr = out_ext in EXR_EXTS

    # sources
    src = []
    per = per_source_args(a)
    if a.audio and len(a.inputs) != 1:
        raise CheckError("--audio joins one picture source; with several inputs write the "
                         "brackets by hand: [ pics.#.exr sound.wav ] other.mov")
    for i, inp in enumerate(a.inputs):
        group = per + [inp] + ([a.audio] if a.audio and i == 0 else [])
        src += (["["] + group + ["]"]) if len(group) > 1 else group

    g = []
    if a.range:
        g += ["-t", a.range]
    if a.fps is not None:
        g += ["-fps", str(a.fps)]
    g += IN_COLOUR[a.in_colour] if a.in_colour else []
    if a.in_gamma is not None:
        g += ["-ingamma", str(a.in_gamma)]
    if a.flut:
        g += ["-flut", a.flut]
    if a.exposure is not None:
        g += ["-exposure", str(a.exposure)]
    if a.resize:
        w, h = (a.resize + [0])[:2]
        g += ["-resize", str(w), str(h)]
    if a.scale is not None:
        g += ["-scale", str(a.scale)]
    if a.outres:
        g += ["-outres", str(a.outres[0]), str(a.outres[1])]
    if a.dlut:
        g += ["-dlut", a.dlut]
    g += OUT_COLOUR[a.out_colour] if a.out_colour else []
    if a.out_gamma is not None:
        g += ["-outgamma", str(a.out_gamma)]
    if a.outformat:
        g += ["-outformat", a.outformat[0], a.outformat[1]]
    if a.outrgb:
        g += ["-outrgb"]
    if a.outchannelmap:
        g += ["-outchannelmap"] + a.outchannelmap
    if a.outstereo:
        g += ["-outstereo", a.outstereo]
    if a.outfps is not None:
        g += ["-outfps", str(a.outfps)]
    if a.outpa:
        g += ["-outpa", a.outpa]

    codec = a.codec
    if a.exr_compression:
        if codec:
            raise CheckError("use either --codec or --exr-compression, not both")
        codec = a.exr_compression
    if codec:
        if is_exr and codec.upper() not in EXR_CODECS:
            raise CheckError(f"EXR compression '{codec}' is unknown; use one of "
                             f"{', '.join(EXR_CODECS)}")
        if is_exr:
            codec = codec.upper()
        if is_movie and codec in UNAVAILABLE_CODECS and not a.allow_codec:
            raise CheckError(f"OpenRV's rvio cannot write '{codec}'. Use "
                             f"{UNAVAILABLE_CODECS[codec]}. Pass --allow-codec if this is an "
                             f"RV build that has it.")
        if is_movie and codec in BUILD_DEPENDENT_CODECS:
            warnings.append(f"'{codec}' is only in some OpenRV builds (stock builds leave out "
                            f"ProRes, DNxHD, MPEG-2 and others); run rvio_codecs.py to see "
                            f"what this one writes")
        elif (is_movie and codec not in SAFE_VIDEO_CODECS
              and codec not in UNAVAILABLE_CODECS and codec != "rawvideo"):
            warnings.append(f"codec '{codec}' was not tested with OpenRV; check rvio's error")
        if is_movie and codec == "rawvideo" and out_ext == "mov":
            raise CheckError("rawvideo in .mov writes an unreadable file; use png for "
                             "lossless or write an image sequence")
        if codec == "dnxhd":
            warnings.append("dnxhd only accepts its fixed profiles (for example 1920x1080 at "
                            "-outparams vcc:b=36000000); other sizes or rates fail")
        g += ["-codec", codec]
    if a.quality is not None:
        if is_movie and (codec or "mjpeg") != "mjpeg":
            warnings.append("-quality only changes mjpeg movies; for other codecs set the "
                            "rate with --outparam vcc:b=BITS or vcc:qscale / vc:profile")
        if is_exr and codec in ("DWAA", "DWAB") and a.quality <= 1.0:
            warnings.append("for DWAA/DWAB -quality is the DWA level (45 is the OpenEXR "
                            "default), not 0-1")
        g += ["-quality", str(a.quality)]
    if a.audiocodec:
        if a.audiocodec in ("aac", "libfdk_aac", "mp3", "libmp3lame") and not a.allow_codec:
            raise CheckError(f"rvio cannot mux '{a.audiocodec}' audio in OpenRV; use "
                             f"pcm_s16le / pcm_s24le (the default is pcm_s16be)")
        g += ["-audiocodec", a.audiocodec]
    if a.audiorate is not None:
        g += ["-audiorate", str(a.audiorate)]
    if a.audiochannels is not None:
        g += ["-audiochannels", str(a.audiochannels)]
    if a.comment is not None:
        g += ["-comment", a.comment]
    if a.copyright is not None:
        g += ["-copyright", a.copyright]
    if a.threads:
        g += ["-rthreads", str(a.threads)]
    if a.verbose:
        g += ["-v"]

    # leaders and overlays (their arguments run until the next option)
    lo = []
    if a.slate:
        for v in a.slate:
            if v.startswith("-"):
                raise CheckError(f"slate value '{v}' starts with '-'; rvio would read it as an "
                                 f"option. Reword it.")
        side, fields = a.slate[0], a.slate[1:]
        bad = [f for f in fields if "=" not in f]
        if bad:
            raise CheckError(f"slate fields must be Name=Value; got {bad}")
        lo += ["-leader", "simpleslate", side] + fields
        if a.leader_frames:
            lo += ["-leaderframes", str(a.leader_frames)]
    if a.frameburn is not None:
        vals = (a.frameburn + ["0.4", "1.0", "30"][len(a.frameburn):])[:3]
        lo += ["-overlay", "frameburn"] + vals
    if a.watermark:
        text = a.watermark[0]
        if text.startswith("-"):
            raise CheckError("watermark text must not start with '-'")
        lo += ["-overlay", "watermark", text, a.watermark[1] if len(a.watermark) > 1 else "0.25"]
    if a.matte:
        lo += ["-overlay", "matte", a.matte[0], a.matte[1] if len(a.matte) > 1 else "0.8"]
    if a.bug:
        if ext_of(a.bug[0]) not in ("tif", "tiff"):
            raise CheckError("the bug overlay only reads TIFF logos (a PNG is silently "
                             "ignored); convert it first: rvio logo.png -o logo.tif")
        lo += ["-overlay", "bug"] + a.bug

    tail = ["-o", a.output]
    params = list(a.outparam or [])
    for p in params:
        if p.startswith("-"):
            raise CheckError(f"-outparams value '{p}' starts with '-'")
    if params:
        tail += ["-outparams"] + params
    extra = list(a.extra or [])
    return src + g + extra + lo + tail, warnings


def check_inputs(a):
    """Raise CheckError for inputs rvio would silently replace with a placeholder."""
    for inp in a.inputs + ([a.audio] if a.audio else []):
        if inp.endswith(".movieproc"):
            continue                              # procedural source: bars, solid colour, ...
        if "*" in inp or "?" in inp:
            raise CheckError(f"'{inp}': rvio does not expand wildcards itself (and on Windows "
                             f"nothing does); use name.#.ext or pass the folder")
        if is_sequence(inp):
            if not frames_on_disk(inp):
                raise CheckError(f"no files match the sequence '{inp}'")
        elif not os.path.exists(inp):
            raise CheckError(f"input '{inp}' does not exist")


def expected_frames(a):
    """Frame count for image-sequence output when it can be known, else None."""
    slate = (a.leader_frames or 1) if a.slate else 0
    if a.range:
        r = a.range.strip()
        if re.fullmatch(r"-?\d+", r):
            return 1 + slate
        m = re.fullmatch(r"(-?\d+)-(-?\d+)", r)
        return abs(int(m.group(2)) - int(m.group(1))) + 1 + slate if m else None
    total = 0
    for inp in a.inputs:
        if is_sequence(inp):
            fr = frames_on_disk(inp)
            if not fr:
                return None
            total += fr[-1] - fr[0] + 1          # rvio fills gaps by holding frames
        elif ext_of(inp) in MOVIE_EXTS or os.path.isdir(inp):
            return None
        else:
            total += 1
    return total + slate


def check_output(a):
    """Raise CheckError for output names that lose frames or cannot be written."""
    out_ext = ext_of(a.output)
    if not out_ext:
        raise CheckError("the output needs an extension; rvio picks the format from it")
    parent = os.path.dirname(os.path.abspath(a.output))
    if not os.path.isdir(parent) and not a.mkdir:
        raise CheckError(f"output folder '{parent}' does not exist; rvio will not create it "
                         f"(pass --mkdir)")
    if out_ext in MOVIE_EXTS or out_ext in AUDIO_EXTS or out_ext == "null":
        return
    if not is_sequence(a.output):
        n = expected_frames(a)
        if n is None or n > 1:
            raise CheckError(f"'{a.output}' names one image but more than one frame will be "
                             f"written; add frame notation (name.#.{out_ext}) or pick one "
                             f"frame with --range N")


# --- printing --------------------------------------------------------------------------

_PS_SAFE = re.compile(r"^[A-Za-z0-9_\-./:\\=+]+$")


def ps_quote(s):
    """Quote one argument for PowerShell (single quotes, ' doubled)."""
    if s and _PS_SAFE.match(s):
        return s
    return "'" + s.replace("'", "''") + "'"


def render(exe, argv):
    """Ready-to-paste command lines for each shell."""
    return {"posix": " ".join(shlex.quote(x) for x in [exe] + argv),
            "powershell": "& " + " ".join(ps_quote(x) for x in [exe] + argv),
            "cmd": subprocess.list2cmdline([exe] + argv)}


def count_written(output):
    """Files present for an output name (frames for a sequence)."""
    if is_sequence(output):
        return len(frames_on_disk(output))
    return 1 if os.path.isfile(output) and os.path.getsize(output) > 0 else 0


def make_parser():
    ap = argparse.ArgumentParser(
        description="Build, check and optionally run an rvio conversion command.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  rvio_cmd.py plate.#.exr -o plate.mov --in-colour linear --out-colour srgb "
               "--codec mjpeg --quality 0.9\n"
               "  rvio_cmd.py shot.#.png -o shot.#.exr --in-colour srgb --outformat 16 float "
               "--exr-compression DWAA --quality 45\n"
               "  rvio_cmd.py shot.#.exr -o review.mov --out-colour srgb --slate Studio "
               "Shot=sh010 Version=v001 --frameburn --run\n"
               "Exit: 0 OK, 2 check failed, 1/3/124/127 from rv_tool.py, 4 outputs missing.")
    ap.add_argument("inputs", nargs="+", help="sources: files, sequences (name.#.exr), movies, "
                                              "folders")
    ap.add_argument("-o", "--output", required=True, help="output file or sequence")
    t = ap.add_argument_group("frames and timing")
    t.add_argument("--range", help="output frame range for -t, e.g. 1001-1100 or 1050")
    t.add_argument("--fps", type=float, help="input / global frame rate (-fps)")
    t.add_argument("--outfps", type=float, help="output frame rate tag (-outfps)")
    t.add_argument("--range-start", type=int, help="per-source: renumber to start here (-rs)")
    t.add_argument("--range-offset", type=int, help="per-source: shift frame numbers (-ro)")
    t.add_argument("--cut-in", type=int, help="per-source cut-in frame (-in)")
    t.add_argument("--cut-out", type=int, help="per-source cut-out frame (-out)")
    s = ap.add_argument_group("size")
    s.add_argument("--resize", type=int, nargs="+", metavar="N",
                   help="W [H]; 0 for one side keeps the aspect (-resize)")
    s.add_argument("--scale", type=float, help="scale factor (-scale)")
    s.add_argument("--outres", type=int, nargs=2, metavar=("W", "H"),
                   help="conform to this size (-outres)")
    s.add_argument("--crop", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                   help="per-source crop, inclusive pixel box")
    s.add_argument("--uncrop", type=int, nargs=4, metavar=("W", "H", "X", "Y"),
                   help="per-source uncrop (pad) into a W x H canvas at X,Y")
    s.add_argument("--pa", type=float, help="per-source pixel aspect (-pa)")
    s.add_argument("--outpa", help="output pixel aspect metadata, e.g. 2.0 or 16:9")
    c = ap.add_argument_group("colour")
    c.add_argument("--in-colour", choices=sorted(IN_COLOUR), help="linearise the input from")
    c.add_argument("--in-gamma", type=float, help="input gamma (-ingamma)")
    c.add_argument("--out-colour", choices=sorted(OUT_COLOUR), help="encode the output as")
    c.add_argument("--out-gamma", type=float, help="output gamma (-outgamma)")
    c.add_argument("--exposure", type=float, help="exposure change in stops")
    c.add_argument("--flut", help="file LUT applied to the pixels (linearising LUT)")
    c.add_argument("--llut", help="per-source look LUT")
    c.add_argument("--dlut", help="display LUT (applied last, e.g. a baked OCIO view)")
    f = ap.add_argument_group("format and codec")
    f.add_argument("--codec", help="video codec (movies) or compression (images)")
    f.add_argument("--exr-compression", help=f"EXR compression: {', '.join(EXR_CODECS)}")
    f.add_argument("--quality", type=float,
                   help="0-1 for lossy codecs; the DWA level (e.g. 45) for DWAA/DWAB")
    f.add_argument("--outformat", nargs=2, metavar=("BITS", "TYPE"),
                   help="output bits and type, e.g. 16 float, 8 int, 10 int, 32 float")
    f.add_argument("--outrgb", action="store_true", help="drop alpha (-outrgb)")
    f.add_argument("--outchannelmap", nargs="+", metavar="CH", help="e.g. R G B")
    f.add_argument("--outstereo", choices=STEREO_MODES, help="stereo output mode")
    f.add_argument("--outparam", action="append", metavar="KEY=VALUE",
                   help="codec / header parameter, repeatable (e.g. timecode=01:00:00:00, "
                        "vc:profile=3, \"shot:s=sh010\")")
    f.add_argument("--allow-codec", action="store_true",
                   help="skip the OpenRV codec check (for RV builds with more encoders)")
    au = ap.add_argument_group("audio")
    au.add_argument("--audio", help="audio file to lay under the (single) picture source")
    au.add_argument("--audio-offset", type=float, help="per-source audio offset in seconds")
    au.add_argument("--no-movie-audio", action="store_true", help="drop a movie's own audio")
    au.add_argument("--audiocodec", help="e.g. pcm_s16le, pcm_s24le")
    au.add_argument("--audiorate", type=float, help="sample rate (default 48000)")
    au.add_argument("--audiochannels", type=int, help="channel count (default 2)")
    au.add_argument("--comment", help="movie comment metadata")
    au.add_argument("--copyright", help="movie copyright metadata")
    lo = ap.add_argument_group("slates and overlays")
    lo.add_argument("--slate", nargs="+", metavar="TEXT",
                    help="SIDE_TEXT then Name=Value fields (simpleslate leader)")
    lo.add_argument("--leader-frames", type=int, help="frames the slate is held for")
    lo.add_argument("--frameburn", nargs="*", metavar="V",
                    help="frame number burn-in: [OPACITY GREY POINT_SIZE] (0.4 1.0 30)")
    lo.add_argument("--watermark", nargs="+", metavar="V", help="TEXT [OPACITY]")
    lo.add_argument("--matte", nargs="+", metavar="V", help="ASPECT [OPACITY], e.g. 2.39 0.8")
    lo.add_argument("--bug", nargs="+", metavar="V",
                    help="LOGO.tif [OPACITY HEIGHT X Y] corner logo")
    r = ap.add_argument_group("running")
    r.add_argument("--threads", type=int, help="reader/render threads (-rthreads)")
    r.add_argument("--verbose", action="store_true", help="rvio -v")
    r.add_argument("--extra", action="append", metavar="ARG",
                   help="raw rvio argument, repeatable; write --extra=-flag for options")
    r.add_argument("--print", dest="fmt", default="json",
                   choices=("json", "posix", "powershell", "cmd"), help="what to print")
    r.add_argument("--no-check", action="store_true", help="skip input and output checks")
    r.add_argument("--mkdir", action="store_true", help="create the output folder")
    r.add_argument("--run", action="store_true", help="run rvio and count the written files")
    r.add_argument("--rv-bin", help="RV bin folder (see rv_find.py)")
    r.add_argument("--timeout", type=float, default=rv_tool.DEFAULT_TIMEOUT_S,
                   help="seconds before rvio is stopped")
    return ap


def main(argv=None):
    a = make_parser().parse_args(argv)
    try:
        if not a.no_check:
            check_inputs(a)
            check_output(a)
        args, warnings = build(a)
    except CheckError as exc:
        print(f"rvio_cmd: {exc}", file=sys.stderr)
        return 2
    for w in warnings:
        print(f"rvio_cmd: warning: {w}", file=sys.stderr)

    if not a.run:
        lines = render("rvio", args)
        if a.fmt == "json":
            print(json.dumps({"argv": ["rvio"] + args, **lines, "warnings": warnings},
                             indent=2))
        else:
            print(lines[a.fmt])
        return 0

    if a.mkdir:
        os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    res = rv_tool.run("rvio", args, a.rv_bin, a.timeout)
    written = count_written(a.output)
    expected = None if a.no_check else (expected_frames(a) if is_sequence(a.output) else 1)
    summary = {"exit": res["exit"], "message": res["message"], "elapsed_s": res["elapsed_s"],
               "argv": res["argv"], "error_lines": res["error_lines"], "output": a.output,
               "files_written": written, "frames_expected": expected, "warnings": warnings}
    code = res["exit"]
    if code == 0 and (written == 0 or (expected and is_sequence(a.output)
                                       and written < expected)):
        summary["message"] = (f"rvio exited cleanly but {written} output file(s) exist for "
                              f"'{a.output}' (expected {expected or 'at least 1'})")
        code = 4
    summary["exit"] = code
    print(json.dumps(summary, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
