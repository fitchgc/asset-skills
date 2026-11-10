# 03: `asynx-audio` Asset Skill (Cloud Generation & Keyless Local Audio Post-Processing)

**What to build:** Deliver the self-contained `asynx-audio` Asset Skill enabling AI Agents to generate sound effects (`sfx`), background music (`music`), and synthesized speech (`speech`) via the Asset Gateway, manage audio Generation Tasks and Batches, and run keyless Local Post-Processing on audio files (`info`, `convert`, `trim`, `fade`, `normalize`, `concat`).

**Blocked by:** 01: Shared Foundation Extraction & `asynx-image` End-to-End Parity

**Status:** resolved

- [x] Building the skills produces a standalone `asynx-audio` Asset Skill complete with declarative workflow documentation, reference guides, and a self-contained CLI entry point.
- [x] Cloud generation commands support `sfx` (prompt and duration), `music` (prompt, optional lyrics or instrumental flag, duration, output format), and `speech` (text, voice selector, optional reference audio or `--from-task`, speed, emotion, output format), recording all tasks and downloaded audio Assets under `modality = 'audio'` in the shared state ledger.
- [x] Task lifecycle commands (`recent`, `task list|status|wait|cancel|recover`, and `batch` operations) scope to `audio` by default and share user credentials with the rest of the skill family.
- [x] Keyless `audio` Local Post-Processing commands (`info`, `convert`, `trim`, `fade`, `normalize`, `concat`) work offline without API credentials, supporting uncompressed WAV inspection and editing with Python's standard library and delegating compressed codec conversion (`mp3`, `ogg`, `m4a`) and loudness normalization to `ffmpeg` / `ffprobe` with clear diagnostic errors when unavailable.
- [x] End-to-end CLI tests verify cloud task submission/downloading, batch execution, and all six local audio post-processing subcommands.

## Answer

Implemented the self-contained `asynx-audio` Asset Skill with declarative documentation (`SKILL.md`, `references/operations.md`, `setup.md`, `audio-models.md`, `agents/openai.yaml`), cloud task builders for `sfx`, `music`, and `speech` (`src/audio/audio_tasks.py`), and keyless local audio post-processing (`src/audio/local_audio.py` for `info`, `convert`, `trim`, `fade`, `normalize`, `concat` using Python's standard `wave` and `struct` plus `ffmpeg`/`ffprobe`). Verified with full automated test suite in `tests/test_issue_03_audio_skill.py`.
