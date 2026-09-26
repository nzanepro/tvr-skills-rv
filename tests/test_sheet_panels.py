"""Tests for rv-review/scripts/sheet_panels.py.

All sheets are synthetic images built with Pillow in tmp_path -- nothing here
reads or writes outside the pytest tmp directory.
"""
import json
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

MIN_PANEL_WIDTH = 400  # keeps max(64, width // 8) == 64 for every sheet below


def build_sheet(path, *, width=MIN_PANEL_WIDTH, bg, title_color, title_h=30, gap=8,
                 panel_colors, panel_h=100, marker_color=(255, 255, 0), marker=10):
    """Write a stacked contact sheet: a title band, then one panel per colour in
    panel_colors (all the same height), each separated -- and followed -- by a
    full-width run of background-coloured rows. Each panel gets a small marker
    box so its own content can be told apart from its neighbours'.

    Returns (title_box, panel_boxes) where title_box is (left, top, right, bottom)
    for the title band *including* the background gap after it (this is exactly
    what sheet_panels.panels_of treats as the title), and panel_boxes is a list
    of (left, top, right, bottom) for each panel, in the same order as panel_colors.
    """
    n = len(panel_colors)
    total_h = title_h + gap + n * (panel_h + gap)
    im = Image.new("RGB", (width, total_h), bg)
    d = ImageDraw.Draw(im)
    # sheet_panels.py samples the background colour from pixel (0, 0), so the title
    # band must leave that corner alone -- start it a few rows down, like a real
    # title band whose top-left corner is background with text drawn elsewhere.
    d.rectangle([0, 4, width - 1, title_h - 1], fill=title_color)
    title_box = (0, 0, width, title_h + gap)
    y = title_h + gap
    panel_boxes = []
    for color in panel_colors:
        d.rectangle([0, y, width - 1, y + panel_h - 1], fill=color)
        d.rectangle([4, y + 4, 4 + marker - 1, y + 4 + marker - 1], fill=marker_color)
        panel_boxes.append((0, y, width, y + panel_h))
        y += panel_h + gap
    im.save(path)
    return title_box, panel_boxes


def read_frames_json(out_dir):
    return json.loads((out_dir / "frames.json").read_text())


# ---------------------------------------------------------------------------
# split(): panel detection, naming, labels, frames.json
# ---------------------------------------------------------------------------

def test_split_finds_right_panel_count_per_sheet(tmp_path, sp):
    sheet_a = tmp_path / "shotA_red_green_blue.png"
    sheet_b = tmp_path / "shotB_before_after.png"
    build_sheet(sheet_a, bg=(30, 30, 30), title_color=(90, 90, 200),
                panel_colors=[(200, 40, 40), (40, 200, 40), (40, 40, 200)])
    build_sheet(sheet_b, bg=(30, 30, 30), title_color=(90, 90, 200),
                panel_colors=[(40, 150, 220), (220, 150, 40)])

    out = tmp_path / "out"
    paths = sp.split([str(sheet_a), str(sheet_b)], out)

    assert len(paths) == 5
    data = read_frames_json(out)
    assert [len(v["labels"]) for v in data["views"]] == [3, 2]


def test_frame_naming_and_global_running_index(tmp_path, sp):
    sheet_a = tmp_path / "shotA_red_green_blue.png"
    sheet_b = tmp_path / "shotB_before_after.png"
    build_sheet(sheet_a, bg=(30, 30, 30), title_color=(90, 90, 200),
                panel_colors=[(200, 40, 40), (40, 200, 40), (40, 40, 200)])
    build_sheet(sheet_b, bg=(30, 30, 30), title_color=(90, 90, 200),
                panel_colors=[(40, 150, 220), (220, 150, 40)])

    out = tmp_path / "out"
    paths = sp.split([str(sheet_a), str(sheet_b)], out)

    pattern = re.compile(r"^(?P<stem>.+)__(?P<label>[A-Za-z0-9-]+)__(?P<n>\d+)\.png$")
    matches = [pattern.match(p.name) for p in paths]
    assert all(matches), [p.name for p in paths]
    # index runs 1..N once, globally, in the order frames were written
    assert [int(m.group("n")) for m in matches] == list(range(1, len(paths) + 1))
    # stems match their own sheet, not the other one
    assert [m.group("stem") for m in matches] == ["shotA_red_green_blue"] * 3 + ["shotB_before_after"] * 2


