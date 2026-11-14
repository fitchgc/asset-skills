# Identity Service Authentication, Asset Lineage, and Asset Manager Specification

Status: ready-for-agent

## Problem Statement

Users and AI Agents creating multi-modal game and multimedia Assets across the Asset Skill family (`asynx-image-pro`, `asynx-audio`, `asynx-video`, `asynx-3d`) currently face three major friction points:

1. **Incompatible Static API Key Authentication vs. Dual-Server Partner Token Protocol**: The existing skills authenticate using a static `asx-...` API key sent via an `Authorization: Bearer` header to a hardcoded gateway URL. However, the client AI integration protocol delegates authentication to an Identity Service (`login_svr`) that issues short-lived Ed25519 (`EdDSA`) Partner Tokens (`token`, valid for 1 day) and long-lived Refresh Tokens (`refreshToken`, valid for 30 days) along with dynamic Asset Gateway routing metadata (`asynx.baseUrl`, `tokenHeader`, `issuer`, `audience`, `kid`). Furthermore, the Asset Gateway strictly requires `X-Asynx-Partner-Token` and rejects requests that also include an `Authorization` header with `401 ambiguous_credentials`, uses distinct response envelopes (`code === "ok"` with HTTP `200`/`202` vs. `code === 0` on the Identity Service), and requires proactive and reactive token refresh across long-running Generation Tasks.
2. **Lack of an On-Demand Login Workbench**: When local credentials (`token` and `refreshToken`) are missing, expired, or invalidated (such as after a password change or token rotation), users and AI Agents have no browser-based login and registration page that can be invoked automatically to authenticate against the Identity Service, provision their partner identity on the Asset Gateway, and seamlessly resume the interrupted command.
3. **No Unified Project Asset Manager or Asset Lineage Tracking**: As a project accumulates dozens or hundreds of Assets across images, audio clips, Frame Sequence Atlases, videos, and 3D models—some placed manually in the workspace and others generated or modified by Asset Skills—creators cannot:
   - View all project Assets organized by category (`image`, `audio`, `frame_sequence`, `video`, `3d`) in a single workbench;
   - Distinguish Assets generated or modified by our Asset Skills from external project files;
   - Trace the parent-child derivation history (**Asset Lineage**) of how an Asset was created or modified (e.g., which concept image produced a video, which video produced a Frame Sequence Atlas, or which image was edited into a new variant), especially when files are moved or renamed within the project;
   - Launch cross-skill editing and derivation workflows (such as editing an image, generating a Frame Sequence Atlas from an image, generating a video from an image, generating a 3D model from an image, extracting frame sequences from a video, or rigging/animating/rendering a 3D model) directly from a selected Asset in the Asset Manager interface while automatically recording the resulting parent-child relationship.

## Solution

Upgrade the shared core foundation across all Asset Skills and introduce a fifth standalone, homologous Asset Skill, **`asynx-asset-manager`**, integrated with the Local UI Workbench:

1. **Dual-Protocol Identity Service & Asset Gateway Client (ADR-0005)**:
   - Replace static `asx-...` API key authentication with session credentials (`login_svr_url`, `token`, `refreshToken`, `asynx`, `user`) managed against the Identity Service (`login_svr`).
   - Strictly separate HTTP envelope parsing and authentication headers between the Identity Service (`Authorization: Bearer <token>`, `code === 0`) and the Asset Gateway (`X-Asynx-Partner-Token: <token>` only, never sending `Authorization`, checking `code === "ok"`, HTTP `202` for task submission, `Idempotency-Key` headers, and stripping partner credentials on cross-origin `302` asset download redirects).
   - Implement automatic two-tier token refresh: proactive pre-flight refresh when `payload.exp - now <= 60` or when `kid`/`iss`/`aud` mismatch `/api/user/asynx-config`, and reactive retry refresh when any Asset Gateway request or task poll returns HTTP `401` (`invalid_partner_token`).
   - Automatically provision and query user quota and concurrency status via `GET {baseUrl}/v1/partner/identity` upon login and before task submission.

2. **On-Demand Login Workbench & Account Bar (ADR-0004, ADR-0005)**:
   - Extend the Local UI Workbench with a `login` mode that launches automatically whenever any authenticated CLI command or workbench action finds missing or unrefreshable credentials (and can also be invoked explicitly via `login`).
   - Provide account login, new account registration with real-time username availability checks, configurable Identity Service URL, password change, account switching, and logout.
   - Once the user completes login or registration in the browser, persist the session credentials, provision partner identity on the Asset Gateway, and automatically resume the interrupted CLI command or workbench operation without requiring the user or AI Agent to re-run the command.
   - Display the active user identity, remaining budget quota, and active task concurrency in the top navigation bar across all Local UI Workbenches.

