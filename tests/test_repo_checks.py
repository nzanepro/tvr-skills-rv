"""Tests for scripts/check_repo.py, the fresh-install / release checklist.

scripts/check_repo.py is a standalone script (not part of an installed package),
loaded here by file path the same way tests/conftest.py loads sheet_panels.py and
rv_review.py.

Two kinds of test:

- Run each check against *this actual repository* and assert it finds nothing.
  This doubles as a privacy gate: if someone later commits a personal path, a
  broken SKILL.md, or a dangling marketplace skill entry, this test fails in CI.
- Run each check against a small synthetic repo layout built in ``tmp_path`` and
  assert it *does* flag a planted problem, so the checks are proven to actually
  detect what they claim to, not just pass vacuously on a clean tree.
"""
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_repo.py"


def _load_check_repo():
    spec = importlib.util.spec_from_file_location("check_repo", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def cr():
    """A fresh import of scripts/check_repo.py for each test."""
    return _load_check_repo()


# --- checks against the real repository -----------------------------------


def test_skill_frontmatter_clean_on_real_repo(cr):
    problems = cr.check_skill_frontmatter()
    assert problems == [], "\n".join(str(p) for p in problems)


def test_marketplace_skills_paths_clean_on_real_repo(cr):
    problems = cr.check_marketplace_skills_paths()
    assert problems == [], "\n".join(str(p) for p in problems)


def test_personal_paths_clean_on_real_repo(cr):
    problems = cr.check_personal_paths()
    assert problems == [], "\n".join(str(p) for p in problems)


def test_main_exits_zero_on_real_repo(cr, capsys):
    assert cr.main([]) == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_main_rejects_unknown_check_name(cr):
    assert cr.main(["not-a-real-check"]) == 2


# --- synthetic repos: prove each check can actually fail --------------------


def test_skill_frontmatter_flags_missing_key(cr, tmp_path, monkeypatch):
    skill_dir = tmp_path / "example-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: example-skill\n"
        "license: MIT\n"
        "compatibility: test only\n"
        "metadata:\n"
        "  version: 0.1.0\n"
        "---\n"
        "# Missing a description key on purpose\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_skill_frontmatter()

    assert any(p.check == "skill-frontmatter" and "description" in p.detail for p in problems)


def test_skill_frontmatter_flags_name_folder_mismatch(cr, tmp_path, monkeypatch):
    skill_dir = tmp_path / "example-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: totally-different-name\n"
        "description: a test skill\n"
        "license: MIT\n"
        "compatibility: test only\n"
        "metadata:\n"
        "  version: 0.1.0\n"
        "---\n"
        "# Body\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_skill_frontmatter()

    assert any("does not match its folder" in p.detail for p in problems)


def test_skill_frontmatter_passes_for_well_formed_skill(cr, tmp_path, monkeypatch):
    skill_dir = tmp_path / "example-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: example-skill\n"
        "description: a test skill\n"
        "license: MIT\n"
        "compatibility: test only\n"
        "metadata:\n"
        "  version: 0.1.0\n"
        "---\n"
        "# Body\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    assert cr.check_skill_frontmatter() == []


def test_marketplace_skills_paths_flags_missing_skill_dir(cr, tmp_path, monkeypatch):
    (tmp_path / ".claude-plugin").mkdir()
    manifest = {
        "plugins": [
            {
                "name": "example",
                "source": "./",
                "skills": ["./does-not-exist"],
            }
        ]
    }
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_marketplace_skills_paths()

    assert any("does not exist" in p.detail for p in problems)


def test_marketplace_skills_paths_flags_missing_skill_md(cr, tmp_path, monkeypatch):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / "example-skill").mkdir()  # exists, but no SKILL.md inside
    manifest = {
        "plugins": [
            {
                "name": "example",
                "source": "./",
                "skills": ["./example-skill"],
            }
        ]
    }
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_marketplace_skills_paths()

    assert any("no SKILL.md" in p.detail for p in problems)


def test_marketplace_skills_paths_passes_when_all_present(cr, tmp_path, monkeypatch):
    (tmp_path / ".claude-plugin").mkdir()
    skill_dir = tmp_path / "example-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: example-skill\n---\n", encoding="utf-8")
    manifest = {
        "plugins": [
            {
                "name": "example",
                "source": "./",
                "skills": ["./example-skill"],
            }
        ]
    }
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    assert cr.check_marketplace_skills_paths() == []


@pytest.mark.parametrize(
    "line",
    [
        r"C:\Users\someone\project\notes.txt",
        "/home/someone/.rv/Packages",
        "/Users/someone/Library/Application Support/RV",
        "contact real.person@example.com for access",
        "seen while testing on hostuser's machine",
    ],
)
def test_personal_paths_flags_planted_violation(cr, tmp_path, monkeypatch, line):
    monkeypatch.setenv("REPO_CHECK_WORDS", "hostuser")
    (tmp_path / "notes.md").write_text(f"Some text.\n{line}\nMore text.\n", encoding="utf-8")
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_personal_paths()

    assert problems, f"expected a hit for: {line!r}"


@pytest.mark.parametrize(
    "line",
    [
        "/home/me/.rv/Packages",
        "/home/example/.rv/Packages",
        "C:\\Users\\you\\project",
        "no personal data on this line at all",
    ],
)
def test_personal_paths_ignores_placeholder_usernames(cr, tmp_path, monkeypatch, line):
    (tmp_path / "notes.md").write_text(f"{line}\n", encoding="utf-8")
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    assert cr.check_personal_paths() == []


def test_personal_paths_skips_excluded_and_binary_files(cr, tmp_path, monkeypatch):
    (tmp_path / "image.png").write_bytes(b"not a real personal path but binary: /home/someone/x")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("/home/someone/.gitconfig\n", encoding="utf-8")
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    assert cr.check_personal_paths() == []


def test_private_words_come_from_ignored_file_and_env(cr, tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("REPO_CHECK_WORDS", raising=False)
    (tmp_path / "notes.md").write_text("built on projectx hardware\n", encoding="utf-8")
    assert cr.check_personal_paths() == []          # no words configured: nothing to match
    (tmp_path / ".private-words").write_text("# local only\nprojectx\n", encoding="utf-8")
    assert cr.check_personal_paths()                # file word found in notes.md
    (tmp_path / ".private-words").unlink()
    monkeypatch.setenv("REPO_CHECK_WORDS", "other, projectx")
    assert cr.check_personal_paths()                # env word found


def test_private_words_file_is_git_ignored():
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    assert ".private-words" in (root / ".gitignore").read_text(encoding="utf-8")
