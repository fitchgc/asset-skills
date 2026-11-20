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
- **Core and workbench packages.** Each modality ships a core package with generation, query and
  download only. The workbench package `plaiers/asynx-workbench` (directory `plaiers-asynx-workbench`) also carries the asset manager. It holds the
  asset manager, every Local UI Workbench (`ui/`, `ui/<modality>/`), `ui_server.py`, `thumb_cache.py`
  and every `local_*` module. A core package locates it on demand: `ASYNX_WORKBENCH_DIR`, else a sibling
  `plaiers-asynx-workbench` in the same skills directory. It then appends the
  workbench `scripts/asxlib` to its own `asxlib.__path__`, so core modules keep precedence. A missing
  workbench returns `workbench_not_installed` (with the `get_skill` ID). A different version returns
  `workbench_version_mismatch`. The version is checked only when a workbench feature is used. Merging
  the asset manager avoids a sixth package that would duplicate the same UI and local modules.
- **No bundled fonts.** `Anton-Regular.ttf` is removed. The CSS uses Anton only when installed locally,
  otherwise Impact/sans-serif. Publishing the font as a binary resource waits on Q1.
- **Build checks.** Every package must have ≤ 200 files and ≤ 4 MiB, and every file must be UTF-8.
  `.DS_Store`/caches are pruned; any violation fails the build.
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
- Names (PR #10 Q2, confirmed 2026-11-20): `plaiers/asynx-{image-pro,audio,video,3d,workbench}`, with `name` and
  directory `plaiers-asynx-*`. The repository's `skills/` directories use the same names.
