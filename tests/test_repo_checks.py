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


def _write_plugin_repo(tmp_path, entry, plugin_json=None, skill_dirs=("example-skill",)):
    """A synthetic marketplace repo with one relative-path plugin at the root."""
    (tmp_path / ".claude-plugin").mkdir()
    for name in skill_dirs:
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    manifest = {"name": "example-marketplace", "plugins": [entry]}
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(json.dumps(manifest), encoding="utf-8")
    if plugin_json is not None:
        (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps(plugin_json), encoding="utf-8")


def test_marketplace_skills_paths_reads_skills_from_plugin_json(cr, tmp_path, monkeypatch):
    _write_plugin_repo(
        tmp_path,
        {"name": "example", "source": "./"},
        {"name": "example", "skills": ["./example-skill"]},
    )
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    assert cr.check_marketplace_skills_paths() == []


def test_marketplace_skills_paths_flags_missing_skill_in_plugin_json(cr, tmp_path, monkeypatch):
    _write_plugin_repo(
        tmp_path,
        {"name": "example", "source": "./"},
        {"name": "example", "skills": ["./example-skill", "./gone"]},
    )
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_marketplace_skills_paths()

    assert [p.path for p in problems] == [".claude-plugin/plugin.json"]
    assert "'./gone' does not exist" in problems[0].detail


def test_marketplace_skills_paths_flags_conflicting_manifests(cr, tmp_path, monkeypatch):
    """A plugin.json next to a "strict": false entry that also lists skills does not load in
    Claude Code ("Plugin <name> has conflicting manifests")."""
    _write_plugin_repo(
        tmp_path,
        {"name": "example", "source": "./", "strict": False, "skills": ["./example-skill"]},
        {"name": "example", "skills": ["./example-skill"]},
    )
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_marketplace_skills_paths()

    assert any("conflicting manifests" in p.detail for p in problems)


def test_marketplace_skills_paths_flags_plugin_name_mismatch(cr, tmp_path, monkeypatch):
    _write_plugin_repo(
        tmp_path,
        {"name": "example", "source": "./"},
        {"name": "other-name", "skills": ["./example-skill"]},
    )
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_marketplace_skills_paths()

    assert any("does not match the marketplace entry" in p.detail for p in problems)


def test_real_plugin_keeps_its_names_and_skills():
    """Users install `rv@tvr-skills-rv` and run `/rv:<skill>`, and release zips and tests use
    the skill folders at the repository root, so none of these may change by accident."""
    marketplace = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert marketplace["name"] == "tvr-skills-rv"
    assert [entry["name"] for entry in marketplace["plugins"]] == ["rv"]
    entry = marketplace["plugins"][0]
    assert entry["source"] == "./"
    assert plugin["name"] == "rv"
    assert plugin["skills"] == ["./rv-review", "./rvio", "./rvls", "./rvpkg"]
    # plugin.json is the manifest: the entry declares no components and no second version.
    assert not any(key in entry for key in ("skills", "commands", "agents", "hooks", "strict", "version"))


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


# --- iter_repo_files(): git-based listing, with a walk fallback -------------
#
# iter_repo_files() used to always walk REPO_ROOT.rglob("*"), which meant a local, .gitignore'd
# folder such as .venv got scanned like any tracked file -- a single virtualenv can contain
# thousands of files carrying the machine's own home directory path. It now lists files from
# `git ls-files` (tracked + untracked-not-ignored) when git is available, and only falls back
# to walking the tree when it is not.


def test_iter_repo_files_fallback_walk_skips_venv_and_friends(cr, tmp_path, monkeypatch):
    """With git unavailable (_git_repo_files -> None), the walk fallback must still skip a
    local .venv (and the other tooling folders it never makes sense to scan)."""
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cr, "_git_repo_files", lambda root: None)
    (tmp_path / "notes.md").write_text("nothing personal here\n", encoding="utf-8")
    for folder in (".venv", "venv", "node_modules", ".tox"):
        site = tmp_path / folder / "sub"
        site.mkdir(parents=True)
        (site / "bad.pth").write_text("C:\\Users\\someone\\dev\n", encoding="utf-8")

    rels = {p.as_posix() for p in cr.iter_repo_files()}

    assert "notes.md" in rels
    assert not any(folder in r for r in rels for folder in (".venv", "venv", "node_modules", ".tox"))
    assert cr.check_personal_paths() == []


def test_iter_repo_files_uses_git_listing_when_available(cr, tmp_path, monkeypatch):
    """When the git-based listing succeeds, iter_repo_files uses exactly what it returns
    instead of walking the tree -- so a file git does not list (here standing in for a
    .gitignore'd, untracked file) is never scanned even though it sits right there on disk
    with a personal path inside it."""
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)
    (tmp_path / "tracked.md").write_text("clean\n", encoding="utf-8")
    ignored_dir = tmp_path / ".venv"
    ignored_dir.mkdir()
    (ignored_dir / "secret.txt").write_text("C:\\Users\\someone\\dev\n", encoding="utf-8")
    monkeypatch.setattr(cr, "_git_repo_files", lambda root: [Path("tracked.md")])

    rels = {p.as_posix() for p in cr.iter_repo_files()}

    assert rels == {"tracked.md"}
    assert cr.check_personal_paths() == []


def test_iter_repo_files_skips_git_entries_that_no_longer_exist(cr, tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)
    (tmp_path / "here.md").write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr(cr, "_git_repo_files", lambda root: [Path("here.md"), Path("gone.md")])

    assert {p.as_posix() for p in cr.iter_repo_files()} == {"here.md"}


def test_git_repo_files_returns_none_outside_a_git_work_tree(cr, tmp_path):
    """A plain tmp_path (not under any .git) is not a git work tree, so the git-based listing
    must report None -- the signal iter_repo_files() uses to fall back to walking."""
    assert cr._git_repo_files(tmp_path) is None


def test_git_repo_files_returns_none_when_git_is_missing(cr, tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(cr.subprocess, "run", fake_run)

    assert cr._git_repo_files(tmp_path) is None


def test_git_repo_files_parses_null_separated_output(cr, tmp_path, monkeypatch):
    """_git_repo_files() is exercised with a mocked subprocess.run (rather than a real git
    repo) so it is deterministic and does not depend on git being installed; it checks the
    command it runs and that it decodes the NUL-separated tracked + untracked-not-ignored
    output `git ls-files -z --cached --others --exclude-standard` produces."""
    calls = {}

    class FakeCompleted:
        returncode = 0
        stdout = b"tracked.md\x00sub/dir/other.py\x00"

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return FakeCompleted()

    monkeypatch.setattr(cr.subprocess, "run", fake_run)

    result = cr._git_repo_files(tmp_path)

    assert result == [Path("tracked.md"), Path("sub/dir/other.py")]
    assert calls["cmd"] == ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    assert calls["kwargs"]["cwd"] == str(tmp_path)
    assert calls["kwargs"]["stdin"] == cr.subprocess.DEVNULL
    assert calls["kwargs"]["timeout"] == cr.GIT_LS_FILES_TIMEOUT


def test_git_repo_files_returns_none_on_nonzero_exit(cr, tmp_path, monkeypatch):
    class FakeCompleted:
        returncode = 128
        stdout = b""

    monkeypatch.setattr(cr.subprocess, "run", lambda cmd, **kwargs: FakeCompleted())

    assert cr._git_repo_files(tmp_path) is None
