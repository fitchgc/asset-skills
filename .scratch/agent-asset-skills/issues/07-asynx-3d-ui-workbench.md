# 07: `asynx-3d` Local UI Workbench (WebGL 3D/PBR Inspector & Multi-Angle Frame Sequence Renderer)

**What to build:** Extend the Local UI Workbench to `asynx-3d` (`ui viewer` and `ui render-sequence`) so users can interactively inspect GLB/FBX 3D models, skeletal animations, and PBR texture channels in Three.js, and render animated 3D models from 1/4/8 orthographic camera angles (side, top-down, isometric) into 2D Frame Sequence Atlases (`atlas.png` + `manifest.json`) saved to disk and returned to the AI Agent.

**Blocked by:** 02: Local UI Workbench Server & `asynx-image` Visual Editing Workbenches, 05: `asynx-3d` Asset Skill (Cloud 3D Generation, Rigging, Animation & Pure-Python 3D Post-Processing)

**Status:** resolved

- [x] Running `ui viewer` or `ui render-sequence` on a local GLB/FBX model (or `--from-task`) launches the pre-bundled 3D Workbench in the browser with orbit controls, skeletal animation playback controls (clip selector, play/pause, loop, speed), and a PBR material/texture inspection panel (`base_color`, `metallic`, `roughness`, `normal`) with per-channel download.
- [x] In the Frame Sequence export dialog, users can select an animation clip, camera view (`isometric_8way`, `isometric_4way`, `topdown_8way`, `topdown_4way`, `side_1way`), FPS, frame width/height, and optional per-direction split; the browser renders the orthographic WebGL frames with transparent background and in-place root stabilization (enforcing the 240-frame safety cap) into a square-optimized Frame Sequence Atlas (`atlas.png` + `manifest.json`).
- [x] Submitting the rendered 3D Frame Sequence Atlas posts the resulting `atlas.png`, `manifest.json`, and direction/animation metadata to the local Python workbench server, which writes the files (and optional `.zip`) to disk, shuts down the server, and returns the structured JSON result to the calling AI Agent.
- [x] Automated tests cover 3D direction azimuth compensation, square atlas grid calculation, in-place root stabilization, PBR material extraction helpers, and headless Python HTTP loopback submission of multi-direction 3D Frame Sequence Atlases.

## Answer

Delivered the `asynx-3d` Local UI Workbench (`src/ui/3d/index.html`) featuring WebGL 3D model orbit inspection, PBR channel viewing, and orthographic multi-direction 2D Frame Sequence rendering across 5 camera angles (`side_1way`, `topdown_4way`, `topdown_8way`, `isometric_4way`, `isometric_8way`) with Tripo azimuth compensation, 240-frame safety capping, in-place root stabilization, and square atlas grid packing. Verified with automated tests in `tests/test_issue_07_3d_ui_workbench.py`.
