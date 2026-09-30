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


def test_readme_listing_clean_on_real_repo(cr):
    problems = cr.check_readme_listing()
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


@pytest.mark.parametrize("key,limit", [("description", 1024), ("compatibility", 500)])
def test_skill_frontmatter_flags_fields_over_the_spec_length(cr, tmp_path, monkeypatch, key, limit):
    skill_dir = tmp_path / "example-skill"
    skill_dir.mkdir()
    fields = {"description": "a test skill", "compatibility": "test only"}
    fields[key] = "x" * (limit + 1)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: example-skill\n"
        f"description: {fields['description']}\n"
        "license: MIT\n"
        f"compatibility: {fields['compatibility']}\n"
        "metadata:\n"
        "  version: 0.1.0\n"
        "---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_skill_frontmatter()

    assert any(f"'{key}' is {limit + 1} characters" in p.detail for p in problems)


# --- frontmatter must be YAML a strict parser accepts ------------------------
#
# parse_skill_frontmatter() is deliberately lenient, so an unquoted value containing ": "
# (a YAML error: "mapping values are not allowed here") used to pass every check here while a
# stricter loader, such as Anthropic's plugin directory, would refuse the skill.

GOOD_FRONTMATTER_LINES = [
    "description: Lists sequences, finds missing frames and reads headers.",
    'description: "Converts media: sequences to movies and back."',
    "description: 'It''s quoted: fine.'",
    "description: A value with a colon:inside a word and a C# or issue#12 reference.",
    "description: >\n  Folded text: a colon here is fine,\n  because it is a block scalar.",
]
BAD_FRONTMATTER_LINES = [
    "description: Converts media: sequences to movies and back.",
    "description: Ends with a colon:",
    'description: "Unterminated quote',
    'description: "Quoted" and then more text',
    "description: 'It's not doubled'",
    "description: [a, list]",
    "description: *anchor-like",
    "description: A value # with a comment",
]


def _frontmatter(line):
    return (
        "---\n"
        "name: example-skill\n"
        f"{line}\n"
        "license: MIT\n"
        "compatibility: test only\n"
        "metadata:\n"
        "  version: 0.1.0\n"
        "---\n"
        "# Body\n"
    )


@pytest.mark.parametrize("line", GOOD_FRONTMATTER_LINES)
def test_frontmatter_yaml_accepts_valid_values(cr, line):
    assert cr.frontmatter_yaml_problems(_frontmatter(line)) == []


@pytest.mark.parametrize("line", BAD_FRONTMATTER_LINES)
def test_frontmatter_yaml_flags_values_strict_yaml_rejects(cr, line):
    assert cr.frontmatter_yaml_problems(_frontmatter(line))


def test_skill_frontmatter_flags_unquoted_colon_space(cr, tmp_path, monkeypatch):
    skill_dir = tmp_path / "example-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        _frontmatter("description: Converts media: sequences to movies."), encoding="utf-8"
    )
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_skill_frontmatter()

    assert any("not valid YAML" in p.detail and "': '" in p.detail for p in problems)


def _pyyaml_description(line):
    """The description PyYAML reads from _frontmatter(line), or None when it fails to parse."""
    yaml = pytest.importorskip("yaml")
    try:
        return yaml.safe_load(_frontmatter(line).split("---\n")[1]).get("description")
    except yaml.YAMLError:
        return None


@pytest.mark.parametrize("line", GOOD_FRONTMATTER_LINES)
def test_good_frontmatter_samples_are_valid_for_pyyaml(line):
    assert isinstance(_pyyaml_description(line), str)


@pytest.mark.parametrize("line", BAD_FRONTMATTER_LINES)
def test_bad_frontmatter_samples_fail_or_change_meaning_in_pyyaml(line):
    """Keeps the samples honest: each "bad" line is a PyYAML error, is not a string, or (for
    ' #') loads as something shorter than what the line shows."""
    description = _pyyaml_description(line)
    shown = line.split(": ", 1)[1]
    assert not isinstance(description, str) or description != shown


