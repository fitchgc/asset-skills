# 02: Local UI Workbench Server & `asynx-image` Visual Editing Workbenches

**What to build:** Deliver the on-demand ephemeral Local UI Workbench HTTP server and the pre-bundled browser editing workbenches for `asynx-image`—covering Region Edit Annotations (`edit --ui` / `ui region-edit`), interactive Local Image Tools (`ui tools`), and Freehand Annotation (`ui annotate`)—so that users can visually mark up or process images in their browser and have the resulting Generation Task or processed Asset automatically saved and returned to the calling AI Agent.

**Blocked by:** 01: Shared Foundation Extraction & `asynx-image` End-to-End Parity

**Status:** ready-for-agent

- [ ] Launching the Local UI Workbench binds a loopback HTTP server on an ephemeral port with a one-time session token, serves pre-bundled static frontend assets without requiring Node.js at runtime, and rejects unauthenticated requests.
- [ ] In `region-edit` mode (`edit --ui` or `ui region-edit`), users can draw pointing arrows, rectangles, and lassos on the source image, attach per-mark modification instructions plus an overall prompt, select a mask-capable image model, and submit; the browser exports a transparent `mask` PNG and an `annotation_preview` PNG, and the local server submits `image.edit` with inputs ordered as `[source, annotation_preview]`, attaches the `mask`, appends the structured `Image edit annotations:` prompt block, downloads the generated Asset, shuts down the server, and emits the standard JSON payload to stdout.
- [ ] In `tools` mode (`ui tools`), users can interactively run game-asset and canvas recipes (inventory icon export, transparent asset normalization, background removal, transparent border trim, alpha bleed fix, resize, pad, format conversion) with source/preview/compare views, and clicking Save writes a new local Asset file without mutating the source image or requiring an API key.
- [ ] In `annotate` mode (`ui annotate`), users can draw with brush, eraser, rectangle, arrow, and text tools with undo/redo, and clicking Save writes the annotated image as a new local Asset file and returns its metadata JSON to the Agent.
- [ ] Automated tests cover both the frontend workbench logic (region geometry, mask/preview export, and image tool pipeline operations) and headless Python HTTP loopback submission flows for all three modes.