def test_labels_come_from_last_tokens_of_sheet_name(tmp_path, sp):
    sheet = tmp_path / "shotA_red_green_blue.png"
    build_sheet(sheet, bg=(30, 30, 30), title_color=(90, 90, 200),
                panel_colors=[(200, 40, 40), (40, 200, 40), (40, 40, 200)])

    out = tmp_path / "out"
    paths = sp.split([str(sheet)], out)

    labels = [p.name.split("__")[1] for p in paths]
    assert labels == ["red", "green", "blue"]
    data = read_frames_json(out)
    assert data["views"][0]["labels"] == ["red", "green", "blue"]


def test_every_frame_has_the_title_band_and_its_own_panel(tmp_path, sp):
    # A single sheet needs no cross-sheet padding, so each frame's un-padded
    # content should be an exact pixel match for [title band][that panel].
    sheet = tmp_path / "shotA_red_green_blue.png"
    title_box, panel_boxes = build_sheet(
        sheet, bg=(30, 30, 30), title_color=(90, 90, 200),
        panel_colors=[(200, 40, 40), (40, 200, 40), (40, 40, 200)])
    src = np.asarray(Image.open(sheet).convert("RGB"))
    title_h = title_box[3] - title_box[1]

    out = tmp_path / "out"
    paths = sp.split([str(sheet)], out)

    for path, box in zip(paths, panel_boxes):
        frame = np.asarray(Image.open(path).convert("RGB"))
        expected_title = src[title_box[1]:title_box[3], title_box[0]:title_box[2]]
        expected_panel = src[box[1]:box[3], box[0]:box[2]]
        assert np.array_equal(frame[0:title_h], expected_title)
        assert np.array_equal(frame[title_h:title_h + (box[3] - box[1])], expected_panel)


def test_frames_padded_centred_to_largest_size_with_sheet_background(tmp_path, sp):
    bg_a = (30, 30, 30)
    bg_b = (60, 60, 60)
    sheet_a = tmp_path / "shotA_red_green_blue.png"
    sheet_b = tmp_path / "shotB_before_after.png"
    # different width AND different panel height, so both axes need padding
    build_sheet(sheet_a, width=400, bg=bg_a, title_color=(90, 90, 200), panel_h=100,
                panel_colors=[(200, 40, 40), (40, 200, 40), (40, 40, 200)])
    build_sheet(sheet_b, width=300, bg=bg_b, title_color=(200, 200, 90), panel_h=150,
                panel_colors=[(40, 150, 220), (220, 150, 40)])

    # ground truth for each sheet's own (un-padded) frames, from the function under
    # test elsewhere for panel-content correctness -- used here only to know the
    # pre-pad size/content so the padding/centring math can be checked independently.
    frames_a, bgc_a = sp.panels_of(sheet_a)
    frames_b, bgc_b = sp.panels_of(sheet_b)
    assert bgc_a == bg_a and bgc_b == bg_b

    out = tmp_path / "out"
    paths = sp.split([str(sheet_a), str(sheet_b)], out)
    data = read_frames_json(out)

    W, H = data["size"]
    fw_a, fh_a = frames_a[0][1].size
    fw_b, fh_b = frames_b[0][1].size
    assert W == max(fw_a, fw_b)
    assert H == max(fh_a, fh_b)
    # sheet A is narrower/shorter than sheet B in exactly one axis each -> both need padding
    assert (fw_a, fh_a) != (W, H)
    assert (fw_b, fh_b) != (W, H)

    for path, (_, frame), bgc in zip(paths[:3], frames_a, [bgc_a] * 3):
        _assert_padded(path, frame, bgc, W, H)
    for path, (_, frame), bgc in zip(paths[3:], frames_b, [bgc_b] * 2):
        _assert_padded(path, frame, bgc, W, H)


def _assert_padded(path, source_frame, bgc, W, H):
    out_im = np.asarray(Image.open(path).convert("RGB"))
    assert out_im.shape[:2] == (H, W)
    src = np.asarray(source_frame)
    ox = (W - source_frame.width) // 2
    oy = (H - source_frame.height) // 2
    # interior matches the un-padded frame exactly, at the centred offset
    assert np.array_equal(out_im[oy:oy + source_frame.height, ox:ox + source_frame.width], src)
    # every border pixel is the sheet's own background colour
    bgc_arr = np.array(bgc, dtype=out_im.dtype)
    if oy > 0:
        assert np.all(out_im[0] == bgc_arr) and np.all(out_im[H - 1] == bgc_arr)
    if ox > 0:
        assert np.all(out_im[:, 0] == bgc_arr) and np.all(out_im[:, W - 1] == bgc_arr)