3. **Asset Lineage Ledger (`SCHEMA_VERSION = 3`) & `asynx-asset-manager` Skill (ADR-0006)**:
   - Upgrade the shared SQLite state ledger from `SCHEMA_VERSION = 2` to `SCHEMA_VERSION = 3` with zero-data-loss migration, recording directed parent-child **Asset Lineage** (`asset_lineage`) across both cloud Generation Tasks and deterministic/UI Local Post-Processing operations (supporting single-parent and multi-parent DAG derivations).
   - Match workspace files against ledger records using both canonical file path and SHA-256 content digest so that skill-generated badges and parent-child Asset Lineage remain intact even when files are moved or renamed inside the project.
   - Provide the **`asynx-asset-manager`** Asset Skill (CLI + Unified Local UI Workbench) that scans the current project workspace, categorizes all discovered Assets into **`image`**, **`audio`**, **`frame_sequence`**, **`video`**, and **`3d`**, highlights Assets created or modified by our Asset Skills, displays upstream parent and downstream child lineage chains/graphs, and allows users to directly invoke cross-modal editing and generation interfaces (such as image editing, image-to-frame-sequence, image-to-video, image-to-3D, video-to-frame-sequence, and 3D viewing/rigging/animating/rendering) with automatic parent-child lineage recording.

## User Stories

### Identity Service Authentication & Dual-Server Protocol

1. As an AI Agent or creator, I want to configure only a single Identity Service entry URL (defaulting to `http://localhost:3006` or overridden via environment/config), so that the skill automatically obtains both my authentication tokens and the dynamic Asset Gateway `baseUrl` without manual gateway configuration.
2. As an AI Agent, I want every request to the Asset Gateway to carry exclusively the `X-Asynx-Partner-Token: <token>` header and never include an `Authorization` header, so that the Asset Gateway never rejects requests with `401 ambiguous_credentials`.
3. As an AI Agent, I want responses from the Identity Service to be validated against HTTP `200` and numeric `code === 0` (`{ code: 0, data: ... }`), while responses from the Asset Gateway are validated against HTTP `200`/`202` and string `code === "ok"` (`{ code: "ok", data: ..., request_id: ... }`), so that protocol differences between the two servers are handled accurately.
4. As an AI Agent, I want the client to decode the cached JWT header and payload before making a gateway request and proactively call `POST /api/user/refresh` if `exp - now <= 60` seconds or if `kid`, `iss`, or `aud` no longer match the active `asynx` configuration, so that near-expiry or rotated tokens are refreshed before a request fails.
5. As an AI Agent, I want the client to sync against `GET /api/user/asynx-config` when validating cached credentials, so that changes to the Asset Gateway `baseUrl` or signing key parameters (`kid`, `issuer`, `audience`) are detected automatically.
6. As an AI Agent running a long Generation Task (such as video or 3D generation), I want the client to automatically call `POST /api/user/refresh` and retry the Asset Gateway request once if any submission, status poll, or asset download returns HTTP `401` (`invalid_partner_token`), so that tasks spanning a token expiration boundary complete without failing.
7. As an AI Agent, I want both the new `token` and the new `refreshToken` returned by `POST /api/user/refresh` or `POST /api/user/change-password` to atomically overwrite the local configuration file, so that rotated refresh tokens are never lost.
8. As an AI Agent, I want the skill to call `GET {baseUrl}/v1/partner/identity` upon login and before task submission, so that my partner identity is automatically provisioned on first use and my quota status (`active` vs. `quota_exhausted`), concurrency limit, active task count, and remaining USD budget are visible.
9. As an AI Agent, I want model discovery (`GET {baseUrl}/v1/tasks/models`) and per-model parameter guide lookup (`GET {baseUrl}/v1/tasks/models/{name}/guide`) as well as optional task cost quoting (`POST {baseUrl}/v1/tasks/quote`) to use the Partner Token session, so that I can inspect model capabilities and estimated charges before submitting tasks.
10. As an AI Agent, I want every Generation Task submission (`POST {baseUrl}/v1/tasks`) to include a unique ASCII `Idempotency-Key` header, treat HTTP `202` (`status: "queued"`) as accepted, poll `GET {baseUrl}/v1/tasks/{task_id}` until a terminal status (`succeeded`, `failed`, `timeout`, `canceled`, `blocked`) is reached, and surface `data.error.code` and `data.error.message` on abnormal termination, so that task execution is idempotent and observable.
11. As an AI Agent, I want large reference files that are inconvenient to inline as Data URLs to be uploadable via `multipart/form-data` to `POST {baseUrl}/v1/files` (handling its direct `{ id: "file_..." }` response without a `data` wrapper), so that oversized reference inputs can be referenced cleanly.
12. As an AI Agent, I want Asset downloads (`GET {baseUrl}/v1/tasks/{task_id}/assets/{index}`) to include `X-Asynx-Partner-Token: <token>` on the initial request and automatically follow HTTP `302` redirects to external object storage while stripping the `X-Asynx-Partner-Token` header on cross-origin redirect hops, so that private assets download reliably without leaking credentials to third-party storage domains.

