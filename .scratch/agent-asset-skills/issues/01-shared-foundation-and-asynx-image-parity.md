# 01: Shared Foundation Extraction & `asynx-image` End-to-End Parity

**What to build:** Extract the shared multi-modal core foundation and self-contained skill bundler so that building `asynx-image` produces a standalone, zero-external-path Asset Skill that preserves 100% behavioral parity with existing image workflows while upgrading the local state ledger to `SCHEMA_VERSION = 2`, streaming large Asset downloads to disk with SHA-256 verification, scoping default queries by Modality, and resolving cross-modal `--from-task` references.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Running the skill build pipeline produces a self-contained `asynx-image` skill package that executes independently outside the repository tree without external path dependencies.
- [ ] Connecting to an existing `SCHEMA_VERSION = 1` SQLite state ledger automatically and idempotently migrates `tasks` and `batches` to `SCHEMA_VERSION = 2` with `modality = 'image'` and zero loss of historical tasks, batches, or downloaded Assets.
- [ ] All four Modalities share a single user-level credential configuration, and `doctor` reports credential health, state ledger status, optional Python imaging support, and external media tool availability (`ffmpeg` / `ffprobe`) with installation guidance.
- [ ] Default list and discovery commands (`recent`, `task list`, `batch list`, `recover`) filter by the active skill's Modality unless `--all-modalities` is requested, while explicit task lookup and `--from-task <task-id>` resolve across Modalities.
- [ ] Asset downloads stream in chunks to a temporary file while computing SHA-256 on the fly and enforce modality-specific size and timeout limits without buffering the entire file in memory.
- [ ] `asynx-image` passes end-to-end CLI tests (against a loopback fake Asset Gateway) for `generate` (with and without reference images and `--from-task`), `edit` (with and without transparency masks), `batch` creation/addition/polling/pause/resume/cancel, and keyless `image` Local Post-Processing (`info`, `convert`, `resize`, `crop`, `slice`, `pad`, `mask`).
