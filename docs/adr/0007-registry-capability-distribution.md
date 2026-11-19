# ADR-0007: Registry capability distribution, slim packages and shared runtime

Status: accepted (dev only), 2026-11-20

## Context

The Plaiers owner rejected the separate GitHub install chain for the Asset Skills (PR tanqunzhao/Plaiers#10).
Asset Skills must reach users through the Plaiers skill registry as `kind: "capability"` entries with pinned
content-hash versions, integrity checks and withdrawal semantics. The registry accepts UTF-8 text only
(≤ 4 MiB, ≤ 200 files).

## Decision

- **Distribution.** Each Asset Skill is a registry capability. Agents discover it with
  `search_skills(kind: "capability")` / `get_skill`, and install an exact version only through the Plaiers
  installer (`install-capability-skill.mjs`), which fetches via `plaiers-skills.mjs fetch --version`.
- **Slim packages.** No vendored third-party code and no binaries. Pillow lives in one shared, hash-locked
  runtime per `(os, arch, python)` and is prepared only by an explicit, user-approved `runtime prepare`
  (supersedes the vendor parts of ADR-0001).
- **Credentials.** JWTs come only from the `get-skill-jwt.mjs` argv recorded in `.plaiers-context.json`
  (supersedes the credential-lookup parts of ADR-0005).
- **Updates.** The GitHub release poller, the background update check and in-place `update apply` /
  `update rollback` are removed (supersedes the self-update parts of ADR-0001). `update status` compares
  the installed `skill_version` from the context with the registry's current version and reports withdrawal.
  Applying an update is a user-confirmed installer run with the new pinned version. The package never
  rewrites its own files, and a withdrawn installed version is reported, never deleted.

## Consequences

- Repository `install.py` copies remain possible for development; they are not registry installs and
  `update status` reports `not_registry_install` for them.
- Binary resources (font) and dependency declaration wait for the registry protocol (PR #10 Q1).