### On-Demand Login Workbench & Account Management

13. As a creator or AI Agent, I want the Local UI Workbench to automatically open the Login page in my browser whenever I run an authenticated command and my local `token` and `refreshToken` are missing, expired, or rejected by `/api/user/refresh` (with codes such as `401`, `10`, `11`, `12`, or `15`), so that I am guided to log in immediately instead of hitting a dead-end CLI error.
14. As a creator, I want to be able to explicitly run a `login` command in any Asset Skill to open the Login Workbench at any time, so that I can sign in or switch accounts proactively.
15. As a creator on the Login Workbench page, I want to log in with my `username` and `password` (`POST /api/user/login`) and see clear, human-friendly error messages for invalid credentials (`1003`), locked accounts (`12`), or deleted accounts (`15`), so that I can authenticate quickly.
16. As a new creator on the Login Workbench page, I want to switch to a Registration tab, have my desired username (`^[a-z0-9_]{3,32}$`) checked in real time for availability (`GET /api/user/check-username`), enter a password (`6–64` characters) and optional nickname, and submit (`POST /api/user/register`), so that I can create an account and be logged in immediately without leaving the workbench.
17. As a creator on the Login Workbench page, I want to expand an advanced server setting to view or update the Identity Service URL (`LOGIN_SVR_URL`), so that I can connect to a custom or team-hosted `login_svr` instance when needed.
18. As an AI Agent or creator, I want an interrupted CLI command (such as `generate`, `edit`, `models`, or `doctor --verify`) that triggered the Login Workbench to automatically resume and finish executing using the newly saved credentials as soon as login succeeds in the browser, so that my workflow continues without re-running the command.
19. As an AI Agent running in a strictly headless environment (or when `--no-login-ui` is passed), I want the CLI to fail fast with a structured `need_login` JSON error when credentials are missing or unrefreshable, so that automated pipelines never hang waiting for a browser window.
20. As a creator using any Local UI Workbench, I want to see my logged-in nickname/username, remaining Asynx budget (`budget.remaining_amount`), and active/max task concurrency (`usage.active_tasks` / `api_key.concurrency_limit`) in the top navigation bar, and be able to change my password (`POST /api/user/change-password`), switch accounts, or log out from an account menu, so that I always know my account and quota state.

### Shared State Ledger v3 & Asset Lineage Tracking

21. As an existing Asset Skill user, I want my local SQLite state ledger (`SCHEMA_VERSION = 1` or `2`) to automatically and idempotently migrate to `SCHEMA_VERSION = 3` without losing any existing tasks, batches, or assets, so that upgrading is seamless.
22. As a creator or AI Agent, I want every Asset generated or modified by any cloud Generation Task (`image.generate` with reference images, `image.edit`, `audio.speech` with reference audio, `video.generate` from first/last frame or reference video, `video.edit`, `3d.generate` from images, `3d.rig`, `3d.retarget`, `3d.texture`, `3d.convert`) to automatically record its input source Assets as parents in the state ledger's Asset Lineage table, so that cloud derivation chains are preserved automatically.
23. As a creator or AI Agent, I want every Asset produced by deterministic Local Post-Processing CLI commands (`image convert|resize|crop|slice|contact-sheet|apply-mask|pad|trim|alpha-bleed|remove-background`, `audio convert|trim|fade|normalize|concat`, `video extract-frames|spritesheet|trim|convert|mux`, `model3d extract-textures`) and Local UI Workbench exports (`region_edit`, `image_tools`, `image_annotate`, `video_frame_sequence`, `model3d_viewer` sequence render) to be registered in the `assets` ledger together with its parent source Asset(s) and operation type, so that offline and interactive edits are tracked with the exact same lineage fidelity as cloud tasks.
24. As a creator or AI Agent, I want multi-input operations (such as combining multiple reference images into a generated image, concatenating multiple audio files with `audio concat`, or muxing a video file and an audio file with `video mux`) to record all contributing input Assets as parents of the resulting child Asset, so that multi-parent DAG lineage is accurately represented.
25. As a creator who reorganizes files inside my project folders, I want the Asset Manager scanner to match moved or renamed files against existing ledger Assets via their SHA-256 digest and automatically update their recorded path while preserving their "Generated/Modified by Asynx Skill" status and parent-child Asset Lineage links, so that tidying up project directories never breaks asset provenance.

