# Agent Asset Skills Specification

Status: ready-for-agent

## Problem Statement

AI Agents helping developers and creators build games and multimedia applications frequently need to generate, edit, inspect, and post-process multi-modal creative Assets across four core Modalities: Image, Audio, Video, and 3D. Currently, only a standalone image skill exists, leaving several gaps:

1. **Missing Audio, Video, and 3D Asset Skills**: Agents lack unified, reliable skills to generate sound effects, background music, speech, videos, and rigged/animated 3D models, or to run deterministic Local Post-Processing on those Assets without wasting tokens or re-generating content.
2. **Duplicated Foundation vs. Standalone Installation**: Each Asset Skill must be independently installable by any AI Agent host without requiring global package managers or monorepo path dependencies, yet maintaining separate copies of gateway clients, task polling, batch scheduling, state ledgers, and self-updaters leads to drift and maintenance overhead.
3. **Disconnected Cross-Modal Workflows**: Creating a game character or scene often spans multiple Modalities (for example, generating a concept Image, turning that Image into a Video or a 3D model, rigging and animating the 3D model, and exporting a 2D Frame Sequence Atlas). Without a shared credential configuration and shared local task ledger, users must configure credentials repeatedly and manually copy intermediate files between tools.
4. **Lack of Visual Human-in-the-Loop Editing and WebGL Rendering**: Pure command-line flags cannot ergonomically express visual Region Edit Annotations (pointing arrows, bounding boxes, lasso masks, and per-region instructions on an image), interactive background removal and alpha cleanup comparisons, video timeline frame-by-frame cropping for sprite sheets, or multi-direction WebGL rendering of animated 3D models into 2D Frame Sequence Atlases.

## Solution

Build **Agent Asset Skills**, a unified family of four homologous, self-contained Asset Skills (`asynx-image`, `asynx-audio`, `asynx-video`, `asynx-3d`) backed by a shared core foundation, a unified multi-modal local state ledger, and an on-demand Local UI Workbench:

