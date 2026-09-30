#!/usr/bin/env python3
"""Fresh-install / release checklist for this repository.

Standard-library only, run from anywhere:

    python scripts/check_repo.py

Checks:

1. Every SKILL.md has the required frontmatter keys (name, description, license,
   compatibility, metadata.version), the name matches its folder, and the file is
   short enough that an agent reading it in full is cheap (under 500 lines, per
   CONTRIBUTING.md's style rule).
2. Every skill a plugin declares -- in its own .claude-plugin/plugin.json and in its
   .claude-plugin/marketplace.json entry -- points at a folder that exists and holds a
   SKILL.md (so a plugin install cannot point at a missing skill), the entry and the
   manifest agree on the plugin's name, and the two are not declared in a way Claude
   Code rejects as "conflicting manifests" (a "strict": false entry that also lists
   components next to a plugin.json).
3. No tracked, non-binary file contains a personal file path, this project's own
   development machine/user names, an email address, or a small list of words tied
   to the author's unrelated private projects that must never leak into this public
   repo. This is a lightweight net, not a guarantee -- it catches copy-paste
   mistakes, not determined secret-hiding.

Exit status is 0 when every check passes, 1 otherwise. Nothing here touches git,
the network, or RV/OpenRV.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Folders never worth scanning: version control internals, caches, and this
# script's own test, which necessarily quotes the patterns below. Used to filter the
# walk-based fallback in iter_repo_files() (the normal git-based listing skips these on its
# own, since they are untracked and .gitignore'd).
EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", "rv_frames"}
# Extra folders the fallback walk also skips: virtualenvs and other local/tooling caches that
# are .gitignore'd but would otherwise be walked and scanned (a stray .venv can contain
# thousands of files with the reviewer's own machine paths baked into them).
FALLBACK_EXTRA_EXCLUDE_DIRS = {".venv", "venv", ".tox", "node_modules", "dist", "build",
                               ".mypy_cache"}
EXCLUDE_FILES = {
    ".private-words",
    "scripts/check_repo.py",
    "tests/test_repo_checks.py",
}
GIT_LS_FILES_TIMEOUT = 15
# Extensions that are binary / not worth a text scan.
BINARY_EXTS = {
    ".png", ".gif", ".jpg", ".jpeg", ".mov", ".mp4", ".exr", ".dpx", ".tif", ".tiff",
    ".ico", ".pyc", ".rvpkg", ".zip",
}

REQUIRED_SKILL_KEYS = ("name", "description", "license", "compatibility")
MAX_SKILL_MD_LINES = 500

# Personal-path shapes: a Windows user profile, a Unix/macOS home directory, and
# any dev-drive junction/mount pattern that would only make sense on the author's
# own machine.
# Usernames that are clearly placeholders, not a real person's account, so
# "/home/example/..." in a doc or test fixture is not flagged.
PLACEHOLDER_USERNAMES = {"me", "example", "user", "you", "yourname"}

PERSONAL_PATH_PATTERNS = [
    re.compile(r"[A-Za-z]:\\Users\\([^\\\s\"']+)", re.IGNORECASE),
    re.compile(r"/home/([^/\s\"']+)"),
    re.compile(r"/Users/([^/\s\"']+)"),
]
# A real email's final label (the TLD) is letters only (.com, .dev, .io, ...); this
# keeps version-ish strings like "package@8.9" or "graphics@8.9" from matching.
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")

# Private words (your user name, handles, private project names) must never be
# committed, so the list itself is never committed either. It is read at run time from
# PRIVATE_WORDS_FILE at the repo root (git-ignored; one word per line, "#" comments)
# and from the REPO_CHECK_WORDS environment variable (comma-separated). Matched
# case-insensitively as whole words. With neither set, only the path and email checks run.
PRIVATE_WORDS_FILE = ".private-words"


def private_words() -> list:
    words = []
    path = REPO_ROOT / PRIVATE_WORDS_FILE
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line)
    words += [w.strip() for w in os.environ.get("REPO_CHECK_WORDS", "").split(",") if w.strip()]
    return words


def private_word_pattern():
    words = private_words()
    if not words:
        return None
    return re.compile(
        r"(?<![A-Za-z0-9])(" + "|".join(re.escape(w) for w in words) + r")(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


class Problem:
    def __init__(self, check: str, path: str, detail: str) -> None:
        self.check = check
        self.path = path
        self.detail = detail

    def __str__(self) -> str:
        return f"[{self.check}] {self.path}: {self.detail}"


def _git_repo_files(root: Path) -> list | None:
    """Files git would keep for this repo -- tracked files plus untracked files that are not
    .gitignore'd -- as paths relative to `root`, or None when git is missing or `root` is not
    a git work tree. Getting this list from git (rather than walking the whole tree) means an
    ignored folder such as a local .venv is never even considered, whatever it contains.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=GIT_LS_FILES_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    names = result.stdout.decode("utf-8", errors="replace").split("\0")
    return [Path(name) for name in names if name]


