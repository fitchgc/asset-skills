# 04: `asynx-asset-manager` Skill CLI, Five-Category Workspace Scanner & SHA-256 Lineage Re-Linking

**What to build:** Create the fifth standalone, homologous Asset Skill (`asynx-asset-manager`) integrated into the build and installation pipelines, providing project workspace scanning across the five canonical categories (`image`, `audio`, `frame_sequence`, `video`, `3d`), distinguishing skill-generated/modified Assets from external project files, querying upstream/downstream Asset Lineage, and automatically re-linking moved or renamed files via SHA-256 so badges and parent-child relationships never break.

**Blocked by:** 03: State Ledger v3 Migration & End-to-End Asset Lineage Recording

**Status:** ready-for-agent

- [ ] `asynx-asset-manager` is built, bundled, and installable as a standalone skill directory alongside `asynx-image-pro`, `asynx-audio`, `asynx-video`, and `asynx-3d`.
- [ ] Scanning a project workspace classifies discovered files into `image`, `audio`, `frame_sequence`, `video`, and `3d` while excluding non-asset engineering directories (`.git`, `node_modules`, `.venv`, `__pycache__`, `dist`, `.scratch`).
- [ ] Sprite sheet images paired with a valid frame sequence manifest (`manifest.json` containing frame coordinates) are classified under `frame_sequence` and are not duplicated as plain static images under `image`.
- [ ] Workspace Assets are joined against the `SCHEMA_VERSION = 3` state ledger by path and SHA-256 digest, marking Assets generated or modified by any Asynx Skill with their `managed` flag, `source_kind` (`cloud_task` vs. `local_process`), `model`, `operation`, `prompt`, `task_id`, `parents`, and `children`.
- [ ] When a skill-generated or modified Asset is moved or renamed within the project directory, scanning matches the file by its SHA-256 digest against the ledger's missing path record and automatically updates `assets.path` and `asset_lineage` path references so its skill-generated badge and parent-child lineage remain intact.
- [ ] CLI commands (`scan`/`list` with `--category` and `--managed-only` filters, and `lineage <target>`) emit structured JSON and are covered by end-to-end CLI tests including file move/rename scenarios.