### Asset Manager Skill (`asynx-asset-manager`) & Cross-Skill Workbench Invocation

26. As a creator or AI Agent, I want to install and invoke `asynx-asset-manager` as a standalone Asset Skill (built and packaged identically to `asynx-image-pro`, `asynx-audio`, `asynx-video`, and `asynx-3d`), so that any AI Agent can inspect project assets, query lineage, or launch the Asset Manager Workbench.
27. As a creator or AI Agent, I want `asynx-asset-manager` to scan my current project directory (automatically excluding non-asset engineering directories like `.git`, `node_modules`, `.venv`, `__pycache__`, `dist`, `.scratch`) and classify all discovered Assets into five distinct categories: **Image (`image`)**, **Audio (`audio`)**, **Frame Sequence (`frame_sequence`)**, **Video (`video`)**, and **3D Model (`3d`)**, so that I can browse project assets by category.
28. As a creator, I want Frame Sequence Atlases (an `atlas.png` / sprite sheet paired with a `manifest.json` containing frame coordinates, or exported sequence bundles) to be automatically recognized and grouped under the **`frame_sequence`** category rather than mixed into generic static images, so that 2D game animations are cleanly separated from static textures and illustrations.
29. As a creator browsing the Asset Manager Workbench, I want every Asset that was generated or modified by an Asynx Asset Skill to display a prominent visual badge (distinguishing AI-generated vs. skill-post-processed Assets, alongside its model/operation tag) and allow filtering between "All Project Assets", "Skill-Generated / Managed Assets Only", and "External / Unmanaged Assets", so that I can immediately identify which assets came from our skills.
30. As a creator inspecting any Asset in the Asset Manager Workbench, I want a detail and lineage inspector panel that shows its metadata (dimensions, duration, file size, format, generation prompt, model, operation, task ID) together with its **Upstream Parents** and **Downstream Children**, allowing me to click any parent or child node to jump directly to that Asset or switch to a visual **Lineage Tree / DAG view**, so that I can navigate the full derivation history of my creative pipeline.
31. As a creator selecting an **Image (`image`)** Asset in the Asset Manager Workbench, I want to directly invoke:
    - **Edit Image**: Open the interactive Image Region Edit Workbench (`region_edit`), Local Image Tools (`image_tools` for background removal, trim, pad, resize), Freehand Annotation (`image_annotate`), or prompt-based `image.edit`;
    - **Generate Frame Sequence**: Choose between **AI Sprite Sheet / Frame Sequence Generation** (generating an action sequence sheet from the source image and automatically slicing/packing it into a Frame Sequence Atlas `atlas.png` + `manifest.json`) or **Grid Slice to Frame Sequence** (slicing an existing sprite grid image into a Frame Sequence Atlas);
    - **Generate Video**: Open the Image-to-Video panel with the selected image pre-bound as `first_frame` (`video.generate`);
    - **Generate 3D Model**: Open the Image-to-3D panel with the selected image pre-bound as the reference image (`3d.generate` in `image_to_model` mode);
    so that an image can drive downstream editing, 2D animation, video, and 3D pipelines without leaving the Asset Manager.
