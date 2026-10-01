"""Tests for rv-review/scripts/review_manifest.py.

Covers: validate() (every kind of problem the docstring/format promises to catch, and that
several problems in one bad manifest are all reported, not just the first); normalise()
(defaults, path resolution, label fallback, meta/annotation carry-through); load() from a file
and from stdin (including relative-path resolution against the manifest's own folder, not the
process cwd); from_frames_json() (the legacy sheet_panels.py split output); the small pure
helpers rv_tokens(), group_starts() and is_sequence_spec(); and the CLI (main(), via
subprocess) for both a file argument and "-" on stdin.

Not exercised here: rv_review.py / rv_session.py, which import this module but are covered by
their own test files. review_manifest.py never checks that an item's media files actually
exist on disk (only the manifest file itself, for a non-stdin load()), so no test creates
placeholder image files -- plain path strings are enough.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "rv-review" / "scripts" / "review_manifest.py"


def base_manifest(**overrides):
    """The smallest manifest that validate() accepts, with overrides merged in."""
    m = {"schema_version": 1, "items": [{"path": "a.png"}]}
    m.update(overrides)
    return m


def write_manifest(tmp_path, data, name="review.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run_cli(args, cwd=None, input_text=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          timeout=30, cwd=cwd, input=input_text)


# ---------------------------------------------------------------------------
# validate(): schema / schema_version
# ---------------------------------------------------------------------------

def test_validate_minimal_manifest_is_clean(rm):
    assert rm.validate(base_manifest()) == []


def test_validate_not_a_dict(rm):
    probs = rm.validate(["not", "a", "dict"])
    assert probs == ["manifest: must be a JSON object"]


def test_validate_schema_version_missing(rm):
    data = base_manifest()
    del data["schema_version"]
    probs = rm.validate(data)
    assert any("schema_version" in p and "required" in p for p in probs)


@pytest.mark.parametrize("bad_version", ["1", 1.0, True])
def test_validate_schema_version_not_an_int(rm, bad_version):
    data = base_manifest(schema_version=bad_version)
    probs = rm.validate(data)
    assert any("schema_version" in p and "integer" in p for p in probs)


def test_validate_schema_version_none_is_treated_as_missing(rm):
    # explicit null and an absent key hit the same "required" branch (data.get returns None
    # either way), not the "must be an integer" branch.
    data = base_manifest(schema_version=None)
    probs = rm.validate(data)
    assert any("schema_version" in p and "required" in p for p in probs)


def test_validate_schema_version_newer_says_so_and_says_update_the_skill(rm):
    data = base_manifest(schema_version=rm.SCHEMA_VERSION + 1)
    probs = rm.validate(data)
    assert any("newer" in p and "update the skill" in p for p in probs)


def test_validate_schema_version_zero_or_negative(rm):
    probs = rm.validate(base_manifest(schema_version=0))
    assert any("schema_version" in p and "1 or more" in p for p in probs)


def test_validate_schema_wrong(rm):
    data = base_manifest(schema="something-else")
    probs = rm.validate(data)
    assert any("schema:" in p and "something-else" in p for p in probs)


def test_validate_schema_matching_is_fine(rm):
    assert rm.validate(base_manifest(schema=rm.SCHEMA)) == []


# ---------------------------------------------------------------------------
# validate(): top-level keys, layout, stereo, fps, marks, meta, title
# ---------------------------------------------------------------------------

def test_validate_unknown_top_level_key(rm):
    probs = rm.validate(base_manifest(bogus=1))
    assert any("bogus" in p and "unknown top-level key" in p for p in probs)


def test_validate_every_layout_accepted(rm):
    for layout in rm.LAYOUTS:
        assert rm.validate(base_manifest(layout=layout)) == []


def test_validate_bad_layout(rm):
    probs = rm.validate(base_manifest(layout="spin"))
    assert any(p.startswith("layout:") for p in probs)


def test_validate_every_stereo_mode_accepted(rm):
    for mode in rm.STEREO_MODES:
        assert rm.validate(base_manifest(stereo=mode)) == []


def test_validate_bad_stereo(rm):
    probs = rm.validate(base_manifest(stereo="upside-down"))
    assert any(p.startswith("stereo:") for p in probs)


@pytest.mark.parametrize("bad_fps", [0, -24, "24", True])
def test_validate_bad_top_level_fps(rm, bad_fps):
    probs = rm.validate(base_manifest(fps=bad_fps))
    assert any(p.startswith("fps:") for p in probs)


def test_validate_good_top_level_fps(rm):
    assert rm.validate(base_manifest(fps=23.976)) == []


@pytest.mark.parametrize("marks", ["auto", "groups", "none", [], [1, 4, 7]])
def test_validate_good_marks(rm, marks):
    assert rm.validate(base_manifest(marks=marks)) == []


@pytest.mark.parametrize("marks", ["sometimes", [0], [1, -3], [1.5]])
def test_validate_bad_marks(rm, marks):
    probs = rm.validate(base_manifest(marks=marks))
    assert any(p.startswith("marks:") for p in probs)


def test_validate_top_level_meta_must_be_object(rm):
    probs = rm.validate(base_manifest(meta=["not", "an", "object"]))
    assert any(p.startswith("meta:") for p in probs)


def test_validate_title_must_be_a_string(rm):
    probs = rm.validate(base_manifest(title=123))
    assert any(p.startswith("title:") for p in probs)


# ---------------------------------------------------------------------------
# validate(): groups
# ---------------------------------------------------------------------------

def test_validate_groups_must_be_a_list(rm):
    probs = rm.validate(base_manifest(groups="not-a-list"))
    assert any(p.startswith("groups:") for p in probs)


def test_validate_group_missing_id(rm):
    probs = rm.validate(base_manifest(groups=[{"label": "no id"}]))
    assert any("groups[0].id" in p and "required" in p for p in probs)


def test_validate_group_empty_id(rm):
    probs = rm.validate(base_manifest(groups=[{"id": ""}]))
    assert any("groups[0].id" in p and "required" in p for p in probs)


def test_validate_duplicate_group_ids(rm):
    data = base_manifest(groups=[{"id": "a"}, {"id": "a"}])
    probs = rm.validate(data)
    assert any("groups[1].id" in p and "duplicate id" in p for p in probs)


def test_validate_group_label_and_title_must_be_strings(rm):
    probs = rm.validate(base_manifest(groups=[{"id": "a", "label": 1, "title": 2}]))
    assert any("groups[0].label" in p for p in probs)
    assert any("groups[0].title" in p for p in probs)


def test_validate_group_meta_must_be_object(rm):
    probs = rm.validate(base_manifest(groups=[{"id": "a", "meta": "nope"}]))
    assert any("groups[0].meta" in p for p in probs)


def test_validate_group_unknown_key(rm):
    probs = rm.validate(base_manifest(groups=[{"id": "a", "bogus": 1}]))
    assert any("groups[0].bogus" in p and "unknown key" in p for p in probs)


def test_validate_group_not_an_object(rm):
    probs = rm.validate(base_manifest(groups=["not-a-dict"]))
    assert any("groups[0]" in p and "must be an object" in p for p in probs)


# ---------------------------------------------------------------------------
# validate(): items - presence, path, unknown keys, group refs
# ---------------------------------------------------------------------------

def test_validate_items_missing(rm):
    data = {"schema_version": 1}
    probs = rm.validate(data)
    assert any(p.startswith("items:") and "required" in p for p in probs)


def test_validate_items_empty_list(rm):
    probs = rm.validate(base_manifest(items=[]))
    assert any(p.startswith("items:") and "required" in p for p in probs)


def test_validate_items_not_a_list(rm):
    probs = rm.validate(base_manifest(items="not-a-list"))
    assert any(p.startswith("items:") for p in probs)


def test_validate_item_not_an_object(rm):
    probs = rm.validate(base_manifest(items=["not-a-dict"]))
    assert any("items[0]" in p and "must be an object" in p for p in probs)


def test_validate_item_missing_path(rm):
    probs = rm.validate(base_manifest(items=[{"label": "no path"}]))
    assert any("items[0].path" in p and "required" in p for p in probs)


def test_validate_item_path_empty_string(rm):
    probs = rm.validate(base_manifest(items=[{"path": ""}]))
    assert any("items[0].path" in p and "not be empty" in p for p in probs)


def test_validate_item_path_wrong_type(rm):
    probs = rm.validate(base_manifest(items=[{"path": 123}]))
    assert any("items[0].path" in p and "string or a list" in p for p in probs)


def test_validate_item_path_list_ok(rm):
    assert rm.validate(base_manifest(items=[{"path": ["left.exr", "right.exr"]}])) == []


@pytest.mark.parametrize("bad_path_list", [[], [""], ["ok", 5]])
def test_validate_item_path_list_bad(rm, bad_path_list):
    probs = rm.validate(base_manifest(items=[{"path": bad_path_list}]))
    assert any("items[0].path" in p for p in probs)


def test_validate_item_unknown_key(rm):
    probs = rm.validate(base_manifest(items=[{"path": "a.png", "bogus": 1}]))
    assert any("items[0].bogus" in p and "unknown key" in p for p in probs)


@pytest.mark.parametrize("key", ["label", "title", "view"])
def test_validate_item_string_fields_wrong_type(rm, key):
    probs = rm.validate(base_manifest(items=[{"path": "a.png", key: 5}]))
    assert any(f"items[0].{key}" in p for p in probs)


def test_validate_item_group_references_unknown_group(rm):
    probs = rm.validate(base_manifest(items=[{"path": "a.png", "group": "ghost"}]))
    assert any("items[0].group" in p and "ghost" in p and "not an id" in p for p in probs)


def test_validate_item_group_references_known_group(rm):
    data = base_manifest(groups=[{"id": "side"}], items=[{"path": "a.png", "group": "side"}])
    assert rm.validate(data) == []


# ---------------------------------------------------------------------------
# validate(): in / out / fps / stereo_views / meta on items
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["in", "out"])
def test_validate_item_in_out_must_be_int(rm, key):
    probs = rm.validate(base_manifest(items=[{"path": "a.png", key: "1001"}]))
    assert any(f"items[0].{key}" in p and "integer" in p for p in probs)


def test_validate_item_out_before_in(rm):
    probs = rm.validate(base_manifest(items=[{"path": "a.png", "in": 100, "out": 50}]))
    assert any("out (50) is before in (100)" in p for p in probs)


def test_validate_item_out_equal_in_is_fine(rm):
    assert rm.validate(base_manifest(items=[{"path": "a.png", "in": 50, "out": 50}])) == []


@pytest.mark.parametrize("bad_fps", [0, -1, "24"])
def test_validate_item_fps_bad(rm, bad_fps):
    probs = rm.validate(base_manifest(items=[{"path": "a.png", "fps": bad_fps}]))
    assert any("items[0].fps" in p for p in probs)


@pytest.mark.parametrize("sv", [["left"], ["left", "right", "top"], [1, 2], ["", "right"]])
def test_validate_item_stereo_views_bad(rm, sv):
    probs = rm.validate(base_manifest(items=[{"path": "a.png", "stereo_views": sv}]))
    assert any("items[0].stereo_views" in p for p in probs)


def test_validate_item_stereo_views_good(rm):
    data = base_manifest(items=[{"path": "a.png", "stereo_views": ["left", "right"]}])
    assert rm.validate(data) == []


def test_validate_item_meta_must_be_object(rm):
    probs = rm.validate(base_manifest(items=[{"path": "a.png", "meta": "nope"}]))
    assert any("items[0].meta" in p for p in probs)


# ---------------------------------------------------------------------------
# validate(): annotations
# ---------------------------------------------------------------------------

def test_validate_annotations_not_a_list(rm):
    data = base_manifest(items=[{"path": "a.png", "annotations": {"text": "x"}}])
    probs = rm.validate(data)
    assert any("items[0].annotations" in p and "must be a list" in p for p in probs)


def test_validate_annotations_entry_not_an_object(rm):
    data = base_manifest(items=[{"path": "a.png", "annotations": ["not-a-dict"]}])
    probs = rm.validate(data)
    assert any("items[0].annotations[0]" in p and "must be an object" in p for p in probs)


ANNOTATION_CASES = [
    ({}, "text"),
    ({"text": ""}, "text"),
    ({"text": "ok", "bogus": 1}, "unknown key"),
    ({"text": "ok", "frame": 0}, "frame"),
    ({"text": "ok", "frame": 1.5}, "frame"),
    ({"text": "ok", "source_frame": "x"}, "source_frame"),
    ({"text": "ok", "position": [1]}, "position"),
    ({"text": "ok", "position": ["a", "b"]}, "position"),
    ({"text": "ok", "size": 0}, "size"),
    ({"text": "ok", "size": -1}, "size"),
    ({"text": "ok", "color": [1, 2]}, "color"),
    ({"text": "ok", "color": ["r", "g", "b"]}, "color"),
]


@pytest.mark.parametrize("annotation,expect", ANNOTATION_CASES)
def test_validate_annotation_problems(rm, annotation, expect):
    data = base_manifest(items=[{"path": "a.png", "annotations": [annotation]}])
    probs = rm.validate(data)
    assert any("items[0].annotations[0]" in p and expect in p for p in probs)


def test_validate_annotation_good(rm):
    ann = {"text": "check the edge", "frame": 3, "source_frame": 1010, "position": [0.5, 0.2],
           "size": 12, "color": [1, 0, 0]}
    data = base_manifest(items=[{"path": "a.png", "annotations": [ann]}])
    assert rm.validate(data) == []


def test_validate_annotation_color_out_of_range_is_rejected(rm):
    data = base_manifest(items=[{"path": "a.png", "annotations": [{"text": "x", "color": [2, 3, 4]}]}])
    assert rm.validate(data) != []


# ---------------------------------------------------------------------------
# ManifestError.problems: several problems from one manifest
# ---------------------------------------------------------------------------

def test_manifest_error_collects_every_problem_at_once(rm):
    data = {
        "schema_version": 99,
        "bogus_top": 1,
        "layout": "spin",
        "groups": [{"id": "a"}, {"id": "a"}],
        "items": [{"path": "a.png", "group": "ghost", "bogus_item": 1, "in": 10, "out": 1}],
    }
    with pytest.raises(rm.ManifestError) as excinfo:
        rm.normalise(data)
    problems = excinfo.value.problems
    assert len(problems) >= 5
    joined = "\n".join(problems)
    for expect in ("newer", "bogus_top", "layout:", "duplicate id", "ghost", "bogus_item",
                   "before in"):
        assert expect in joined


# ---------------------------------------------------------------------------
# normalise(): defaults, path resolution, labels, meta/annotation carry-through
# ---------------------------------------------------------------------------

def test_normalise_raises_manifest_error_on_bad_input(rm):
    with pytest.raises(rm.ManifestError):
        rm.normalise({"schema_version": 1})


def test_normalise_fills_defaults(rm):
    out = rm.normalise(base_manifest())
    assert out["schema"] == rm.SCHEMA
    assert out["schema_version"] == rm.SCHEMA_VERSION
    assert out["title"] == ""
    assert out["layout"] == "sequence"
    assert out["marks"] == "auto"
    assert out["meta"] == {}
    assert out["groups"] == []


def test_normalise_omits_fps_and_stereo_when_absent(rm):
    out = rm.normalise(base_manifest())
    assert "fps" not in out
    assert "stereo" not in out


def test_normalise_keeps_fps_and_stereo_when_present(rm):
    out = rm.normalise(base_manifest(fps=30, stereo="pair"))
    assert out["fps"] == 30
    assert out["stereo"] == "pair"


def test_normalise_relative_path_resolved_against_given_base(tmp_path, rm):
    data = base_manifest(items=[{"path": "renders/side_v1.png"}])
    out = rm.normalise(data, base=tmp_path)
    assert Path(out["items"][0]["path"]) == tmp_path / "renders" / "side_v1.png"


def test_normalise_absolute_path_left_alone(tmp_path, rm):
    abs_path = str(tmp_path / "elsewhere" / "shot.exr")
    out = rm.normalise(base_manifest(items=[{"path": abs_path}]), base=tmp_path / "manifest_dir")
    assert Path(out["items"][0]["path"]) == Path(abs_path)


def test_normalise_path_list_resolved_and_label_falls_back_to_first_entry(tmp_path, rm):
    data = base_manifest(items=[{"path": ["left.exr", "right.exr"]}])
    out = rm.normalise(data, base=tmp_path)
    item = out["items"][0]
    assert item["path"] == [str(tmp_path / "left.exr"), str(tmp_path / "right.exr")]
    assert item["label"] == "left.exr"


def test_normalise_label_defaults_to_file_name(tmp_path, rm):
    data = base_manifest(items=[{"path": "shots/side_v1.png"}])
    out = rm.normalise(data, base=tmp_path)
    assert out["items"][0]["label"] == "side_v1.png"


def test_normalise_explicit_label_kept(tmp_path, rm):
    data = base_manifest(items=[{"path": "a.png", "label": "v1"}])
    out = rm.normalise(data, base=tmp_path)
    assert out["items"][0]["label"] == "v1"


def test_normalise_item_index_matches_position(tmp_path, rm):
    data = base_manifest(items=[{"path": "a.png"}, {"path": "b.png"}, {"path": "c.png"}])
    out = rm.normalise(data, base=tmp_path)
    assert [it["index"] for it in out["items"]] == [0, 1, 2]


def test_normalise_group_defaults(rm):
    data = base_manifest(groups=[{"id": "side"}], items=[{"path": "a.png", "group": "side"}])
    out = rm.normalise(data)
    g = out["groups"][0]
    assert g == {"id": "side", "label": "side", "title": "", "meta": {}}


def test_normalise_group_explicit_fields_kept(rm):
    data = base_manifest(groups=[{"id": "side", "label": "Side view", "title": "t",
                                  "meta": {"k": 1}}],
                         items=[{"path": "a.png", "group": "side"}])
    out = rm.normalise(data)
    g = out["groups"][0]
    assert g["label"] == "Side view" and g["title"] == "t" and g["meta"] == {"k": 1}


def test_normalise_annotations_copied_through(tmp_path, rm):
    ann = {"text": "check it", "frame": 1}
    data = base_manifest(items=[{"path": "a.png", "annotations": [ann]}])
    out = rm.normalise(data, base=tmp_path)
    assert out["items"][0]["annotations"] == [ann]
    assert out["items"][0]["annotations"][0] is not ann  # copied, not aliased


def test_normalise_meta_round_trips_nested_unicode_unchanged(tmp_path, rm):
    meta = {
        "shot": "ショット010",  # unicode (Japanese) text
        "note": "café résumé",
        "versions": [1, 2, 3],
        "nested": {"a": {"b": ["x", "y", {"z": True}]}},
    }
    data = base_manifest(meta=dict(meta), items=[{"path": "a.png", "meta": dict(meta)}])
    out = rm.normalise(data, base=tmp_path)
    assert out["meta"] == meta
    assert out["items"][0]["meta"] == meta


# ---------------------------------------------------------------------------
# load(): from a file
# ---------------------------------------------------------------------------

def test_load_good_manifest_from_file(tmp_path, rm):
    path = write_manifest(tmp_path, base_manifest())
    out = rm.load(path)
    assert out["items"][0]["path"] == str(tmp_path / "a.png")


def test_load_missing_file_raises(tmp_path, rm):
    with pytest.raises(rm.ManifestError) as excinfo:
        rm.load(tmp_path / "missing.json")
    assert any("not found" in p for p in excinfo.value.problems)


def test_load_invalid_json_raises(tmp_path, rm):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(rm.ManifestError) as excinfo:
        rm.load(path)
    assert any("not valid JSON" in p for p in excinfo.value.problems)


def test_load_resolves_relative_paths_against_manifest_folder_not_cwd(tmp_path, rm, monkeypatch):
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    decoy_cwd = tmp_path / "decoy"
    decoy_cwd.mkdir()
    monkeypatch.chdir(decoy_cwd)

    path = write_manifest(manifest_dir, base_manifest(items=[{"path": "renders/a.png"}]))
    out = rm.load(path)

    assert Path(out["items"][0]["path"]) == manifest_dir / "renders" / "a.png"
    assert decoy_cwd not in Path(out["items"][0]["path"]).parents


def test_load_legacy_frames_json_converts(tmp_path, rm):
    frames_data = {
        "frames": ["a1.png", "a2.png"],
        "views": [{"frame": 1, "sheet": "sheetA.png", "labels": ["before", "after"]}],
    }
    path = write_manifest(tmp_path, frames_data, name="frames.json")
    out = rm.load(path)
    assert out["marks"] == "groups"
    assert [it["label"] for it in out["items"]] == ["before", "after"]
    assert out["items"][0]["path"] == str(tmp_path / "a1.png")


# ---------------------------------------------------------------------------
# from_frames_json(): direct tests
# ---------------------------------------------------------------------------

def test_from_frames_json_one_group_per_view_with_correct_labels(rm):
    data = {
        "frames": ["a1.png", "a2.png", "a3.png", "a4.png", "a5.png"],
        "views": [
            {"frame": 1, "sheet": "sheetA.png", "labels": ["red", "green"]},
            {"frame": 3, "sheet": "sheetB.png", "labels": ["x", "y", "z"]},
        ],
    }
    out = rm.from_frames_json(data)
    assert out["schema"] == rm.SCHEMA
    assert out["schema_version"] == rm.SCHEMA_VERSION
    assert out["marks"] == "groups"
    assert [g["id"] for g in out["groups"]] == ["v1", "v2"]
    assert [g["label"] for g in out["groups"]] == ["sheetA", "sheetB"]
    assert [it["group"] for it in out["items"]] == ["v1", "v1", "v2", "v2", "v2"]
    assert [it["label"] for it in out["items"]] == ["red", "green", "x", "y", "z"]
    assert [it["path"] for it in out["items"]] == data["frames"]


def test_from_frames_json_labels_fall_back_to_frame_stem(rm):
    data = {"frames": ["shot_a.png", "shot_b.png"],
           "views": [{"frame": 1, "sheet": "s.png"}]}  # no labels given
    out = rm.from_frames_json(data)
    assert [it["label"] for it in out["items"]] == ["shot_a", "shot_b"]


def test_from_frames_json_uncovered_frames_get_their_own_item(rm):
    # the one view starts at frame 2, so frame 1 is never assigned to a group
    data = {"frames": ["a1.png", "a2.png", "a3.png"],
           "views": [{"frame": 2, "sheet": "s.png", "labels": ["mid"]}]}
    out = rm.from_frames_json(data)
    paths = [it["path"] for it in out["items"]]
    assert set(paths) == {"a1.png", "a2.png", "a3.png"}
    leftover = [it for it in out["items"] if "group" not in it]
    assert {it["path"] for it in leftover} == {"a1.png"}
    grouped = [it for it in out["items"] if "group" in it]
    assert {it["path"] for it in grouped} == {"a2.png", "a3.png"}


def test_from_frames_json_no_views_means_no_groups_and_auto_marks(rm):
    out = rm.from_frames_json({"frames": ["a1.png", "a2.png"]})
    assert "groups" not in out
    assert out["marks"] == "auto"


# ---------------------------------------------------------------------------
# rv_tokens()
# ---------------------------------------------------------------------------

def test_rv_tokens_plain_path_no_options(rm):
    assert rm.rv_tokens({"path": "a.png"}) == ["a.png"]


def test_rv_tokens_multi_media_without_options_still_bracketed(rm):
    item = {"path": ["left.exr", "right.exr"]}
    assert rm.rv_tokens(item) == ["[", "left.exr", "right.exr", "]"]


def test_rv_tokens_options_bracket_a_single_path(rm):
    item = {"path": "a.exr", "in": 1001, "out": 1100, "fps": 24, "view": "centre"}
    assert rm.rv_tokens(item) == [
        "[", "a.exr", "-in", "1001", "-out", "1100", "-fps", "24",
        "-select", "view", "centre", "]",
    ]


def test_rv_tokens_falsy_view_name_adds_no_select(rm):
    item = {"path": "a.exr", "view": ""}
    assert rm.rv_tokens(item) == ["a.exr"]


# ---------------------------------------------------------------------------
# group_starts()
# ---------------------------------------------------------------------------

def test_group_starts_first_appearance_order_ignores_ungrouped(rm):
    items = [{"group": "a"}, {"group": "a"}, {"path": "x"}, {"group": "b"}, {"group": "a"}]
    item_frames = [(1, 10), (11, 20), (21, 21), (22, 30), (31, 40)]
    assert rm.group_starts(items, item_frames) == [1, 22]


def test_group_starts_no_groups_is_empty(rm):
    items = [{"path": "x"}, {"path": "y"}]
    item_frames = [(1, 5), (6, 10)]
    assert rm.group_starts(items, item_frames) == []


# ---------------------------------------------------------------------------
# is_sequence_spec()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("name.#.exr", True),
    ("name.@@@@.exr", True),
    ("name.%04d.exr", True),
    ("name.%d.exr", True),
    ("shot.mov", False),
    ("plain_name.png", False),
    ("shot_004.png", False),
])
def test_is_sequence_spec(rm, path, expected):
    assert rm.is_sequence_spec(path) is expected


def test_is_sequence_spec_only_looks_at_the_file_name(rm):
    assert rm.is_sequence_spec("folder#1/name.exr") is False


# ---------------------------------------------------------------------------
# CLI, via subprocess (sys.executable only)
# ---------------------------------------------------------------------------

def test_cli_help_exits_0(rm):
    result = run_cli(["--help"])
    assert result.returncode == 0
    assert "manifest" in result.stdout
    assert "stdin" in result.stdout


def test_cli_good_manifest_from_file(tmp_path):
    path = write_manifest(tmp_path, base_manifest())
    result = run_cli([str(path)])
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["manifest"]["items"][0]["path"] == str(tmp_path / "a.png")


def test_cli_bad_manifest_from_file_exits_1(tmp_path):
    path = write_manifest(tmp_path, {"schema_version": 1})  # no items
    result = run_cli([str(path)])
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any("items" in p for p in payload["problems"])


def test_cli_reads_manifest_from_stdin_and_resolves_against_cwd(tmp_path):
    data = base_manifest(items=[{"path": "renders/a.png"}])
    result = run_cli(["-"], cwd=tmp_path, input_text=json.dumps(data))
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    resolved = Path(payload["manifest"]["items"][0]["path"])
    assert resolved == tmp_path / "renders" / "a.png"


def test_cli_stdin_bad_manifest_exits_1(tmp_path):
    result = run_cli(["-"], cwd=tmp_path, input_text=json.dumps({"schema_version": 5,
                                                                  "items": [{"path": "a.png"}]}))
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any("newer" in p for p in payload["problems"])