def _real_skill_frontmatters():
    for skill_md in sorted(REPO_ROOT.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        yield skill_md.parent.name, text.split("---\n")[1]


@pytest.mark.parametrize("folder,frontmatter", list(_real_skill_frontmatters()))
def test_real_skill_frontmatter_parses_with_pyyaml(folder, frontmatter):
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(frontmatter)
    assert data["name"] == folder
    assert isinstance(data["description"], str) and data["description"]
    assert isinstance(data["metadata"]["version"], str)


@pytest.mark.parametrize("folder,frontmatter", list(_real_skill_frontmatters()))
def test_real_skill_frontmatter_parses_with_strictyaml(folder, frontmatter):
    strictyaml = pytest.importorskip("strictyaml")
    data = strictyaml.dirty_load(frontmatter, allow_flow_style=False).data
    assert data["name"] == folder
    assert data["description"]


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


def _write_versioned_repo(tmp_path, plugin_version="1.2.3", top_level="1.2.3", entry_version=None,
                          changelog_version="1.2.3", link=True, skill_version="1.0.0"):
    entry = {"name": "example", "source": "./"}
    if entry_version is not None:
        entry["version"] = entry_version
    _write_plugin_repo(tmp_path, entry, {"name": "example", "version": plugin_version,
                                         "skills": ["./example-skill"]})
    marketplace_path = tmp_path / ".claude-plugin" / "marketplace.json"
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    marketplace["version"] = top_level
    marketplace_path.write_text(json.dumps(marketplace), encoding="utf-8")
    (tmp_path / "example-skill" / "SKILL.md").write_text(
        f"---\nname: example-skill\nmetadata:\n  version: {skill_version}\n---\n", encoding="utf-8"
    )
    changelog = (
        "# Changelog\n\n## [Unreleased]\n\n"
        f"## [{changelog_version}] - 2026-01-02\n\n- A change.\n\n"
        "## [1.0.0] - 2026-01-01\n\n- First.\n\n"
    )
    if link:
        changelog += f"[{changelog_version}]: https://example.invalid/releases/tag/v{changelog_version}\n"
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")


def test_versions_pass_when_everything_agrees(cr, tmp_path, monkeypatch):
    _write_versioned_repo(tmp_path)
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    assert cr.check_versions() == []


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"top_level": "1.2.2"}, "top-level version '1.2.2'"),
        ({"entry_version": "1.2.3"}, "entry sets a version"),
        ({"changelog_version": "1.2.2"}, "newest release heading is '1.2.2'"),
        ({"link": False}, "no '[1.2.3]: <url>' link reference"),
        ({"skill_version": "1.3.0"}, "ahead of the plugin version"),
        ({"plugin_version": "1.2"}, "not x.y.z"),
    ],
)
def test_versions_flag_each_disagreement(cr, tmp_path, monkeypatch, kwargs, expected):
    _write_versioned_repo(tmp_path, **kwargs)
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_versions()

    assert any(expected in p.detail for p in problems), [str(p) for p in problems]


def test_versions_clean_on_real_repo(cr):
    problems = cr.check_versions()
    assert problems == [], "\n".join(str(p) for p in problems)


def test_real_plugin_keeps_its_names_and_skills():
    """Users install `rv-tools@tvr-skills-rv` and run `/rv-tools:<skill>` (0.3.0 renamed the
    plugin from `rv`, which the directory held as too close to another listing's name), and
    release zips and tests use the skill folders at the repository root, so none of these may
    change by accident."""
    marketplace = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert marketplace["name"] == "tvr-skills-rv"
    assert [entry["name"] for entry in marketplace["plugins"]] == ["rv-tools"]
    entry = marketplace["plugins"][0]
    assert entry["source"] == "./"
    assert plugin["name"] == "rv-tools"
    # The display name the directory shows says what the plugin is.
    assert plugin["displayName"] == "RV and OpenRV Media Review"
    assert plugin["skills"] == ["./rv-review", "./rvio", "./rvls", "./rvpkg"]
    # One description, shown both before install (entry) and after (plugin.json).
    assert entry["description"] == plugin["description"]
    # plugin.json is the manifest: the entry declares no components and no second version.
    assert not any(key in entry for key in ("skills", "commands", "agents", "hooks", "strict", "version"))


# Shell-variable samples are built from pieces, so this file never spells one itself (the
# no-env-reads check scans it too).
DOLLAR, PERCENT = "$", "%"
SHELL_SAMPLES = [
    'ln -s "' + DOLLAR + 'SRC_DIR/rv-review" ~/.claude/skills/rv-review',
    'cmd /c mklink /J "' + DOLLAR + 'e' + 'nv:BASE_DIR\\skills\\rv-review" x',
    "cp -r " + DOLLAR + "{BASE_DIR}/x ~/.claude/skills/",
    "echo " + DOLLAR + "(date)",
    "copy x " + PERCENT + "BASE_DIR" + PERCENT + "\\skills",
]


