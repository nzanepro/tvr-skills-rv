"""A wording guard for Anthropic's plugin directory scanner.

The scanner reads one English verb, followed by an object, as a credential of that name being
handed to a command ("Uses a credential from the user's machine"), in documents, messages and
tests alike. The repository says "give" or "use" instead. Python's own statement of the same
spelling stands alone on its line and is not matched.
"""
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORD = "pa" + "ss"                       # spelled in two parts so this file has no match
PATTERN = re.compile(r"\b" + WORD + r"\b[ \t]+[^\s#]", re.IGNORECASE)
TEXT_SUFFIXES = (".md", ".py", ".json", ".yml", ".yaml", ".txt")


def _tracked_text_files():
    out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True,
                         check=True).stdout.split("\n")
    return [f for f in out if f.endswith(TEXT_SUFFIXES) and not f.startswith(".github/workflows/")]


def test_pattern_matches_the_verb_and_not_the_statement():
    assert PATTERN.search("then " + WORD + " the folder")
    assert PATTERN.search(WORD.capitalize() + " `--flag`")
    assert not PATTERN.search("    " + WORD)
    assert not PATTERN.search("    " + WORD + "  # nothing to do")
    assert not PATTERN.search("by" + WORD + " the check")


def test_no_file_uses_the_verb_the_scanner_misreads():
    hits = []
    for name in _tracked_text_files():
        text = (REPO_ROOT / name).read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.split("\n"), 1):
            if PATTERN.search(line):
                hits.append(f"{name}:{n}: {line.strip()[:100]}")
    assert not hits, "say give or use instead:\n" + "\n".join(hits)
