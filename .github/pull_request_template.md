<!--
Thanks for the pull request. Please fill in every section below and delete this comment.
See CONTRIBUTING.md for the test/eval commands and the privacy rules for anything you add
(no personal paths, no real production file names, synthetic demo images only).
-->

## What this changes and why

<!-- One or two sentences. Link the issue this addresses, if there is one. -->

## Which skill(s)

- [ ] rv-review
- [ ] rvio
- [ ] rvls
- [ ] rvpkg
- [ ] Repository-wide (docs, CI, templates, etc.)

## Testing

- [ ] `python -m pytest tests -q` passes locally
- [ ] `claude plugin validate . --strict` and `python scripts/check_repo.py` pass (only needed if you touched `.claude-plugin/` or a `SKILL.md`)
- [ ] Tested against a real RV / OpenRV build: <!-- version/build and OS, or "not applicable" -->
- [ ] Added or updated tests for the behavior this changes
- [ ] Added or updated trigger-eval entries in `evals/` (only needed if a `SKILL.md` `description:` changed)

## Version bump

- [ ] `metadata.version` in the affected `SKILL.md`(s) was bumped
- [ ] The plugin version in `.claude-plugin/marketplace.json` was bumped
- [ ] `CHANGELOG.md` has a new entry under `[Unreleased]` (or the maintainer will add one)
- [ ] Not applicable (docs-only / internal change with no user-facing effect)

## Checklist

- [ ] No personal file paths, usernames, machine names, emails, or tokens in code, tests, or commit messages
- [ ] No real production file names, shot names, or imagery; demo/test data is synthetic or clearly fictional
- [ ] `SKILL.md` changes keep the file under 500 lines, with any new gotcha documented in `SKILL.md` itself