32. As a creator selecting a **Frame Sequence (`frame_sequence`)** Asset in the Asset Manager Workbench, I want to preview the animated sprite loop interactively (controlling FPS, playback direction, onion-skin/grid bounds, and direction angles) or re-open the sequence editor, so that I can verify 2D game animations in place.
33. As a creator selecting a **Video (`video`)** Asset in the Asset Manager Workbench, I want to directly invoke the interactive **Video Frame Sequence Extractor** (`video_frame_sequence` for timeline scrubbing, chroma-key background removal, and `atlas.png` + `manifest.json` export), **Video Edit/Extend** (`video.edit`), or local video post-processing (`trim`, `convert`, `mux`), so that video assets can be turned into 2D game sprite atlases or refined in place.
34. As a creator selecting a **3D Model (`3d`)** Asset in the Asset Manager Workbench, I want to directly invoke the interactive **3D WebGL Inspector & Multi-Angle Frame Sequence Renderer** (`model3d_viewer` / `render-sequence` to export 4-way/8-way isometric or top-down 2D Frame Sequence Atlases), **Rig Check & Auto-Rig** (`3d.rig_check` / `3d.rig`), **Retarget Animation** (`3d.retarget`), **Re-Texture** (`3d.texture`), or **Format Conversion** (`3d.convert`), so that 3D models can be inspected, rigged, animated, and rendered into 2D sprite sheets from one interface.
35. As a creator selecting an **Audio (`audio`)** Asset in the Asset Manager Workbench, I want to play/preview the audio waveform, use it as a reference voice for **Speech Synthesis** (`audio.speech`), or run local audio post-processing (`trim`, `fade`, `normalize`, `convert`), so that audio assets can be auditioned and derived in place.
36. As a creator executing any editing or derivation action from the Asset Manager Workbench, I want the newly generated or modified Asset to be automatically saved to the project, linked to the source Asset(s) as its parent in the `SCHEMA_VERSION = 3` ledger, and immediately reflected in the Asset Manager UI with its new parent-child connection without restarting the workbench server.

## Implementation Decisions

### 1. Identity Service (`login_svr`) & Asset Gateway Dual-Protocol Client (ADR-0005)

- **Configuration Schema**:
  - Persist session state in the canonical user config file (`~/.config/asynx/config.json` or `ASYNX_CONFIG_PATH`):
    ```json
    {
      "login_svr_url": "http://localhost:3006",
      "token": "<EdDSA JWT>",
      "refreshToken": "<Refresh Token>",
      "asynx": {
        "baseUrl": "https://.../api",
        "tokenHeader": "X-Asynx-Partner-Token",
        "issuer": "login_svr",
        "audience": "asynx",
        "kid": "k1",
        "tokenExpiresIn": 86400
      },
      "user": {
        "id": "...",
        "username": "...",
        "nickname": "...",
        "locked": false,
        "deleted": false
      }
    }
    ```
  - Support environment overrides: `ASYNX_LOGIN_SVR_URL` (default `http://localhost:3006`), `ASYNX_PARTNER_TOKEN` (optional direct override for headless CI), and `ASYNX_BASE_URL` (optional gateway URL override).
- **Identity Service Endpoints & Envelope Contract**:
  - `GET /api/user/asynx-config` (unauthenticated)
  - `GET /api/user/check-username?username=<name>` (unauthenticated)
  - `POST /api/user/register` (`{ username, password, nickname }`)
  - `POST /api/user/login` (`{ username, password }`)
  - `POST /api/user/refresh` (`{ refreshToken }`)
  - `GET /api/user/profile` (`Authorization: Bearer <token>`)
  - `POST /api/user/change-password` (`Authorization: Bearer <token>`, `{ oldPassword, newPassword }`, overwrites stored `{ token, refreshToken, asynx, user }`)
  - All Identity Service responses return HTTP `200` with `{ "code": 0, "data": { ... } }` on success and `{ "code": <non-zero int>, "msg": "<reason>" }` on failure.
- **Token Validation & Two-Tier Auto-Refresh State Machine**:
  - **Pre-Flight Check**: Decode the base64url JWT header (segment 0) and payload (segment 1) of `token`. If `asynx.baseUrl` is missing, fetch `/api/user/asynx-config`. The token is considered usable only if:
    1. `header.kid == asynx.kid`
    2. `payload.iss == asynx.issuer`
    3. `payload.aud == asynx.audience`
    4. `int(payload.exp) - current_epoch_seconds > 60`
  - **Proactive Refresh**: If the token is unusable and `refreshToken` is present, call `POST /api/user/refresh` and atomically persist the new `{ token, refreshToken, asynx, user }`.
  - **Reactive Refresh on `401`**: If an Asset Gateway call returns HTTP `401` (`code === "invalid_partner_token"`), invoke `POST /api/user/refresh` once, persist the new credentials, and retry the exact Asset Gateway request once.
  - **Fallback to Login Workbench**: If `refreshToken` is missing or `/api/user/refresh` returns an auth/account failure (`code` in `{401, 10, 11, 12, 15}`), clear invalid tokens from config and trigger the Login Workbench (unless running with `--no-login-ui` or headless without browser capability, in which case raise `AsxError(code="need_login")`).
