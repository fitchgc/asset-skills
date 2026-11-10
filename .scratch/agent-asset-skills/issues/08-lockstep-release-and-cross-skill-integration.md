# 08: Lockstep Release Packaging, Self-Update Verification & Cross-Skill Pipeline Integration

**What to build:** Complete lockstep release packaging (`asynx-image.zip`, `asynx-audio.zip`, `asynx-video.zip`, `asynx-3d.zip` with SHA-256 manifests), per-skill self-update discovery and transactional replacement (`update status` / `update apply`), and a full cross-skill integration verification suite exercising all four standalone installed skills together.

**Blocked by:** 03: `asynx-audio` Asset Skill (Cloud Generation & Keyless Local Audio Post-Processing), 06: `asynx-video` Local UI Workbench (Interactive Video Frame Sequence Extractor & Atlas Previewer), 07: `asynx-3d` Local UI Workbench (WebGL 3D/PBR Inspector & Multi-Angle Frame Sequence Renderer)

**Status:** ready-for-agent

- [ ] Running the release packaging command produces four self-contained zip archives (`asynx-image.zip`, `asynx-audio.zip`, `asynx-video.zip`, `asynx-3d.zip`) and verified SHA-256 package manifests sharing a single lockstep semantic version.
- [ ] Each Asset Skill's `update status` and `update apply` commands discover releases for its own skill identifier, validate package integrity and file safety rules, hold an exclusive runtime lock during replacement across known agent skill installation paths, and atomically roll back from the transaction journal if replacement fails.
- [ ] An end-to-end cross-skill integration test installs all four built zip packages into an isolated temporary agent skills directory and executes a complete multi-modal game asset workflow using a single shared configuration and state ledger: generating a character concept in `asynx-image`, chaining its task ID via `--from-task` into both `asynx-video` (followed by spritesheet extraction) and `asynx-3d` (`generate` → `rig` → `animate` → `model3d info`), and generating + normalizing companion sound effects in `asynx-audio`.