def _walk_repo_files(root: Path):
    exclude_dirs = EXCLUDE_DIRS | FALLBACK_EXTRA_EXCLUDE_DIRS
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in exclude_dirs for part in rel.parts):
            continue
        yield rel


def iter_repo_files():
    tracked = _git_repo_files(REPO_ROOT)
    if tracked is not None:
        rels = sorted(tracked, key=lambda p: p.as_posix())
    else:
        rels = _walk_repo_files(REPO_ROOT)
    for rel in rels:
        if rel.as_posix() in EXCLUDE_FILES:
            continue
        if not (REPO_ROOT / rel).is_file():
            continue  # e.g. a name git reported that was deleted/renamed since
        yield rel


def parse_skill_frontmatter(text: str) -> dict:
    """A deliberately small YAML-frontmatter reader for this project's SKILL.md
    files: flat ``key: value`` lines plus one level of nesting under
    ``metadata:``. Good enough for these files; not a general YAML parser.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing opening '---' frontmatter fence")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise ValueError("missing closing '---' frontmatter fence")

    result: dict = {}
    current_nested_key = None
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        if raw.startswith((" ", "\t")):
            if current_nested_key is None:
                continue
            key, _, value = raw.strip().partition(":")
            result.setdefault(current_nested_key, {})[key.strip()] = value.strip().strip('"')
            continue
        key, sep, value = raw.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if value == "":
            current_nested_key = key
            result.setdefault(key, {})
        else:
            current_nested_key = None
            result[key] = value.strip('"')
    return result


def check_skill_frontmatter() -> list[Problem]:
    problems: list[Problem] = []
    for skill_md in sorted(REPO_ROOT.glob("*/SKILL.md")):
        rel = skill_md.relative_to(REPO_ROOT).as_posix()
        folder_name = skill_md.parent.name
        text = skill_md.read_text(encoding="utf-8")

        line_count = len(text.splitlines())
        if line_count > MAX_SKILL_MD_LINES:
            problems.append(
                Problem("skill-frontmatter", rel, f"{line_count} lines, over the {MAX_SKILL_MD_LINES}-line style limit")
            )

        try:
            fm = parse_skill_frontmatter(text)
        except ValueError as exc:
            problems.append(Problem("skill-frontmatter", rel, f"could not read frontmatter: {exc}"))
            continue

        for key in REQUIRED_SKILL_KEYS:
            if not fm.get(key):
                problems.append(Problem("skill-frontmatter", rel, f"missing or empty '{key}:'"))

        name = fm.get("name")
        if name and name != folder_name:
            problems.append(
                Problem("skill-frontmatter", rel, f"name '{name}' does not match its folder '{folder_name}'")
            )

        metadata = fm.get("metadata")
        version = metadata.get("version") if isinstance(metadata, dict) else None
        if not version:
            problems.append(Problem("skill-frontmatter", rel, "missing 'metadata: version:'"))
        elif not re.match(r"^\d+\.\d+\.\d+$", version):
            problems.append(Problem("skill-frontmatter", rel, f"metadata.version '{version}' is not semantic (x.y.z)"))

    if not list(REPO_ROOT.glob("*/SKILL.md")):
        problems.append(Problem("skill-frontmatter", ".", "no SKILL.md files found at all -- check REPO_ROOT"))

    return problems


# Keys that declare components; a "strict": false marketplace entry may not set any of them
# when the plugin also has a plugin.json.
COMPONENT_KEYS = ("commands", "agents", "skills", "hooks", "outputStyles", "themes")


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def check_marketplace_skills_paths() -> list[Problem]:
    problems: list[Problem] = []
    manifest_path = REPO_ROOT / ".claude-plugin" / "marketplace.json"
    rel = manifest_path.relative_to(REPO_ROOT).as_posix()
    if not manifest_path.exists():
        return [Problem("marketplace-skills", rel, "file does not exist")]

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [Problem("marketplace-skills", rel, f"invalid JSON: {exc}")]

    for plugin in manifest.get("plugins", []):
        plugin_name = plugin.get("name", "<unnamed plugin>")
        plugin_source = plugin.get("source", "./")
        if not isinstance(plugin_source, str):
            continue  # a github / url / npm source: its files are not in this repo
        plugin_root = REPO_ROOT / plugin_source
        entry_skills = _as_list(plugin.get("skills"))

        plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
        plugin_json_rel = os.path.relpath(str(plugin_json), str(REPO_ROOT)).replace(os.sep, "/")
        skill_sources = [(rel, entry_skills)]
        if plugin_json.is_file():
            try:
                plugin_manifest = json.loads(plugin_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                problems.append(Problem("marketplace-skills", plugin_json_rel, f"invalid JSON: {exc}"))
                continue
            manifest_name = plugin_manifest.get("name")
            if manifest_name != plugin.get("name"):
                problems.append(
                    Problem("marketplace-skills", plugin_json_rel,
                            f"name '{manifest_name}' does not match the marketplace entry '{plugin_name}'")
                )
            conflicting = [key for key in COMPONENT_KEYS if key in plugin]
            if plugin.get("strict") is False and conflicting:
                problems.append(
                    Problem("marketplace-skills", rel,
                            f"plugin '{plugin_name}' is \"strict\": false and lists {', '.join(conflicting)} "
                            f"next to {plugin_json_rel}; Claude Code refuses to load it (conflicting manifests). "
                            "Declare components in plugin.json only.")
                )
            skill_sources.insert(0, (plugin_json_rel, _as_list(plugin_manifest.get("skills"))))

        if not any(skills for _, skills in skill_sources) and not (plugin_root / "skills").is_dir():
            problems.append(Problem("marketplace-skills", rel, f"plugin '{plugin_name}' lists no skills"))

        for source_rel, skills in skill_sources:
            problems.extend(_check_skill_refs(source_rel, plugin_name, plugin_root, skills))

    return problems


def _check_skill_refs(rel: str, plugin_name: str, plugin_root: Path, skills: list) -> list[Problem]:
    problems: list[Problem] = []
    for skill_ref in skills:
        skill_dir = (plugin_root / skill_ref).resolve()
        try:
            skill_dir.relative_to(REPO_ROOT.resolve())
        except ValueError:
            problems.append(
                Problem("marketplace-skills", rel, f"plugin '{plugin_name}' skill path '{skill_ref}' escapes the repo")
            )
            continue
        if not skill_dir.is_dir():
            problems.append(
                Problem("marketplace-skills", rel, f"plugin '{plugin_name}' skill path '{skill_ref}' does not exist")
            )
            continue
        if not (skill_dir / "SKILL.md").is_file():
            problems.append(
                Problem("marketplace-skills", rel, f"plugin '{plugin_name}' skill path '{skill_ref}' has no SKILL.md")
            )
    return problems


def check_personal_paths() -> list[Problem]:
    word_pattern = private_word_pattern()
    problems: list[Problem] = []
    for rel in iter_repo_files():
        if rel.suffix.lower() in BINARY_EXTS:
            continue
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # not a text file we can usefully scan

        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in PERSONAL_PATH_PATTERNS:
                m = pattern.search(line)
                if m and m.group(1).lower() not in PLACEHOLDER_USERNAMES:
                    problems.append(
                        Problem("personal-path", f"{rel.as_posix()}:{lineno}", f"looks like a personal path: {m.group(0)!r}")
                    )
            m = EMAIL_PATTERN.search(line)
            if m:
                problems.append(
                    Problem("personal-path", f"{rel.as_posix()}:{lineno}", f"looks like an email address: {m.group(0)!r}")
                )
            m = word_pattern.search(line) if word_pattern else None
            if m:
                problems.append(
                    Problem("personal-path", f"{rel.as_posix()}:{lineno}", f"private word '{m.group(0)}'")
                )

    return problems


CHECKS = {
    "skill-frontmatter": check_skill_frontmatter,
    "marketplace-skills": check_marketplace_skills_paths,
    "personal-paths": check_personal_paths,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    selected = argv or list(CHECKS)
    unknown = [name for name in selected if name not in CHECKS]
    if unknown:
        print(f"unknown check(s): {', '.join(unknown)}; available: {', '.join(CHECKS)}", file=sys.stderr)
        return 2

    all_problems: list[Problem] = []
    for name in selected:
        problems = CHECKS[name]()
        all_problems.extend(problems)

    if all_problems:
        for problem in all_problems:
            print(str(problem))
        print(f"\n{len(all_problems)} problem(s) found.")
        return 1

    print(f"OK: {', '.join(selected)} -- no problems found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