- **Asset Gateway Request & Download Contract**:
  - Authenticate exclusively with `{ [asynx.tokenHeader || "X-Asynx-Partner-Token"]: token }`. Never send `Authorization`.
  - Parse Asset Gateway JSON responses expecting HTTP `200` or `202` and `payload.get("code") == "ok"` (unwrapping `payload["data"]`), except for `POST /v1/files` which returns `{ "id": "file_...", ... }` directly at the top level.
  - Provision/check partner identity via `GET /v1/partner/identity`. If `api_key.status == "quota_exhausted"`, surface a clear `quota_exhausted` error before submitting new tasks.
  - On `GET /v1/tasks/{task_id}/assets/{index}`, include `X-Asynx-Partner-Token: <token>` on requests to the Asset Gateway origin; when following HTTP `301/302/303/307/308` redirects to a different origin (such as object storage), strip the partner token header on the redirected request.

### 2. Login Workbench & Top-Bar Account Session UI (ADR-0004, ADR-0005)

- **Login Workbench Mode (`mode="login"`)**:
  - Hosted by the Local UI Workbench server with dedicated local endpoints:
    - `GET /api/auth/status`: Returns current login status, `login_svr_url`, `user` profile, and `/v1/partner/identity` quota/concurrency summary.
    - `GET /api/auth/check-username?username=...`: Proxies username availability check to `login_svr`.
    - `POST /api/auth/login`: Authenticates `{ username, password, login_svr_url? }`, saves config, calls `/v1/partner/identity`, and (when running in standalone `login` blocking mode) signals the workbench server to complete and unblock the waiting CLI command.
    - `POST /api/auth/register`: Registers `{ username, password, nickname?, login_svr_url? }`, saves config, calls `/v1/partner/identity`, and unblocks the waiting CLI command.
    - `POST /api/auth/change-password`: Calls `/api/user/change-password` and updates stored tokens.
    - `POST /api/auth/logout`: Clears stored `token` and `refreshToken` from config.
- **Seamless Command Resumption**:
  - When `load_credentials()` / `get_authenticated_client()` detects missing or unrefreshable credentials during a CLI command execution, it invokes `launch_login_workbench()`, waits for the user to sign in or register in the browser, reloads the saved credentials, and returns the authenticated `AsynxClient` to the caller so the original CLI command proceeds transparently.

### 3. Shared State Ledger `SCHEMA_VERSION = 3` & Asset Lineage (ADR-0003, ADR-0006)

- **Schema v3 Migration**:
  - Upgrade SQLite `PRAGMA user_version` to `3` with idempotent migration from v1 and v2:
    ```sql
    ALTER TABLE assets ADD COLUMN category TEXT;
    ALTER TABLE assets ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'cloud_task';
    ALTER TABLE assets ADD COLUMN operation TEXT;
    ALTER TABLE assets ADD COLUMN metadata_json TEXT;

    CREATE TABLE IF NOT EXISTS asset_lineage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_artifact_id TEXT,
        parent_path TEXT NOT NULL,
        parent_sha256 TEXT,
        child_artifact_id TEXT NOT NULL REFERENCES assets(artifact_id) ON DELETE CASCADE,
        child_path TEXT NOT NULL,
        child_sha256 TEXT,
        operation TEXT NOT NULL,
        modality TEXT NOT NULL,
        task_id TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(parent_path, child_artifact_id, operation)
    );
    CREATE INDEX IF NOT EXISTS ix_lineage_child ON asset_lineage(child_artifact_id);
    CREATE INDEX IF NOT EXISTS ix_lineage_parent_id ON asset_lineage(parent_artifact_id);
    CREATE INDEX IF NOT EXISTS ix_lineage_parent_path ON asset_lineage(parent_path);
    CREATE INDEX IF NOT EXISTS ix_lineage_parent_sha ON asset_lineage(parent_sha256);
    ```
- **Universal Artifact & Lineage Registration**:
  - Provide a unified ledger helper `record_artifact_with_lineage(...)` called by:
    1. Cloud task downloads (`image`, `audio`, `video`, `3d`), extracting parent file paths or `--from-task` parent artifacts from the task submission context;
    2. Local Post-Processing CLI commands (`local_images`, `local_audio`, `local_video`, `local_model3d`), recording a synthetic local task/artifact entry (`source_kind = 'local_process'`) and linking the input file(s) as parent(s);
    3. Local UI Workbench submissions (`region_edit`, `image_tools`, `image_annotate`, `video_frame_sequence`, `model3d_viewer`, and `asset_manager` actions), linking the source Asset(s) to the exported Asset(s).
