# 06: `asynx-video` Local UI Workbench (Interactive Video Frame Sequence Extractor & Atlas Previewer)

**What to build:** Extend the Local UI Workbench to `asynx-video` (`ui frame-sequence` and `ui preview-sequence`) so users can interactively scrub video timelines in the browser, extract frames, remove backgrounds, crop and reorder individual frames with undo/redo, preview animation playback at target FPS, and export a 2D Frame Sequence Atlas (`atlas.png` + `manifest.json` + `.zip`) saved to disk and returned to the AI Agent.

**Blocked by:** 02: Local UI Workbench Server & `asynx-image` Visual Editing Workbenches, 04: `asynx-video` Asset Skill (Cloud Generation, Cross-Modal Image-to-Video & Keyless Local Video Post-Processing)

**Status:** ready-for-agent

- [ ] Running `ui frame-sequence` on a local video (or `--from-task`) launches the pre-bundled Video Frame Sequence Extractor Workbench in the browser with timeline start/end handles, frame count and FPS controls, output dimension scaling, chroma-key/background removal options, per-frame crop and drag-reorder with undo/redo, and live animation loop preview.
- [ ] Clicking Export in the Video Frame Sequence Extractor posts the assembled `atlas.png`, unrotated/untrimmed `manifest.json` (`frame-0000.png` ...), and editor metadata to the local Python workbench server, which writes the Frame Sequence Atlas files (and optional `.zip` archive) to the output directory, records the artifact, shuts down the server, and prints the structured JSON result to stdout.
- [ ] Running `ui preview-sequence` opens the Frame Sequence Atlas Previewer in the browser, rendering the sprite sheet animation at configurable FPS with frame-by-frame scrubbing.
- [ ] Automated tests cover both the frontend frame-sequence extraction/manifest state machine and headless Python HTTP loopback submission of an exported Frame Sequence Atlas.
