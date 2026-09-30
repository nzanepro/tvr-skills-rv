"""Tests for scripts/build_release.py, which builds the per-skill zips and release notes that
.github/workflows/release.yml publishes on a version tag.

The zips must keep the layout of the earlier hand-made releases: ``<skill>-<version>.zip``
holding ``<skill>/`` with a directory entry for every folder, then the skill's files.
"""
import importlib.util
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_release.py"


def _load_build_release():
    spec = importlib.util.spec_from_file_location("build_release", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def br():
    return _load_build_release()


def _in_git_checkout():
    if shutil.which("git") is None:
        return False
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode == 0


needs_git = pytest.mark.skipif(not _in_git_checkout(), reason="needs a git checkout of this repo")


def _plugin():
    return json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))


@needs_git
def test_builds_one_zip_per_skill_with_the_release_layout(br, tmp_path):
    plugin = _plugin()
    result = br.build(f"v{plugin['version']}", tmp_path)

    assert result["ok"] is True
    names = sorted(Path(p).name for p in result["zips"])
    expected = sorted(f"{folder}-{br.skill_version(folder)}.zip" for folder in br.skill_folders(plugin))
    assert names == expected
    for zip_path in result["zips"]:
        folder = Path(zip_path).name.rsplit("-", 1)[0]
        with zipfile.ZipFile(zip_path) as zf:
            entries = zf.namelist()
            assert entries[0] == f"{folder}/"
            assert all(name.startswith(f"{folder}/") for name in entries)
            assert f"{folder}/SKILL.md" in entries
            assert f"{folder}/LICENSE.txt" in entries
            assert not any("__pycache__" in name or name.endswith(".pyc") for name in entries)
            # Every folder has its own directory entry, as in the earlier releases.
            dirs = {name.rsplit("/", 1)[0] + "/" for name in entries
                    if not name.endswith("/") and name.count("/") > 1}
            assert dirs <= set(entries)
            # Files are the committed bytes (LF), not whatever the checkout converted them to.
            tracked = subprocess.run(["git", "cat-file", "blob", f"HEAD:{folder}/SKILL.md"],
                                     cwd=str(REPO_ROOT), stdout=subprocess.PIPE, check=True).stdout
            assert zf.read(f"{folder}/SKILL.md") == tracked


@needs_git
def test_same_commit_builds_identical_zips(br, tmp_path):
    first = br.build(None, tmp_path / "a")
    second = br.build(None, tmp_path / "b")
    for a, b in zip(first["zips"], second["zips"]):
        assert Path(a).read_bytes() == Path(b).read_bytes()


def test_tag_must_match_the_plugin_version(br, tmp_path):
    with pytest.raises(br.ReleaseError, match="does not match plugin.json version"):
        br.build("v0.0.0-not-this", tmp_path)
    assert br.main(["--tag", "v0.0.0-not-this", "--out", str(tmp_path)]) == 1
    assert not list(tmp_path.glob("*.zip"))


@needs_git
def test_release_notes_hold_the_changelog_section_and_install_commands(br, tmp_path):
    plugin = _plugin()
    result = br.build(None, tmp_path)
    notes = Path(result["notes"]).read_text(encoding="utf-8")
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert br.changelog_section(changelog, plugin["version"]) in notes
    assert f"## [{plugin['version']}]" not in notes
    assert "/plugin marketplace add nzanepro/tvr-skills-rv" in notes
    assert "/plugin install rv-tools@tvr-skills-rv" in notes
    assert "claude plugin update rv-tools@tvr-skills-rv" in notes


def test_changelog_section_stops_at_the_next_release_and_link_references(br):
    changelog = (
        "# Changelog\n\nIntro.\n\n## [Unreleased]\n\n- Pending.\n\n"
        "## [1.1.0] - 2026-02-01\n\n### Fixed\n\n- A fix.\n\n"
        "## [1.0.0] - 2026-01-01\n\n- First.\n\n"
        "[1.1.0]: https://example.invalid/v1.1.0\n[1.0.0]: https://example.invalid/v1.0.0\n"
    )
    assert br.changelog_section(changelog, "1.1.0") == "### Fixed\n\n- A fix."
    assert br.changelog_section(changelog, "1.0.0") == "- First."
    with pytest.raises(br.ReleaseError, match="no '## \\[2.0.0\\]' section"):
        br.changelog_section(changelog, "2.0.0")


def test_skill_folders_rejects_paths_outside_the_repo_root(br):
    assert br.skill_folders({"skills": ["./rvls", "./rvio/"]}) == ["rvls", "rvio"]
    for bad in (["./nested/skill"], ["../elsewhere"], []):
        with pytest.raises(br.ReleaseError):
            br.skill_folders({"skills": bad})