- **Move/Rename Resilience via SHA-256 Re-Linking**:
  - When scanning the project workspace, compute SHA-256 digests for discovered Asset files (caching by `(path, mtime_ns, size)` for fast incremental scans).
  - If a workspace file does not match an existing `assets.path` directly, but its `sha256` matches an existing `assets.sha256` whose recorded `path` no longer exists on disk, automatically update `assets.path` and corresponding `asset_lineage` path references to the new file location so neither the "Skill-Generated" badge nor the parent-child lineage chain is broken.

### 4. Asset Manager Skill (`asynx-asset-manager`) & Unified Workbench (ADR-0001, ADR-0004, ADR-0006)

- **Skill Packaging**:
  - Register `asset_manager` (`skill_name: "asynx-asset-manager"`) in the build and installer pipelines alongside `asynx-image-pro`, `asynx-audio`, `asynx-video`, and `asynx-3d`.
  - Provide CLI subcommands on `asynx-asset-manager`:
    - `scan` / `list`: Scan the project directory and emit the categorized JSON inventory with skill-generated flags and parent/child lineage summaries (`--category image|audio|frame_sequence|video|3d|all`, `--managed-only`, `--query`).
    - `lineage <path-or-artifact-id>`: Emit the full upstream ancestor chain and downstream descendant tree for a given Asset.
    - `ui` (or default workbench launch): Launch the Unified Asset Manager Workbench (`mode="asset_manager"`).
    - `login` / `logout` / `whoami`: Manage Identity Service authentication session.
- **Five-Category Classification Rules**:
  - **`frame_sequence`**: Any directory or file pair where a sprite sheet image (`atlas.png` or `*.png`/`*.webp`) is accompanied by a valid frame sequence manifest (`manifest.json` or `<stem>.manifest.json` containing `"frames"`), or any artifact recorded in the ledger with category `frame_sequence` / operation `spritesheet` / `frame_sequence` / `render_sequence`. The companion atlas image is grouped into the `frame_sequence` entry so it is not duplicated as a plain `image`.
  - **`image`**: `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tga` (excluding atlas images already claimed by a `frame_sequence`).
  - **`audio`**: `.mp3`, `.wav`, `.ogg`, `.m4a`, `.flac`, `.aac`.
  - **`video`**: `.mp4`, `.webm`, `.mov`, `.mkv`.
  - **`3d`**: `.glb`, `.gltf`, `.obj`, `.fbx`, `.usdz`.
- **Cross-Skill Action Dispatch in the Asset Manager Workbench**:
  - The Asset Manager Workbench server exposes endpoints to list/rescan project assets (`GET /api/assets`), inspect lineage (`GET /api/lineage`), stream any workspace asset safely (`GET /api/workspace-file?path=...`), and execute cross-modal actions (`POST /api/manager/action` and embedded workbench modes):
    - **From `image`**:
      1. *Edit Image*: Opens embedded Image Workbench (`region_edit`, `image_tools`, `image_annotate`) or submits `image.edit`;
      2. *Generate Frame Sequence*: Supports (a) AI Sprite Sheet Generation (`image.edit` / `image.generate` with sprite-grid prompt + automatic grid slicing and `atlas.png` + `manifest.json` generation) and (b) Grid Slice to Frame Sequence (local grid slicing into `atlas.png` + `manifest.json`);
      3. *Generate Video*: Submits `video.generate` with `first_frame` set to the selected image;
      4. *Generate 3D Model*: Submits `3d.generate` (`image_to_model`) with the selected image;
    - **From `frame_sequence`**: Interactive animated sprite preview (FPS, direction selector, frame scrubber) and re-extraction.
    - **From `video`**: Opens embedded Video Frame Sequence Extractor (`video_frame_sequence`), submits `video.edit`, or executes local `trim` / `convert` / `mux`.
    - **From `3d`**: Opens embedded 3D WebGL Inspector & Multi-Angle Frame Sequence Renderer (`model3d_viewer`), or executes `3d.rig_check`, `3d.rig`, `3d.retarget` (`animate`), `3d.texture`, `3d.convert`, and local `model3d info|validate|extract-textures`.
    - **From `audio`**: Waveform/audio player preview, Speech synthesis using the audio as `--reference-audio` (`audio.speech`), or local `trim` / `fade` / `normalize` / `convert`.
  - Every action executed from the Asset Manager automatically passes `parent_paths=[selected_asset_path, ...]` into `record_artifact_with_lineage(...)` so the resulting child Asset is immediately linked to its parent(s).

