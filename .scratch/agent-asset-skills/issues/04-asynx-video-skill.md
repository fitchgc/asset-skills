# 04: `asynx-video` Asset Skill (Cloud Generation, Cross-Modal Image-to-Video & Keyless Local Video Post-Processing)

**What to build:** Deliver the self-contained `asynx-video` Asset Skill enabling AI Agents to generate and edit videos from text prompts, first/last-frame images, or previous `asynx-image` Generation Tasks (`--from-task`), and perform keyless Local Post-Processing on video Assets (`info`, `extract-frames`, `spritesheet`, `trim`, `convert`, `mux`).

**Blocked by:** 01: Shared Foundation Extraction & `asynx-image` End-to-End Parity

**Status:** ready-for-agent

- [ ] Building the skills produces a standalone `asynx-video` Asset Skill complete with declarative workflow documentation, reference guides, and a self-contained CLI entry point.
- [ ] Cloud `generate` and `edit` commands support text-to-video, first-frame (`--image`) and last-frame (`--end-image`) inputs, reference media, and cross-modal `--from-task <image-task-id>` chaining that automatically resolves image Assets produced by `asynx-image`, with configurable duration, aspect ratio, resolution, FPS, and audio generation flags.
- [ ] Large video Assets stream to disk with up to 500 MiB size ceilings and modality-appropriate timeouts, recording tasks and Assets under `modality = 'video'` in the shared state ledger.
- [ ] Keyless `video` Local Post-Processing commands (`info`, `extract-frames`, `spritesheet`, `trim`, `convert`, `mux`) inspect video streams, extract frame sequences, pack extracted frames into a 2D Frame Sequence Atlas (`atlas.png` + unrotated/untrimmed `manifest.json`), trim time ranges, convert between `mp4`/`webm`/`gif`, and mux audio tracks locally without API credentials.
- [ ] End-to-end CLI tests verify text-to-video, cross-modal `--from-task` image-to-video, streaming download validation, and all six local video post-processing subcommands.
