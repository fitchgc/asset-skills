# 05: Unified Asset Manager Workbench & Cross-Skill Derivation Workflows

**What to build:** Deliver the interactive `asynx-asset-manager` Local UI Workbench that displays all project Assets by category (`image`, `audio`, `frame_sequence`, `video`, `3d`), highlights skill-generated Assets, visualizes upstream/downstream parent-child Asset Lineage chains and graphs, and allows users to directly launch cross-skill editing and generation workflows in place while automatically recording parent-child lineage for every resulting Asset.

**Blocked by:** 02: On-Demand Login Workbench & Top-Bar Account Management UI, 04: `asynx-asset-manager` Skill CLI, Five-Category Workspace Scanner & SHA-256 Lineage Re-Linking

**Status:** resolved

- [x] Launching the `asynx-asset-manager` workbench (`ui` command) opens a unified browser interface with category tabs (`All`, `Image`, `Audio`, `Frame Sequence`, `Video`, `3D Model`), source filters (`All`, `Asynx Skill Generated/Modified`, `External`), search, and `Asynx AI` badges on managed Assets.
- [x] Selecting any Asset opens a detail and lineage inspector showing its metadata, generation parameters, clickable Upstream Parent cards, clickable Downstream Child cards, and an interactive Lineage Graph / Tree view.
- [x] Selecting an `image` Asset allows invoking in place:
  1. **Edit Image** (interactive Region Edit annotations, Local Image Tools, Freehand Annotation, or prompt-based `image.edit`);
  2. **Generate Frame Sequence** (both AI Sprite Sheet generation into `atlas.png` + `manifest.json` and deterministic Grid Slice into `atlas.png` + `manifest.json`);
  3. **Generate Video** (`video.generate` with the image pre-bound as `first_frame`);
  4. **Generate 3D Model** (`3d.generate` in `image_to_model` mode with the image pre-bound).
- [x] Selecting `frame_sequence`, `video`, `3d`, or `audio` Assets allows invoking their corresponding skill interfaces in place:
  - `frame_sequence`: interactive animation loop preview (FPS, direction selector, frame scrubber);
  - `video`: interactive Video Frame Sequence Extractor & chroma-key background removal (`atlas.png` + `manifest.json` export), `video.edit`, and local `trim`/`convert`/`mux`;
  - `3d`: interactive WebGL 3D Inspector & Multi-Angle Frame Sequence Renderer (`atlas.png` + `manifest.json` export), `rig-check`, `rig`, `animate`, `texture`, and `convert`;
  - `audio`: waveform playback preview, reference-voice `speech` synthesis, and local `trim`/`fade`/`normalize`/`convert`.
- [x] Every Asset created or modified through an Asset Manager action is saved to the project, automatically linked to the source Asset(s) as its parent in `asset_lineage`, and immediately reflected in the Asset Manager UI with its updated parent-child connection.
- [x] All five Asset Skills (`asynx-image-pro`, `asynx-audio`, `asynx-video`, `asynx-3d`, `asynx-asset-manager`) build and package cleanly in lockstep and pass the complete end-to-end test suite.

## Answer

Delivered the Unified Asset Manager Workbench (`src/ui/asset_manager/index.html`, `src/asset_manager/actions.py`, `shared/asxlib/ui_server.py`, and shared `src/ui/common/model3d-loader.js`), adding category tabs, managed/external filtering, clickable upstream/downstream cards and SVG DAG lineage graph, real GLB/glTF/OBJ 3D rendering, embedded sub-workbenches (`region_edit`, `image_tools`, `image_annotate`, `video_frame_sequence`, `model3d_viewer`), and cross-skill action dispatch with automatic parent-child `asset_lineage` recording. Updated `src/build_skills.py` for deterministic lockstep self-contained packaging across all five skills (`dist/*.zip` + `dist/*-0.7.0.json`) and verified all 68 tests pass (`tests/test_ticket_05_asset_manager_workbench.py`, `tests/test_issue_08_cross_skill_integration.py`).
