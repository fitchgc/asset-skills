# 05: `asynx-3d` Asset Skill (Cloud 3D Generation, Rigging, Animation & Pure-Python 3D Post-Processing)

**What to build:** Deliver the self-contained `asynx-3d` Asset Skill enabling AI Agents to generate 3D models from text or images (including cross-modal `--from-task <image-task-id>`), check riggability (`rig-check`), bind skeletons (`rig`), retarget preset animations (`animate`), re-texture meshes (`texture`), convert 3D formats (`convert`), and run keyless pure-Python 3D Local Post-Processing (`info`, `validate`, `extract-textures`) without requiring Blender.

**Blocked by:** 01: Shared Foundation Extraction & `asynx-image` End-to-End Parity

**Status:** ready-for-agent

- [ ] Building the skills produces a standalone `asynx-3d` Asset Skill complete with declarative workflow documentation, reference guides, and a self-contained CLI entry point.
- [ ] Cloud commands support `generate` (text-to-3D, local image-to-3D, and cross-modal `--from-task <image-task-id>` with `pbr`, `texture`, `face-limit`, `negative-prompt`, and `output-format`), `rig-check`, `rig` (`auto | biped | quadruped | hexapod | octopod | serpentine | aquatic`), `animate` (preset animation retargeting with in-place and bake options), `texture`, and `convert`, storing tasks and Assets (including preview thumbnails when returned) under `modality = '3d'`.
- [ ] Keyless `model3d` Local Post-Processing commands (`info`, `validate`, `extract-textures`) parse GLB, glTF, and OBJ files in pure Python without external 3D DCC tools: reporting vertex/face counts, bounding box dimensions, materials, textures, skin/joint counts, and animation clips; validating against polygon/material/PBR/animation requirements; and extracting embedded PBR textures from GLB/glTF to standalone image files on disk.
- [ ] End-to-end CLI tests verify cloud 3D generation and animation task chains (`generate` → `rig` → `animate`), cross-modal image-to-3D via `--from-task`, and pure-Python `info`, `validate`, and `extract-textures` on GLB and OBJ test fixtures.
