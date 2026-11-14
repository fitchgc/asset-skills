# 03: State Ledger v3 Migration & End-to-End Asset Lineage Recording

**What to build:** Upgrade the shared SQLite state ledger from `SCHEMA_VERSION = 2` to `SCHEMA_VERSION = 3` without data loss and automatically record directed single-parent and multi-parent Asset Lineage across all cloud Generation Tasks, keyless Local Post-Processing CLI commands, and Local UI Workbench exports.

**Blocked by:** 01: Identity Service & Asset Gateway Dual-Protocol Client with Auto-Refresh

**Status:** ready-for-agent

- [ ] Connecting to an existing `SCHEMA_VERSION = 1` or `2` SQLite state ledger automatically and idempotently migrates to `SCHEMA_VERSION = 3`, adding `asset_lineage` and asset classification/source metadata columns with zero loss of existing tasks, batches, or assets:
  ```sql
  CREATE TABLE IF NOT EXISTS asset_lineage (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      parent_artifact_id TEXT,
      parent_path TEXT NOT NULL,
      parent_sha256 TEXT,
      child_artifact_id TEXT NOT NULL REFERENCES assets(artifact_id) ON DELETE CASCADE,
      child_path TEXT NOT NULL,
      child_sha256 TEXT,
      operation TEXT NOT NULL,
      modality TEXT NOT NULL,
      task_id TEXT,
      created_at TEXT NOT NULL,
      UNIQUE(parent_path, child_artifact_id, operation)
  );
  ```
- [ ] Cloud Generation Tasks that consume input assets (`image.generate` with `--reference`, `image.edit`, `audio.speech` with `--reference-audio`, `video.generate` with `--image`/`--end-image`/`--reference`, `video.edit`, `3d.generate` from images, `3d.rig`, `3d.retarget`, `3d.texture`, `3d.convert`, and any task using `--from-task`) automatically link all input source Assets as parents of the downloaded output Asset(s).
- [ ] Keyless Local Post-Processing commands across `asynx-image-pro`, `asynx-audio`, `asynx-video`, and `asynx-3d` register their output files in the `assets` ledger (`source_kind = 'local_process'`) and record their input file(s) as parent(s) in `asset_lineage` (including multi-parent operations such as `audio concat`, `video mux`, and `image contact-sheet`).
- [ ] Local UI Workbench submissions (`region_edit`, `image_tools`, `image_annotate`, `video_frame_sequence`, and `model3d_viewer` sequence rendering) register their exported Asset(s) and link the source Asset as the parent in `asset_lineage`.
- [ ] Automated tests verify v1/v2-to-v3 schema migration as well as single-parent, multi-parent, and multi-hop cross-modal lineage chains across cloud, local CLI, and UI workbench operations.
