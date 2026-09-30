#!/usr/bin/env python3
"""Build the files for a GitHub release of this repository.

Standard-library only, run from anywhere in a git checkout:

    python scripts/build_release.py [--tag vX.Y.Z] [--out dist]

Writes, into --out (default dist/ at the repo root, which is git-ignored):

- one zip per skill that .claude-plugin/plugin.json lists, named
  ``<skill>-<metadata.version>.zip`` and holding the skill folder itself
  (``rvls/SKILL.md``, ``rvls/scripts/...``), so it unzips straight into a skills
  folder such as ``~/.claude/skills/``. The files come from the HEAD commit, byte for
  byte (LF line endings, whatever the checkout uses), dated with the commit's time, so
  the same commit builds the same zips.
- ``release-notes.md``: the plugin version's section of CHANGELOG.md plus install and
  update commands, for the release body.

--tag checks that the tag names the plugin version in plugin.json (``v`` + version) and
fails otherwise, so a release cannot be cut from a tag the manifests disagree with. The
release workflow (.github/workflows/release.yml) runs this on every ``v*`` tag push.

Prints one JSON line; exit 0 on success, 1 on any error.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_repo  # noqa: E402  (parse_skill_frontmatter, shared with the repo checks)

NOTES_NAME = "release-notes.md"
FILE_MODE = 0o100644
DIR_MODE = 0o040755


class ReleaseError(Exception):
    pass


def _git_bytes(*args: str) -> bytes:
    try:
        result = subprocess.run(["git", *args], cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(f"git {' '.join(args)} failed: {exc}")
    if result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def _git(*args: str) -> str:
    return _git_bytes(*args).decode("utf-8", errors="replace")


def load_plugin() -> dict:
    path = REPO_ROOT / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read .claude-plugin/plugin.json: {exc}")


def load_marketplace_name() -> str:
    path = REPO_ROOT / ".claude-plugin" / "marketplace.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))["name"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise ReleaseError(f"cannot read the marketplace name from .claude-plugin/marketplace.json: {exc}")


def skill_folders(plugin: dict) -> list:
    skills = plugin.get("skills") or []
    if isinstance(skills, str):
        skills = [skills]
    folders = []
    for ref in skills:
        name = ref[2:] if ref.startswith("./") else ref
        name = name.rstrip("/")
        if not name or "/" in name or name in (".", ".."):
            raise ReleaseError(f"skill path {ref!r} in plugin.json is not a folder at the repo root")
        folders.append(name)
    if not folders:
        raise ReleaseError("plugin.json lists no skills")
    return folders


def skill_version(folder: str) -> str:
    skill_md = REPO_ROOT / folder / "SKILL.md"
    try:
        metadata = check_repo.parse_skill_frontmatter(skill_md.read_text(encoding="utf-8")).get("metadata")
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"cannot read {folder}/SKILL.md: {exc}")
    version = metadata.get("version") if isinstance(metadata, dict) else None
    if not version or not re.match(r"^\d+\.\d+\.\d+$", version):
        raise ReleaseError(f"{folder}/SKILL.md has no x.y.z metadata.version")
    return version


def build_skill_zip(folder: str, version: str, out_dir: Path, date_time: tuple) -> Path:
    listing = _git("ls-tree", "-r", "-z", "--name-only", "HEAD", "--", folder + "/")
    files = sorted(name for name in listing.split("\0") if name)
    if not files:
        raise ReleaseError(f"the HEAD commit has no files under {folder}/")
    dirs = set()
    for name in files:
        parts = name.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]) + "/")
    entries = sorted(dirs | set(files))

    zip_path = out_dir / f"{folder}-{version}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in entries:
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.create_system = 3  # Unix, so the modes below apply when unzipped
            if name.endswith("/"):
                info.external_attr = (DIR_MODE << 16) | 0x10
                zf.writestr(info, b"")
            else:
                info.external_attr = FILE_MODE << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, _git_bytes("cat-file", "blob", f"HEAD:{name}"))
    return zip_path


def changelog_section(changelog: str, version: str) -> str:
    """The body of the ``## [version] - date`` section, without its heading, up to the next
    ``## `` heading or the link references at the end."""
    heading = re.compile(r"^## \[" + re.escape(version) + r"\][^\n]*\n", re.MULTILINE)
    match = heading.search(changelog)
    if not match:
        raise ReleaseError(f"CHANGELOG.md has no '## [{version}]' section")
    rest = changelog[match.end():]
    end = re.search(r"^(## |\[[^\]]+\]: )", rest, re.MULTILINE)
    body = (rest[:end.start()] if end else rest).strip()
    if not body:
        raise ReleaseError(f"CHANGELOG.md's [{version}] section is empty")
    return body


def release_notes(plugin: dict, marketplace: str, version: str) -> str:
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    body = changelog_section(changelog, version)
    name = plugin["name"]
    repo_url = (plugin.get("repository") or plugin.get("homepage") or "").rstrip("/")
    owner_repo = re.sub(r"^https://github\.com/", "", repo_url)
    lines = [
        body,
        "",
        "## Install or update",
        "",
        "```",
        f"/plugin marketplace add {owner_repo}",
        f"/plugin install {name}@{marketplace}",
        "```",
        "",
        f"Already installed: run `claude plugin update {name}@{marketplace}` in your shell, or choose "
        f"**Update now** for `{name}` in the **Installed** tab of `/plugin`. Or unzip a single skill "
        "from the assets below into `~/.claude/skills/`.",
    ]
    if repo_url:
        lines += ["", f"Full history: [CHANGELOG.md]({repo_url}/blob/v{version}/CHANGELOG.md)."]
    return "\n".join(lines) + "\n"


def build(tag: str | None, out_dir: Path) -> dict:
    plugin = load_plugin()
    version = plugin.get("version")
    if not isinstance(version, str) or not re.match(r"^\d+\.\d+\.\d+$", version):
        raise ReleaseError(f"plugin.json version {version!r} is missing or not x.y.z")
    if tag is not None and tag != f"v{version}":
        raise ReleaseError(f"tag {tag!r} does not match plugin.json version {version!r} (expected 'v{version}')")
    marketplace = load_marketplace_name()

    commit_time = int(_git("log", "-1", "--format=%ct", "HEAD").strip())
    date_time = time.gmtime(max(commit_time, 315532800))[:6]  # zip dates start in 1980

    out_dir.mkdir(parents=True, exist_ok=True)
    zips = []
    for folder in skill_folders(plugin):
        zips.append(build_skill_zip(folder, skill_version(folder), out_dir, date_time))
    notes_path = out_dir / NOTES_NAME
    with open(notes_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(release_notes(plugin, marketplace, version))
    return {
        "ok": True,
        "version": version,
        "tag": f"v{version}",
        "zips": [str(p) for p in zips],
        "notes": str(notes_path),
    }


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Build per-skill zips and release notes for the plugin version in "
                    ".claude-plugin/plugin.json.",
        epilog="Example: build_release.py --tag v0.2.3 --out dist   Exit: 0 ok, 1 error.")
    ap.add_argument("--tag", help="release tag to check against plugin.json, e.g. v0.2.3")
    ap.add_argument("--out", default=str(REPO_ROOT / "dist"), help="output folder (default: dist/)")
    a = ap.parse_args(argv)
    try:
        result = build(a.tag, Path(a.out))
    except ReleaseError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
