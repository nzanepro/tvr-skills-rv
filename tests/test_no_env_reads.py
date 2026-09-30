"""Nothing in the repository outside .github/workflows reads the process's variables or holds a
shell-variable token.

Anthropic's plugin directory holds a plugin for review ("Uses a credential from the user's
machine") when any of its files, tests and dev scripts included, reads the installer's
variables while the same file (or another surface of the plugin) spells a remote host. The
scripts find programs from flags, a config file, PATH and the usual install folders instead,
and child processes inherit what they need without the repository naming it.

This guard is independent of scripts/check_repo.py's no-env-reads check (which does the same
from the command line), and its needles are built from pieces so this file never spells one.
"""
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BINARY_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".webp", ".ico", ".pyc", ".zip"}

MAPPING = "env" + "iron"
ENV_READS = re.compile("|".join([MAPPING, "get" + "env", "expand" + "vars",
                                 re.escape("process." + "env"),
                                 "(?<![A-Za-z0-9/])" + "env" + ":[A-Za-z_]"]), re.IGNORECASE)
# a dollar sign before a name, a brace or a parenthesis, or a name between percent signs;
# Claude Code's own plugin-option substitution (dollar, brace, user_config.) is allowed
SHELL_VARIABLE = re.compile("[$](?:[A-Za-z_(]|[{](?!user_config[.]))|%[A-Za-z_][A-Za-z0-9_]*%")


def _tracked_text_files():
    names = subprocess.run(["git", "ls-files", "-z"], cwd=str(REPO_ROOT), capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=30).stdout.decode().split("\0")
    if not any(names):              # not a git checkout (an unpacked release): walk instead
        names = [p.relative_to(REPO_ROOT).as_posix() for p in REPO_ROOT.rglob("*")
                 if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts]
    for name in sorted(n for n in names if n):
        if name.startswith(".github/workflows/") or name == ".private-words":
            continue
        path = REPO_ROOT / name
        if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
            continue
        try:
            yield name, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def _offenders(pattern):
    return [f"{name}:{number}: {line.strip()}"
            for name, text in _tracked_text_files()
            for number, line in enumerate(text.splitlines(), 1) if pattern.search(line)]


def test_no_env_reads_in_any_repo_file():
    assert not _offenders(ENV_READS)


def test_no_shell_variables_in_any_repo_file():
    assert not _offenders(SHELL_VARIABLE)


def test_the_guard_patterns_catch_each_form():
    dollar, percent = "$", "%"
    for sample in ("os." + MAPPING + "['X']", "os.get" + "env('X')", "from os import " + MAPPING,
                   "expand" + "vars('x')", "process." + "env.X", "Set-Item E" + "nv:X 1"):
        assert ENV_READS.search(sample), sample
    for sample in (dollar + "NAME", dollar + "{NAME}", dollar + "(date)",
                   dollar + "e" + "nv:NAME", percent + "NAME" + percent):
        assert SHELL_VARIABLE.search("cd " + sample + "/x"), sample
    for sample in ("costs " + dollar + "5M", "50" + percent + " of", "a " + dollar + " b",
                   "[" + dollar + "{user_config.rv_bin}]"):
        assert not SHELL_VARIABLE.search(sample), sample
    for sample in ("#!/usr/bin/" + "env python3", "envelope(body)", "/usr/bin/" + "env RV=none"):
        assert not ENV_READS.search(sample), sample


def test_the_user_config_substitution_is_used_only_for_the_rv_bin_option():
    """The one allowed brace token must be the rv_bin option the manifest declares."""
    token = re.compile(re.escape("$" + "{user_config.") + r"([A-Za-z0-9_]+)\}")
    used = {m.group(1) for _, text in _tracked_text_files() for m in token.finditer(text)}
    assert used <= {"rv_bin"}, used
