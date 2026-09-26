# Contributing to tvr-skills-rv

Thanks for looking at this. The project is four small Agent Skills (`rv-review`, `rvio`,
`rvls`, `rvpkg`) plus the standard-library scripts behind them. Most contributions fall into
one of: a bug fix, a new rvio/rvls/rvpkg recipe, a fix to a "gotcha" the skills protect
against, or a report that a skill did not trigger when it should have.

## Before you start

- Open an issue first for anything beyond a small fix, so the approach can be agreed before
  you write code.
- One change per pull request. Keep unrelated formatting out of a functional change.

## Running the tests

```bash
python -m pip install numpy Pillow pytest
python -m pytest tests -q
```

The tests never need RV or OpenRV installed; they exercise the scripts directly (`sheet_panels.py`,
`rv_review.py`, `rvio_cmd.py`, `rvio_codecs.py`, `rvls_check.py`, `rvpkg_list.py`, `rv_find.py`)
against synthetic inputs and captured RV/rvpush output shapes. CI runs the same suite on
Windows, macOS and Linux with Python 3.10 and 3.13; keep new tests OS-independent (no
hardcoded path separators, no assumption about which drive or home folder exists).

If you can test against a real RV or OpenRV build, note the exact build and OS in the pull
request description; the README's "Checked against OpenRV" notes and SKILL.md gotchas should
only claim what was actually verified.

## Running the trigger evals

Each skill has a trigger-eval file under `evals/` in the [skill-creator trigger-eval
format](https://github.com/anthropics/skills): prompts that should trigger the skill, prompts
that should trigger a different one of the four, and near misses that should not trigger any
of them. If you change a `description:` in a `SKILL.md` frontmatter block, add or update the
matching eval entries so the change is checked, not just asserted in the pull request text.

## Validating the plugin

Before opening a pull request that touches `.claude-plugin/marketplace.json` or any
`SKILL.md`, run:

```bash
claude plugin validate .
```

## Privacy rules for anything you contribute

This is a public repository. Please keep contributions free of:

- Personal file paths (home directories, usernames, machine names, drive letters that are not
  purely illustrative like `C:\` in a generic example).
- Real email addresses, account names, or API keys/tokens, including in code comments, test
  fixtures, and commit messages.
- Real production file names, shot names, project names, or studio names. Use neutral,
  obviously-fictional examples instead (`shot010`, `lighting_v2`, `render_a.png`); the existing
  worked examples in the README and `evals/` are good models to copy.
- Screen recordings or screenshots that include anything outside the RV window itself (desktop
  background, other application windows, file browser paths, taskbar).
- Real renders, plates, or other copyrighted/production imagery. Demo and test images should
  be synthetic (procedurally generated, e.g. with Pillow), not real project output.

If you are not sure whether something counts as personal or production data, ask in the pull
request rather than posting it.

## Adding an rvio / rvls / rvpkg recipe or reference

The command-line skills (`rvio`, `rvls`, `rvpkg`) document real failure modes (things that
exit 0 when they should not, silent gap-filling, and so on) alongside working recipes. A good
addition:

- States which RV/OpenRV build and OS it was checked against.
- Is a real command that was actually run, not a guess at rvio/rvls/rvpkg's behavior.
- Goes in the skill's `references/` file, with a one-line pointer added from `SKILL.md` if the
  file is new.
- Comes with a test in `tests/` when it changes a script's behavior (for example, a new codec
  check in `rvio_codecs.py` or a new failure `rvls_check.py` should catch).

## Code style

- `SKILL.md` files stay under 500 lines; put anything longer in `references/` and link to it.
  Keep gotchas (the known ways a tool lies about success) in `SKILL.md` itself, not buried in a
  reference file, since that is what an agent reads first.
- Scripts (`scripts/*.py`) use only the Python standard library, except `sheet_panels.py`,
  which needs `numpy` and `Pillow` for image work. Do not add a new third-party dependency
  without discussing it in an issue first; every extra dependency is another thing a fresh
  install can fail on.
- Scripts print one JSON line as their machine-readable result and use exit codes that mean
  something (0 = verified/succeeded, 1 = error, other codes documented in the script's
  `--help`). Follow that pattern for new scripts rather than inventing a new output shape.
- Match the existing docstring and `--help` style: short, and enough to use the script as a
  black box without reading the source.

## Reporting a skill that did not trigger

"Did not trigger" issues are some of the most useful ones because they usually mean the
`description:` in `SKILL.md` needs a phrase added. Please include:

- The exact prompt you typed.
- Which client (Claude Code, claude.ai, another Agent Skills client) and how the skill was
  installed (plugin marketplace, personal skill, project skill).
- Which skill you expected to trigger, and which one triggered instead (if any).

## Adding a new skill

This repository is scoped to RV/OpenRV media review and its command-line tools. A skill for
an unrelated tool almost certainly belongs in its own repository rather than here; open an
issue to discuss scope before building one. A skill that extends what these four already cover
(for example, another rv/rvpush workflow) should follow the same layout as the existing
skills: `SKILL.md` with frontmatter (`name`, `description`, `license`, `compatibility`,
`metadata.version`), `LICENSE.txt`, `scripts/`, and `references/` for anything long.
