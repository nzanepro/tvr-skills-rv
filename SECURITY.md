# Security policy

## What this project does and does not do

Every script in this repository runs **locally**, with your own user permissions, and talks
only to RV / OpenRV on the same machine (through `rv`, `rvpush`, `rvio`, `rvls` and `rvpkg` on
`PATH` or a path you give it). Nothing here:

- makes network calls itself,
- uploads images, renders, or file paths anywhere,
- installs or downloads RV/OpenRV, packages, or any other software on its own, or
- needs, stores, or asks for credentials, tokens, or account details.

`rvpkg`-related scripts can install or remove `.rvpkg` packages, but only ones you point them
at; they do not fetch packages from the network. Because everything runs with the permissions
of the account that starts it, only run these scripts against RV/OpenRV builds and packages
you trust, the same as any other local script.

## Reporting a vulnerability

If you find a security issue (for example, a way a script could be made to run unintended
code, write outside the folder you asked it to, or otherwise behave unsafely), please use
GitHub's private vulnerability reporting instead of a public issue:

1. Open [Report a vulnerability](https://github.com/nzanepro/tvr-skills-rv/security/advisories/new)
   while signed in to GitHub (or, on the repository's main page, click the
   **Security and quality** tab, then **Report a vulnerability**).
2. Fill in the form (only the title and description are required) and click
   **Submit report**. Only you and the repository's maintainers can see the report.

If private reporting is not available to you for some reason, open a regular issue that says
only that you have a security report to make, without details, and ask for another way to
reach the maintainer.

Please include:

- The affected script(s) or skill(s) and version (the plugin version shown in `/plugin`,
  `metadata.version` in the relevant `SKILL.md`, or a commit hash).
- Steps to reproduce, with synthetic inputs rather than anything from a real project.
- What you expected to happen and what happened instead.

There is no bug bounty; this is a small open-source project maintained in spare time. Reports
will be acknowledged and, where they turn out to be real issues, fixed and credited in the
changelog unless you ask not to be named.

## Supported versions

Only the latest released version (see [CHANGELOG.md](CHANGELOG.md)) is supported with
security fixes. There is no long-term-support branch.