1. **Self-Contained Skill Bundling from Shared Source (ADR-0001)**: Maintain a single shared runtime and core library alongside modality-specific modules and a shared frontend workbench codebase, and bundle them at build time into four standalone, zero-external-path Asset Skill packages with lockstep versioning and per-skill self-update packages.
2. **Gateway Asynchronous Generation + Keyless Local Post-Processing (ADR-0002)**: Route all generative and creative requests (`image`, `audio`, `video`, `3d`) as asynchronous Generation Tasks to the unified Asset Gateway with streaming asset downloads and SHA-256 verification, while executing deterministic transformations (image cropping/slicing/alpha cleanup, audio trimming/fading/normalization/concatenation, video frame extraction/spritesheet packing/audio muxing, and 3D mesh inspection/validation/texture extraction) locally without requiring an API key.
3. **Shared Credentials and Multi-Modal State Ledger (ADR-0003)**: Share a single user-level credential configuration and a single SQLite state ledger (`SCHEMA_VERSION = 2`) across all four Asset Skills. Automatically migrate existing v1 image ledgers without data loss, filter default task/batch/recent views by the active skill's Modality, and allow `--from-task <task-id>` to chain Assets across Modalities (such as Image-to-Video and Image-to-3D).
4. **Local Ephemeral UI Workbench (ADR-0004)**: Provide an on-demand local HTTP server (using Python's standard HTTP server bound to loopback with a one-time session token) that serves pre-bundled static web interfaces ported from the studio workbench:
   - **Image Region Edit & Local Image Tools Workbench**: Interactive arrow/rectangle/lasso Region Edit Annotations with per-mark instructions that export a transparent Mask PNG, an Annotation Preview PNG, and a structured prompt directly into an `image.edit` Generation Task; plus interactive local image tool recipes (background removal, transparent trim, alpha bleed cleanup, padding, resizing) with split-view comparison and freehand annotation.
   - **Video Frame Sequence Extractor Workbench**: Interactive video timeline range selection, frame sampling, background removal, per-frame cropping and reordering, live animation playback preview, and Frame Sequence Atlas (`atlas.png` + `manifest.json` + archive) export.
   - **3D Inspector & Multi-Angle Frame Sequence Renderer Workbench**: Interactive WebGL 3D model, skeletal animation, and PBR material channel viewer, plus a multi-direction (1-way side, 4-way/8-way top-down, 4-way/8-way isometric) orthographic WebGL renderer that exports animated 3D models into 2D Frame Sequence Atlases (`atlas.png` + `manifest.json`) and returns the saved Assets to the calling AI Agent.

## User Stories

### Shared Foundation, Configuration, State Ledger, and Bundling

1. As an AI Agent user, I want to configure my Asset Gateway API key once in any `asynx-*` Asset Skill, so that all four Asset Skills (`asynx-image`, `asynx-audio`, `asynx-video`, `asynx-3d`) immediately share the same credentials without re-configuration.
2. As an existing `asynx-image` user, I want my existing local task ledger (`SCHEMA_VERSION = 1`) to automatically and idempotently migrate to `SCHEMA_VERSION = 2` when I run any updated Asset Skill, so that none of my historical image Generation Tasks, Batches, or downloaded Assets are lost.
3. As an AI Agent, I want `recent`, `task list`, and `batch list` in each Asset Skill to filter by that skill's Modality by default, so that querying recent items in `asynx-audio` only returns audio Assets unless cross-modal lookup is explicitly requested.
4. As an AI Agent, I want to query or wait on any Generation Task by its explicit local or remote task ID from any Asset Skill, so that I can inspect or recover tasks across Modalities.
5. As an AI Agent, I want large generated Assets (such as high-bitrate videos and PBR 3D models up to hundreds of megabytes) to be streamed directly to temporary files on disk with incremental SHA-256 hashing and modality-appropriate timeouts, so that large downloads never exhaust local memory.
6. As an AI Agent user, I want a `doctor` command in each Asset Skill that validates my credentials, local state ledger health, optional Python dependencies, and external media tools (`ffmpeg` / `ffprobe`), and provides clear platform-specific installation instructions when optional tools are missing, so that I can quickly diagnose environment issues.
7. As a skill maintainer, I want a single build command that compiles the static Local UI Workbench assets, bundles the shared core library and modality-specific modules into each skill's standalone directory, and packages lockstep release archives with integrity manifests, so that every Asset Skill can be distributed and installed independently.
8. As an AI Agent user, I want each Asset Skill's `update status` and `update apply` commands to fetch and verify the release package corresponding to that specific skill name under the unified release version, so that installed skills can self-update safely with transactional rollback on failure.
9. As an AI Agent, I want a cross-process runtime file lock held during CLI execution and exclusive lock held during self-updates, so that running tasks are never corrupted by concurrent skill updates.

### Image Asset Skill (`asynx-image`)

10. As an AI Agent, I want to generate new image Assets from text prompts (`generate --prompt`) with optional model, size, aspect ratio, quality, count, and output format parameters, so that I can create concept art, UI elements, and textures.
11. As an AI Agent, I want to pass one or more local reference images (`--reference`) or a previous task ID (`--from-task`) when generating images, with automatic WebP normalization or `--keep-reference-original`, so that generated images stay consistent with reference style or character designs.
12. As an AI Agent, I want to edit existing images (`edit --prompt --image` or `--from-task`) with an optional transparency `--mask`, preserving the source image's aspect ratio by default unless overridden, so that I can modify existing visual Assets.
13. As an AI Agent, I want to create, append to, poll, pause, resume, and cancel image Batches (`batch create|add|status|wait|pause|resume|cancel`), so that I can orchestrate large sets of image Generation Tasks reliably.
14. As an AI Agent, I want to run keyless Local Post-Processing on images (`image info|convert|resize|crop|slice|pad|mask`), so that I can inspect metadata, convert formats, resize, crop, slice sprite grids, pad canvases, and apply alpha masks deterministically without calling the Asset Gateway.
15. As an AI Agent user, I want to launch the Local UI Workbench for region-based image editing (`edit --ui --image <path>` or `ui region-edit`), draw pointing arrows, rectangles, or freeform lassos on the image, attach modification instructions to each mark plus an overall prompt, and click Submit, so that the CLI automatically exports the transparent Mask PNG and Annotation Preview PNG, submits the structured `image.edit` Generation Task (`[source, annotation_preview]` + `mask` + structured annotation prompt), downloads the resulting image Asset, and returns the saved file path to the Agent.
16. As an AI Agent user, I want to launch the Local UI Workbench for interactive local image processing (`ui tools --image <path>`), preview game-asset recipes (inventory icon export, transparent asset normalization, background removal, transparent border trimming, alpha bleed fixing, resize, and pad) with source/preview/split comparison, and click Save, so that the processed image Asset is written to disk and returned to the Agent without spending API credits.
17. As an AI Agent user, I want to launch the Local UI Workbench for freehand visual annotation (`ui annotate --image <path>`), draw with brush, eraser, rectangle, arrow, and text tools with undo/redo, and save the annotated image as a new local Asset, so that I can visually mark up images for downstream workflows.

### Audio Asset Skill (`asynx-audio`)

18. As an AI Agent, I want to generate sound effect Assets (`sfx --prompt --duration`), so that I can produce UI clicks, footsteps, combat hits, and ambient audio for games and interactive media.
19. As an AI Agent, I want to generate music and soundtrack Assets (`music --prompt`) with optional `--lyrics`, `--instrumental`, `--duration`, and `--output-format` options, so that I can create background music and theme songs.
20. As an AI Agent, I want to generate speech Assets (`speech --text`) with optional `--voice`, `--reference-audio`, `--speed`, and `--emotion` parameters, so that I can synthesize character dialogue and narration.
21. As an AI Agent, I want to manage audio Generation Tasks and Batches (`recent`, `task list|status|wait|cancel|recover`, `batch ...`) using the exact same lifecycle semantics as `asynx-image`, so that I have a consistent operational mental model across Modalities.
22. As an AI Agent, I want to inspect local audio files (`audio info`) without an API key to read duration, sample rate, channel count, codec, file size, and peak/RMS amplitude, so that I can verify audio specs before integrating them into a project.
23. As an AI Agent, I want to deterministically transform local audio files without an API key (`audio convert|trim|fade|normalize|concat`), so that I can convert between `wav`/`mp3`/`ogg`/`m4a`, trim silence or time ranges, apply fade-in/fade-out envelopes, normalize loudness, and concatenate multiple clips locally.

### Video Asset Skill (`asynx-video`)

24. As an AI Agent, I want to generate video Assets from text prompts (`generate --prompt`) with configurable `--duration`, `--aspect-ratio`, `--resolution`, `--fps`, and `--generate-audio` options, so that I can create cutscenes, trailers, and character motion clips.
25. As an AI Agent, I want to generate videos from a first-frame image (`--image`), an optional last-frame image (`--end-image`), or directly from a previous `asynx-image` Generation Task (`--from-task <image-task-id>`), so that I can animate static character or scene Assets seamlessly across Modalities.
26. As an AI Agent, I want to edit or extend existing videos (`edit --prompt --video` or `--from-task`) and pass reference motion videos or audio tracks where supported by the selected model, so that I can refine video Assets iteratively.
27. As an AI Agent, I want to run keyless Local Post-Processing on videos (`video info|extract-frames|spritesheet|trim|convert|mux`), so that I can inspect video streams, extract frames by timestamp or FPS, pack extracted frames into a 2D Frame Sequence Atlas (`atlas.png` + `manifest.json`), trim clips, convert to `mp4`/`webm`/`gif`, and mux or replace audio tracks locally.
28. As an AI Agent user, I want to launch the Local UI Workbench for video frame-sequence extraction (`ui frame-sequence --video <path>`), interactively scrub the timeline start/end range, sample frames, apply chroma-key or AI background removal, crop or reorder individual frames with undo/redo, preview the animation loop at target FPS, and click Export, so that the workbench writes the Frame Sequence Atlas (`atlas.png`, `manifest.json`, and `.zip`) to disk and returns the structured result to the Agent.
29. As an AI Agent user, I want to launch the Local UI Workbench to preview an existing Frame Sequence Atlas (`ui preview-sequence --atlas <path> --manifest <path>`), so that I can visually verify animation smoothness, frame alignment, and playback FPS in the browser.

### 3D Asset Skill (`asynx-3d`)

30. As an AI Agent, I want to generate 3D model Assets (`generate`) from a text prompt (`--prompt`), a local image (`--image`), or a previous `asynx-image` Generation Task (`--from-task <image-task-id>`), with configurable `--output-format` (`glb|fbx|obj|usdz`), `--pbr`, `--texture`, `--face-limit`, and `--negative-prompt`, so that I can create 3D props and characters.
31. As an AI Agent, I want to check whether a 3D model is riggable (`rig-check --model-file <path>` or `--from-task <task-id>`) and bind a skeleton to it (`rig --rig-type auto|biped|quadruped|hexapod|octopod|serpentine|aquatic`), so that static 3D character meshes can be prepared for skeletal animation.
32. As an AI Agent, I want to retarget preset animations onto a rigged 3D model (`animate --from-task <rig-task-id>` or `--model-file <path>` with `--animation` / `--animations`, `--animate-in-place`, `--bake-animation`, and `--output-format`), so that 3D characters can perform game actions like `idle`, `walk`, `run`, `jump`, and `slash`.
33. As an AI Agent, I want to generate or re-texture a 3D mesh (`texture`) and convert 3D model formats (`convert`) via the Asset Gateway, so that I can adapt 3D Assets to target game engine requirements.
34. As an AI Agent, I want to run keyless Local Post-Processing on 3D files (`model3d info|validate|extract-textures`) using pure Python parsers without requiring Blender, so that I can inspect vertex count, triangle face count, bounding box dimensions, material/texture slots, animation clip names, validate against polygon/texture budgets, and extract embedded PBR textures from GLB/GLTF files to disk.
35. As an AI Agent user, I want to launch the Local UI Workbench for 3D inspection and sprite-sheet rendering (`ui viewer --model-file <path>` or `ui render-sequence --model-file <path>`), orbit and inspect the 3D model and its PBR texture channels (`base_color`, `metallic`, `roughness`, `normal`), preview skeletal animation clips, choose a camera view (`isometric_8way`, `isometric_4way`, `topdown_8way`, `topdown_4way`, `side_1way`), FPS, and frame size, and render the 3D animation in WebGL into a 2D Frame Sequence Atlas (`atlas.png` + `manifest.json`) saved directly to disk and returned to the Agent.

## Implementation Decisions

### 1. Monorepo Architecture and Self-Contained Skill Bundling (ADR-0001)

- Maintain four architectural layers in the repository:
  - **Shared Core Layer**: Owns the cross-process file lock runtime, HTTP client for the Asset Gateway, user-level credential configuration, SQLite task/asset/batch state ledger, task polling and recovery orchestration, streaming download manager, GitHub release self-updater, Local UI Workbench HTTP server, structured JSON output emitter, and error taxonomy.
  - **Modality Modules Layer**: Four independent modality packages (`image`, `audio`, `video`, `model3d`). Each defines its CLI argument parser, model capability validators, Generation Task payload builders, and keyless Local Post-Processing commands.
  - **Shared UI Workbench Layer**: A unified frontend workspace containing the visual editors and WebGL renderers ported from `image-flow`, compiled into static HTML/JS/CSS bundles per modality.
  - **Skill Distribution Layer**: Four skill directories (`asynx-image`, `asynx-audio`, `asynx-video`, `asynx-3d`), each containing its declarative `SKILL.md`, on-demand `references/` documentation, and a `scripts/` directory populated by the build pipeline.
- **Build & Packaging Pipeline**: A deterministic build tool copies the shared core layer, the modality's implementation modules, the modality's pre-built static UI bundle, and any required lightweight vendored wheels into each skill's `scripts/` directory, exposing a uniform entry point (`asynx.py`) across all four skills. It also generates per-skill `.zip` release archives and SHA-256 package manifests under a single lockstep semantic version.

### 2. Unified Asset Gateway Protocol and Streaming Asset Delivery (ADR-0002)

- All four skills communicate with the Asset Gateway over `/v1/tasks/models`, `POST /v1/tasks`, `GET /v1/tasks/{task_id}`, `POST /v1/tasks/{task_id}/cancel`, and `GET /v1/tasks/{task_id}/assets/{index}`.
- Model catalog responses from `/v1/tasks/models` are cached locally with a TTL and filtered by each skill's Modality (`image`, `audio`, `video`, `3d` / `model_3d`).
- **Task Types and Input Contracts**:
  - **Image**: `image.generate` (`prompt`, `count`, `image_size`, `aspect_ratio`, `quality`, `output_format`, `reference_images`) and `image.edit` (`prompt`, `images`, `mask`, `aspect_ratio`, `image_size`, `quality`, `output_format`).When submitted from the Region Edit Workbench with an Annotation Preview, `images` are ordered deterministically as `[source_image, annotation_preview_image, ...reference_images]`, `mask` carries the transparent PNG data URL, and `prompt` appends the structured `Image edit annotations:` block.
  - **Audio**: `audio.sfx` (`prompt`, `duration`, `output_format`), `audio.music` (`prompt`, `lyrics`, `instrumental`, `duration`, `output_format`), and `audio.speech` (`text`, `voice`, `reference_audio`, `speed`, `emotion`, `output_format`).
  - **Video**: `video.generate` and `video.edit` (`prompt`, `duration`, `resolution`, `aspect_ratio`, `fps`, `generate_audio`, and `inputs` carrying typed roles `first_frame`, `last_frame`, `reference`).
  - **3D**: `3d.generate` (`mode: text_to_model | image_to_model`, `prompt`, `negative_prompt`, `images`, `pbr`, `texture`, `face_limit`, `output_format`), `3d.rig_check`, `3d.rig` (`rig_type`, `rig_model`, `rig_spec`, `output_format`), `3d.retarget` (`animation` or `animations`, `bake_animation`, `export_with_geometry`, `animate_in_place`, `output_format`), `3d.texture`, and `3d.convert`.
- **Streaming Asset Downloads**: Replace in-memory buffering of downloaded Assets with chunked streaming to a temporary file in the destination output directory while computing the SHA-256 digest on the fly, then atomically renaming into place. Enforce modality-aware size and timeout ceilings (up to 50 MiB / 120s for Image and Audio; up to 500 MiB / 300s for Video and 3D).

### 3. Shared Credentials and State Ledger v2 Migration (ADR-0003)

- All four skills read and write credentials at the canonical user configuration path (`~/.config/asynx/config.json` or platform/env equivalent) and share the SQLite ledger (`~/.local/state/asx/state.db` or `ASYNX_STATE_PATH`).
- **Schema Migration (`SCHEMA_VERSION = 1` → `2`)**:
  - On database connection, inspect `PRAGMA user_version`.
  - If version is `1`, run an idempotent migration inside a transaction that adds `modality TEXT NOT NULL DEFAULT 'image'` to both `tasks` and `batches` tables (and creates an index on `(modality, updated_at)`), backfilling existing rows as `'image'`, and bumps `user_version` to `2`.
- **Modality Scoping and Cross-Modal Chaining**:
  - Commands that list or search without an explicit ID (`recent`, `task list`, `batch list`, `recover`) filter by `modality = <current_skill_modality>` by default (with an optional `--all-modalities` flag for diagnostics).
  - Commands that accept `--from-task <task-id>` look up the task across all Modalities in the ledger, verify that its local downloaded Asset files (or remote asset descriptors) match the required input media type (e.g., an image Asset from `asynx-image` passed into `asynx-video generate --from-task` or `asynx-3d generate --from-task`), and automatically wire those Assets as inputs.

### 4. Keyless Local Post-Processing Toolbox (ADR-0002)

- Local Post-Processing subcommands never require or read API credentials:
  - **`asynx-image` (`image ...`)**: Retains and standardizes `info`, `convert`, `resize`, `crop`, `slice`, `pad`, and `mask` using Pillow, plus local execution of transparent trimming, alpha bleed cleanup, and simple background removal.
  - **`asynx-audio` (`audio ...`)**: Implements `info`, `convert`, `trim`, `fade`, `normalize`, and `concat`. Uncompressed WAV inspection and basic trimming/fading/concatenation work with Python's standard library (`wave`, `audioop`/struct math) as a zero-dependency baseline, while compressed formats (`mp3`, `ogg`, `m4a`) and loudness normalization delegate to system `ffmpeg` / `ffprobe`.
  - **`asynx-video` (`video ...`)**: Implements `info`, `extract-frames`, `spritesheet`, `trim`, `convert`, and `mux` by orchestrating `ffmpeg` / `ffprobe` for video decoding/encoding and Pillow for assembling extracted frames into a Frame Sequence Atlas (`atlas.png` + `manifest.json` with unrotated, untrimmed `frame-NNNN.png` grid coordinates).
  - **`asynx-3d` (`model3d ...`)**: Implements pure-Python parsers for GLB (binary glTF 2.0 container header + JSON chunk + BIN chunk), glTF JSON, and Wavefront OBJ:
    - `info`: Extracts format, vertex count, triangle face count, bounding box min/max/extents, material names, texture descriptors, skin/joint counts, and animation clip names/durations.
    - `validate`: Checks the model against configurable game-engine constraints (`--max-faces`, `--require-pbr`, `--max-materials`, `--require-animations`).
    - `extract-textures`: Extracts embedded image buffers or data URIs from GLB/GLTF into standalone PNG/JPEG/WebP files on disk.

### 5. Local Ephemeral UI Workbench (ADR-0004)

- **Server Lifecycle**:
  - Invoked via `--ui` on editing commands or dedicated `ui <workbench-mode>` subcommands.
  - Binds an HTTP server to `127.0.0.1:0` (ephemeral free port), generates a cryptographically random session token passed in the initial URL query/hash and required on all API requests (`X-Asx-Session-Token`), preventing cross-origin or local port-scanning abuse.
  - Opens the system default browser (unless `--no-open` is passed for headless testing or remote port-forwarding) and logs the workbench URL to stderr.
- **Local HTTP API Contract**:
  - `GET /api/session`: Returns workbench mode (`region_edit`, `image_tools`, `image_annotate`, `video_frame_sequence`, `sequence_preview`, `model3d_viewer`), initial asset metadata, source file URL (`/api/source`), and eligible models from the cached/live model catalog.
  - `GET /api/source` (and `/api/assets/<name>`): Streams the local source Asset file with proper MIME type and CORS/same-origin headers so browser Canvas 2D and WebGL contexts can read pixels and geometry without tainting.
  - `POST /api/submit`: Receives the workbench submission payload:
    - For `region_edit`: Receives `mask` PNG, `annotation_preview` PNG, structured `annotations` and `instructions`, `prompt`, and `model_id`. The Python server immediately executes the `image.edit` Generation Task against the Asset Gateway, waits and downloads the resulting image Asset, records it in the state ledger, returns the result to the browser UI, schedules server shutdown, and emits the standard CLI JSON result to stdout.
    - For `image_tools` and `image_annotate`: Receives the processed/annotated PNG/JPEG/WebP blob and operation metadata, writes the new Asset file to `--output` / `--output-dir` without overwriting the source file, records the local artifact, shuts down the server, and emits the CLI JSON result.
    - For `video_frame_sequence` and `model3d_viewer` (frame sequence export): Receives the rendered `atlas.png` blob, `manifest.json` blob, and metadata (grid columns/rows, frame count, frame dimensions, FPS, view angle, directions), writes the Frame Sequence Atlas files (and optional `.zip` bundle) to disk, shuts down the server, and emits the CLI JSON result.
  - `POST /api/cancel`: Cleanly aborts the workbench session, shuts down the server, and exits with a structured cancellation code.

## Testing Decisions

### Principles of a Good Test

- Tests must verify observable external behavior (CLI exit codes, stdout JSON payloads, files written to disk, SQLite state ledger transitions, and HTTP requests sent to the fake gateway or workbench server), never internal private helper functions or implementation details.
- Every test must run deterministically offline without real network calls or real API keys by injecting temporary directories and a local loopback fake Asset Gateway server.

### Modules and Seams Under Test

1. **Asset Skill CLI Seam (Python `unittest` / `pytest` suite)**:
   - Shared foundation & migration: `SCHEMA_VERSION = 1` to `2` automatic SQLite migration preserving existing image tasks/batches; modality-filtered `recent`/`task list`/`batch list`; cross-modal `--from-task` resolution; streaming download with SHA-256 verification and size limit enforcement; runtime file lock contention; self-update manifest verification and transactional rollback.
   - `asynx-image`: `generate`, `edit` (including structured annotation preview + mask ordering), `batch` lifecycle, and `image info|convert|resize|crop|slice|pad|mask`.
   - `asynx-audio`: `sfx`, `music`, `speech` task building and submission, plus `audio info|convert|trim|fade|normalize|concat` (testing both pure-Python WAV operations and `ffmpeg`-backed operations).
   - `asynx-video`: `generate` (text-to-video, first/last frame, `--from-task` from image task), `edit`, and `video info|extract-frames|spritesheet|trim|convert|mux`.
   - `asynx-3d`: `generate` (text-to-3D, image-to-3D, `--from-task`), `rig-check`, `rig`, `animate`, `texture`, `convert`, and pure-Python `model3d info|validate|extract-textures` against synthetic GLB and OBJ fixtures.
   - Build & packaging verification: Running the build pipeline produces self-contained skill directories that pass CLI smoke tests when invoked from an isolated directory outside the monorepo.

2. **Local UI Workbench Seam**:
   - Python workbench server integration tests (`--no-open`): Start the workbench server for each mode, verify token enforcement, fetch `/api/session` and `/api/source`, POST a valid submission payload to `/api/submit` (including a Region Edit bundle and a Frame Sequence Atlas bundle), and verify that the server completes the gateway/local action, shuts down cleanly, and outputs the expected JSON payload.
   - Frontend workbench unit/component tests (Vitest, following the prior art in `image-flow` such as `ImageRegionEditDialog.test.tsx` and `ImageToolPanel.test.tsx`): Verify region mark geometry, mask and annotation preview export, local image tool pipeline operations, frame sequence manifest generation, and 3D atlas grid layout calculations.

## Out of Scope

- Building or hosting the remote Asset Gateway server itself (this repository builds the client-side Asset Skills, CLI tools, and Local UI Workbench that talk to the gateway).
- Multi-user authentication, cloud project canvases, or persistent server-side databases (all state in this project is local to the user's machine).
- Heavy local neural network inference (such as running local Stable Diffusion, Whisper, or local 3D reconstruction models on the user's GPU), except lightweight browser-side canvas/WebGL processing in the Local UI Workbench.
- Modifying third-party host Agent harnesses (the skills integrate purely through standard `SKILL.md` instructions and CLI execution).

## Further Notes

- Delivery follows the agreed two-phase rollout:
  - **Phase 1**: Extract the shared core foundation (`shared/`), implement `SCHEMA_VERSION = 2` ledger migration, streaming downloads, Local UI Workbench server, `src/image/`, `src/ui/` (image workbench modes), and `src/build_skills.py`, achieving 100% backward compatibility with existing `asynx-image` workflows plus the new visual image editing workbenches.
  - **Phase 2**: Implement `asynx-audio`, `asynx-video`, and `asynx-3d` (cloud task builders, keyless local post-processing tools, video & 3D Local UI Workbench modes, and each skill's `SKILL.md` and `references/` documentation).
