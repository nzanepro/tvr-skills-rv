"""The plugin icon, .claude-plugin/icon.svg.

Anthropic's plugin directory looks for the icon there or at plugin.json's `icon` (ICON_MISSING
otherwise; plugin.json sets both) and wants it square and at least 128 px. It is shown on listings next to other publishers' icons, so it
must be self-contained: no scripts, no event handlers, no raster images, no fonts or text,
and no reference to anything outside the file.
"""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ICON = REPO_ROOT / ".claude-plugin" / "icon.svg"
SVG_NS = "{http://www.w3.org/2000/svg}"
MAX_BYTES = 5 * 1024
FORBIDDEN_ELEMENTS = {"script", "foreignObject", "image", "text", "tspan", "textPath",
                      "style", "iframe", "object", "embed", "use", "a", "font", "font-face"}


def _local(tag):
    return tag.rsplit("}", 1)[-1]


@pytest.fixture(scope="module")
def raw():
    assert ICON.is_file(), f"{ICON.relative_to(REPO_ROOT)} is missing"
    return ICON.read_bytes()


@pytest.fixture(scope="module")
def root(raw):
    return ET.fromstring(raw)


def test_icon_is_small_plain_utf8_without_doctype(raw):
    assert len(raw) <= MAX_BYTES, f"{len(raw)} bytes, over {MAX_BYTES}"
    text = raw.decode("utf-8")
    assert "<!DOCTYPE" not in text.upper() and "<!ENTITY" not in text.upper()


def test_icon_is_a_square_svg_of_at_least_128_px(root):
    assert root.tag == SVG_NS + "svg"
    x, y, width, height = (float(v) for v in re.split(r"[\s,]+", root.get("viewBox", "").strip()))
    assert (x, y) == (0, 0)
    assert width == height >= 128
    for attr in ("width", "height"):
        value = root.get(attr)
        assert value is not None and float(value) == width, f"{attr}={value!r}"


def test_icon_has_no_script_text_raster_or_external_reference(root):
    for el in root.iter():
        name = _local(el.tag)
        assert el.tag.startswith(SVG_NS), f"element outside the SVG namespace: {el.tag}"
        assert name not in FORBIDDEN_ELEMENTS, f"<{name}> is not allowed in the icon"
        for key, value in el.attrib.items():
            attr = _local(key)
            assert not attr.lower().startswith("on"), f"event handler {attr} on <{name}>"
            if attr == "href":
                assert value.startswith("#"), f"external href {value!r} on <{name}>"
            for ref in re.findall(r"url\(\s*([^)]*)\)", value):
                assert ref.strip("'\" ").startswith("#"), f"external url({ref}) on <{name}>"
            assert "data:" not in value and "://" not in value, \
                f"embedded or remote resource in {attr} on <{name}>"


def test_icon_internal_references_resolve(root):
    ids = {el.get("id") for el in root.iter() if el.get("id")}
    for el in root.iter():
        for value in el.attrib.values():
            for ref in re.findall(r"url\(#([^)]+)\)", value):
                assert ref in ids, f"url(#{ref}) points at no element"


def test_plugin_json_icon_points_at_the_icon():
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    icon = plugin.get("icon")
    assert isinstance(icon, str) and icon.startswith("./"), f"icon={icon!r}"
    assert (REPO_ROOT / icon).resolve() == ICON.resolve()