## Testing Decisions

### Principles of a Good Test

- Tests assert only observable external behavior: CLI exit codes, emitted JSON payloads on stdout, files created/modified on disk, SQLite state ledger rows (`tasks`, `assets`, `asset_lineage`), and HTTP requests exchanged with local loopback fake servers (`FakeLoginServer`, `FakeGatewayServer`, and `WorkbenchServer`).
- All tests run deterministically offline using temporary directories (`ASYNX_CONFIG_PATH`, `ASYNX_STATE_PATH`, `ASYNX_CACHE_PATH`) and local loopback HTTP servers.

### Modules and Seams Under Test

1. **Asset Skill CLI & Dual-Server Auth Seam (`unittest` suite)**:
   - **Identity Service + Asset Gateway Protocol**:
     - Login, registration, username availability check, and config persistence against a loopback `FakeLoginServer` (`code: 0` envelope) paired with a loopback `FakeGatewayServer` (`code: "ok"` envelope, HTTP `202` task creation, and strict rejection of any request containing an `Authorization` header alongside `X-Asynx-Partner-Token`).
     - Proactive token refresh when stored JWT `exp - now <= 60` or `kid`/`iss`/`aud` mismatch `/api/user/asynx-config`.
     - Reactive token refresh and transparent retry when `FakeGatewayServer` returns HTTP `401` (`invalid_partner_token`) during task submission or polling.
     - Cross-origin HTTP `302` asset download redirect verifying that `X-Asynx-Partner-Token` is sent to the gateway and stripped when redirected to external storage.
     - Partner identity provisioning call (`GET /v1/partner/identity`) and quota-exhausted guard.
   - **State Ledger v3 Migration & Asset Lineage**:
     - Automatic migration from `SCHEMA_VERSION = 1` and `SCHEMA_VERSION = 2` to `SCHEMA_VERSION = 3` preserving existing records.
     - Automatic parent-child `asset_lineage` recording for cloud tasks (`image.edit`, `--reference`, `--from-task` across image $\rightarrow$ video $\rightarrow$ frame sequence and image $\rightarrow$ 3D $\rightarrow$ rig $\rightarrow$ animate) and Local Post-Processing commands (`image crop/resize/remove-background`, `audio trim/concat`, `video spritesheet/mux`, `model3d extract-textures`).
   - **`asynx-asset-manager` CLI**:
     - Workspace scanning across all 5 categories (`image`, `audio`, `frame_sequence`, `video`, `3d`), distinguishing skill-generated Assets from unmanaged project files.
     - SHA-256 re-linking when a skill-generated Asset file is moved or renamed inside the workspace, verifying that both its managed badge and parent-child lineage remain intact.

2. **Local UI Workbench HTTP Seam (`WorkbenchServer` integration tests)**:
   - **Login Workbench Flow**:
     - Launching `WorkbenchServer` in `login` mode when credentials are missing or refresh fails, completing `/api/auth/register` or `/api/auth/login` over HTTP, verifying config persistence, and confirming that the blocked CLI operation automatically resumes and succeeds.
   - **Unified Asset Manager Workbench Flow**:
     - Querying `/api/assets` and `/api/lineage` with `X-Asx-Session-Token`.
     - Invoking cross-modal actions from a source image in the Asset Manager (`image.edit` / local tool edit, image-to-frame-sequence, image-to-video, image-to-3D) and from video/3D assets (frame sequence extraction/rendering), verifying that child files are created on disk and `asset_lineage` records link the source Asset as the parent of each derived Asset.

## Out of Scope

- Implementing or deploying the remote `login_svr` or `Asynx` server backends themselves (this repository implements the client-side Asset Skills, shared runtime library, and Local UI Workbenches).
- OAuth2 social login providers (GitHub/Google) not defined in `docs/client-ai-integration-guide.md`.
- Real-time collaborative cloud synchronization of project workspace files across multiple machines.

## Further Notes

- All five Asset Skills (`asynx-image-pro`, `asynx-audio`, `asynx-video`, `asynx-3d`, `asynx-asset-manager`) share the lockstep build and packaging pipeline (`src/build_skills.py` and `install.py`) so that running the build updates all bundled scripts and UI assets in lockstep.
