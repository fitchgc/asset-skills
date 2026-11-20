# Shared Multi-Modal State Ledger Across Independent Skills

> Amended by [ADR-0008](0008-identity-bound-task-ledger.md) (2026-11-20): the ledger is shared by the skills of one identity (site + Agent + account), not by every installation of one OS user.

All four independently installed skills (`asynx-image-pro`, `asynx-audio`, `asynx-video`, `asynx-3d`) share a single user-level configuration file (`~/.config/asynx/config.json`) and a single local SQLite task ledger (`~/.local/state/asx/state.db` at `SCHEMA_VERSION = 2`), where tasks and batches carry a `modality` column (`image | audio | video | 3d`, migrating legacy v1 rows as `image`). Each skill filters `recent`, `task list`, and `batch list` by its own modality by default, while allowing explicit cross-modal task references (`--from-task <task-id>`) so agents can chain outputs across skills (such as image-to-video or image-to-3D) without re-configuring credentials or manually copying intermediate files.