@pytest.mark.parametrize("line", SHELL_SAMPLES)
def test_readme_listing_flags_shell_variables(cr, tmp_path, monkeypatch, line):
    (tmp_path / "README.md").write_text(f"Install:\n\n```\n{line}\n```\n", encoding="utf-8")
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_readme_listing()

    assert [p.path for p in problems] == ["README.md:4"], problems


@pytest.mark.parametrize(
    "line",
    [
        "git clone ../tvr-skills-rv ~/tvr-skills-rv",
        "ln -s ~/tvr-skills-rv/rv-review ~/.claude/skills/rv-review",
        "cmd /c mklink /J .claude\\skills\\rv-review tvr-skills-rv\\rv-review",
        "It costs " + DOLLAR + "5, or 100" + PERCENT + " of nothing.",
        "rv_bin: [" + DOLLAR + "{user_config.rv_bin}]",
    ],
)
def test_readme_listing_accepts_literal_paths(cr, tmp_path, monkeypatch, line):
    (tmp_path / "README.md").write_text(f"{line}\n", encoding="utf-8")
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    assert cr.check_readme_listing() == []


def test_readme_listing_flags_image_paths_in_code(cr, tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "demo.png").write_bytes(b"\x89PNG")
    (tmp_path / "README.md").write_text(
        "![demo](docs/demo.png) and [the demo](docs/demo.png) are fine.\n"
        "The demo is `docs/demo.png`.\n"
        "```\nopen docs/demo.png\n```\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)

    problems = cr.check_readme_listing()

    assert [p.path for p in problems] == ["README.md:2", "README.md:4"], problems


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
    (tmp_path / ".private-words").write_text("hostuser\n", encoding="utf-8")
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


def test_private_words_come_only_from_the_ignored_file(cr, tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)
    (tmp_path / "notes.md").write_text("built on projectx hardware\n", encoding="utf-8")
    assert cr.check_personal_paths() == []          # no words configured: nothing to match
    (tmp_path / ".private-words").write_text("# local only\nprojectx\n", encoding="utf-8")
    assert cr.check_personal_paths()                # file word found in notes.md


# --- no-env-reads: nothing reads the process's variables or holds a shell variable ------
#
# Needles are built from pieces so this file never spells one.

MAPPING = "env" + "iron"
ENV_READ_SAMPLES = [
    "import os; os." + MAPPING + ".get('X')",
    "value = os.get" + "env('X')",
    "from os import " + MAPPING,
    "os.path.expand" + "vars('~/x')",
    "const x = process." + "env.X;",
    "Set-Item E" + "nv:X none",
    "x = " + MAPPING + "['X']",
    "import sys, " + MAPPING,
]


@pytest.mark.parametrize("line", ENV_READ_SAMPLES + SHELL_SAMPLES)
def test_no_env_reads_flags_each_form(cr, tmp_path, monkeypatch, line):
    (tmp_path / "tool.py").write_text("ok\n" + line + "\n", encoding="utf-8")
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cr, "_git_repo_files", lambda root: None)

    problems = cr.check_no_env_reads()

    assert [p.path for p in problems][:1] == ["tool.py:2"], problems


@pytest.mark.parametrize("line", [
    "#!/usr/bin/" + "env python3",
    "subprocess.run(['/usr/bin/" + "env', 'RVPUSH_RV_EXECUTABLE_PATH=none', 'rvpush'])",
    "Plugin setting: [" + DOLLAR + "{user_config.rv_bin}]",
    "costs " + DOLLAR + "5, 50" + PERCENT + " off",
    "envelope(body)",
    "We are committed to fostering an " + MAPPING + "ment that respects",
    "which " + MAPPING + "ment variables they read (only to find programs).",
    "## " + MAPPING.capitalize() + "ment variables",
])
def test_no_env_reads_accepts_the_allowed_forms(cr, tmp_path, monkeypatch, line):
    (tmp_path / "tool.py").write_text(line + "\n", encoding="utf-8")
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cr, "_git_repo_files", lambda root: None)

    assert cr.check_no_env_reads() == []


def test_no_env_reads_skips_the_workflows_folder(cr, tmp_path, monkeypatch):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "tests.yml").write_text("run: echo " + DOLLAR + "{{ matrix.os }}\n", encoding="utf-8")
    monkeypatch.setattr(cr, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cr, "_git_repo_files", lambda root: None)

    assert cr.check_no_env_reads() == []


def test_no_env_reads_clean_on_real_repo(cr):
    problems = cr.check_no_env_reads()
    assert problems == [], "\n".join(str(p) for p in problems)


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
