"""The plugin's one user option, rv_bin, and how the skills use it.

Claude Code substitutes a non-sensitive option into skill text when it is set. When it is
not set (or the skill is installed on its own, outside the plugin) the reference stays as
written, placeholder and all, so every skill tells the agent to give it as --rv-bin only when
it reads as a path. Checked by hand with Claude Code 2.1.284: set, the text between the
brackets was the chosen folder; unset, it was the placeholder itself.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS = ("rv-review", "rvio", "rvls", "rvpkg")
REFERENCE = "[" + "$" + "{user_config.rv_bin}]"


def _manifest():
    return json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))


def test_rv_bin_option_is_an_optional_non_sensitive_folder():
    options = _manifest()["userConfig"]
    assert list(options) == ["rv_bin"]
    rv_bin = options["rv_bin"]
    assert rv_bin["type"] == "directory"
    assert rv_bin["required"] is False
    assert not rv_bin.get("sensitive"), "a sensitive value is never substituted into skill text"
    assert rv_bin["title"] and rv_bin["description"]


def test_every_skill_passes_the_option_as_rv_bin_only_when_it_is_a_path():
    for skill in SKILLS:
        text = (REPO_ROOT / skill / "SKILL.md").read_text(encoding="utf-8")
        assert text.count(REFERENCE) == 1, skill
        after = text[text.index(REFERENCE):][:800]
        assert "--rv-bin" in after, skill
        assert "starts with a dollar sign" in after, skill
        assert "brackets are empty" in after, skill          # an unset option may render as []
        assert "never give that text as a path" in after, skill
        assert "before" in after, skill                      # options go before --push / the tool