def test_frames_json_has_absolute_paths_size_and_first_frame_per_view(tmp_path, sp):
    sheet_a = tmp_path / "shotA_red_green_blue.png"
    sheet_b = tmp_path / "shotB_before_after.png"
    build_sheet(sheet_a, bg=(30, 30, 30), title_color=(90, 90, 200),
                panel_colors=[(200, 40, 40), (40, 200, 40), (40, 40, 200)])
    build_sheet(sheet_b, bg=(30, 30, 30), title_color=(90, 90, 200),
                panel_colors=[(40, 150, 220), (220, 150, 40)])

    out = tmp_path / "out"
    sp.split([str(sheet_a), str(sheet_b)], out)
    data = read_frames_json(out)

    assert "size" in data and len(data["size"]) == 2
    assert all(Path(f).is_absolute() for f in data["frames"])
    assert Path(data["frames"][0]).is_absolute()
    assert data["views"][0]["frame"] == 1  # sheet A's first panel is global frame 1
    assert data["views"][1]["frame"] == 4  # sheet B's first panel starts after A's 3
    assert Path(data["views"][0]["sheet"]) == sheet_a.resolve()
    assert Path(data["views"][1]["sheet"]) == sheet_b.resolve()


def test_split_relative_out_still_yields_absolute_paths(tmp_path, sp, monkeypatch):
    monkeypatch.chdir(tmp_path)
    build_sheet(tmp_path / "shotA_red_green_blue.png", bg=(30, 30, 30),
                title_color=(90, 90, 200),
                panel_colors=[(200, 40, 40), (40, 200, 40), (40, 40, 200)])

    paths = sp.split(["shotA_red_green_blue.png"], "rv_frames")

    assert all(p.is_absolute() for p in paths)
    data = read_frames_json(tmp_path / "rv_frames")
    assert all(Path(f).is_absolute() for f in data["frames"])


def test_single_panel_sheet_is_rejected(tmp_path, sp):
    sheet = tmp_path / "shotA_single.png"
    build_sheet(sheet, bg=(30, 30, 30), title_color=(90, 90, 200),
                panel_colors=[(200, 40, 40)])

    with pytest.raises(SystemExit) as excinfo:
        sp.panels_of(sheet)
    assert "found 1 panels" in str(excinfo.value)


# ---------------------------------------------------------------------------
# label(): unstacked renders
# ---------------------------------------------------------------------------

def test_label_burns_title_and_label_box_and_sets_output_height(tmp_path, sp):
    im_a = Image.new("RGB", (200, 120), (80, 80, 80))
    im_b = Image.new("RGB", (200, 120), (120, 40, 40))
    path_a = tmp_path / "a.png"
    path_b = tmp_path / "b.png"
    im_a.save(path_a)
    im_b.save(path_b)

    out = tmp_path / "out"
    paths = sp.label("Shot 010", [(str(path_a), "before"), (str(path_b), "after")], out)

    assert len(paths) == 2
    for path, src_im, src_color in zip(paths, [im_a, im_b], [(80, 80, 80), (120, 40, 40)]):
        frame = Image.open(path).convert("RGB")
        assert frame.size == (src_im.width, src_im.height + 50)
        arr = np.asarray(frame)
        # a corner well away from the title text stays pure background
        assert tuple(arr[2, 2]) == (24, 24, 24)
        # the source image is pasted at y=50, unaffected far from the label box
        assert tuple(arr[-5, -5]) == src_color
        # the label box (burned onto the image, near its top-left) is a black rectangle
        assert tuple(arr[62, 12]) == (0, 0, 0)


def test_label_rejects_images_of_different_sizes(tmp_path, sp):
    path_a = tmp_path / "a.png"
    path_b = tmp_path / "b.png"
    Image.new("RGB", (200, 120), (80, 80, 80)).save(path_a)
    Image.new("RGB", (150, 90), (120, 40, 40)).save(path_b)

    with pytest.raises(SystemExit) as excinfo:
        sp.label("Shot 010", [(str(path_a), "before"), (str(path_b), "after")], tmp_path / "out")
    assert "differs from" in str(excinfo.value)
