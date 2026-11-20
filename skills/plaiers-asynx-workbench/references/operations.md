# Asynx Asset Manager 操作参考

命令中的 `<skill-dir>` 替换为当前已安装 Skill 的绝对路径；文件路径建议使用绝对路径。

## 工作区扫描 (`scan` / `list`)

`scan` 与 `list` 为等价命令，扫描指定 `--root` 目录并结合状态账本（`SCHEMA_VERSION = 3`）输出统一 JSON 资产清单。

```bash
python3 "<skill-dir>/scripts/asynx.py" scan --root "/path/to/project"
python3 "<skill-dir>/scripts/asynx.py" list --root "/path/to/project" --category image --managed-only
python3 "<skill-dir>/scripts/asynx.py" list --root "/path/to/project" --source external --query "bgm"
```

### 参数说明

- `--root <dir>`：工作区根目录，默认当前目录 `.`。
- `--category <cat>`：按分类筛选，支持 `all`（默认）、`image`、`audio`、`frame_sequence`、`video`、`3d`。
- `--managed-only`：仅保留 `managed: true`（由 Asynx Asset Skill 通过 Generation Task、Local Post-Processing 或 Local UI Workbench 产出）的 Asset。
- `--external-only`：仅保留 `managed: false`（外部未受管文件）的 Asset。
- `--source <src>`：按来源筛选：`all`、`managed` 或 `external`。
- `--query <text>`：按文件名、相对路径、绝对路径、Prompt、模型名称、操作类型、`task_id` 或 `artifact_id` 进行大小写不敏感子串匹配。
- `--no-cache`：忽略 `(path, mtime_ns, size)` SHA-256 增量缓存并强制重新计算摘要。

### 五类资产分类规则

| 类别 | 识别规则 |
| --- | --- |
| `frame_sequence` | 与有效清单（`manifest.json` 或 `<stem>.manifest.json`，含 `"frames"` 字段）配对的精灵图（`meta.image`、`atlas.png` 或同目录 companion PNG/WebP），或在账本中记录为 `category == 'frame_sequence'` / `operation` 属于 `spritesheet`、`frame_sequence`、`render_sequence` 的资产。已归入 `frame_sequence` 的图集不会重复出现在 `image` 中。 |
| `image` | `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tga`（排除已配对为 `frame_sequence` 的图集）。 |
| `audio` | `.mp3`, `.wav`, `.ogg`, `.m4a`, `.flac`, `.aac`。 |
| `video` | `.mp4`, `.webm`, `.mov`, `.mkv`。 |
| `3d` | `.glb`, `.gltf`, `.obj`, `.fbx`, `.usdz`。 |

## SHA-256 移动与重命名重连

1. 扫描器使用 `(path, mtime_ns, size)` 缓存高效计算工作区文件 SHA-256 摘要。
2. 当磁盘上的文件未直接命中 `assets.path` 时，扫描器在账本中查找 `sha256` 相同且原记录路径在磁盘上已不存在的受管记录。
3. 命中后在事务内更新 `assets.path`、`asset_lineage.parent_path`、`asset_lineage.child_path`（以及配套 `manifest_path` 与 `tasks.output_dir`），确保受管标记与父子血缘链完整保留。
4. 若原路径文件仍存在于磁盘上（例如用户仅复制了一份副本），不会抢占原记录路径，副本将保持为外部文件。

## Asset Lineage 查询 (`lineage`)

```bash
python3 "<skill-dir>/scripts/asynx.py" lineage "/path/to/project/assets/hero.png"
python3 "<skill-dir>/scripts/asynx.py" lineage "artifact_0123456789abcdef" --root "/path/to/project"
```

返回字段包含：
- `asset`：目标受管资产元数据（`artifact_id`、`model`、`operation`、`prompt`、`source_kind`、`task_id` 等；若为外部文件则为 `null`）。
- `parents` / `children`：直接上游父边与直接下游子边。
- `ancestors` / `descendants`：完整 DAG 上游祖先边集合与下游后代边集合。

## 统一资产管理器工作台 (`ui`)

```bash
python3 "<skill-dir>/scripts/asynx.py" ui --root "/path/to/project" [--no-open]
```

- **离线浏览与懒加载授权**：启动时无需预先登录，可直接浏览工作区五类资产、检查详情与血缘；当触发云端生成/编辑操作时，若本机尚无授权或凭据上下文会返回 `need_login`，需经 `plaiers-community` 授权（`skill_jwt` 权限）并用 `get_skill` 安装后重试。
- **核心工作台 HTTP 接口**（均需 `X-Asx-Session-Token` 或 `?token=`）：
  - `GET /api/assets`：按 `category`、`source`、`managed_only`、`external_only`、`query`、`no_cache` 实时扫描工作区。
  - `GET /api/lineage`：查询目标资产的上下游血缘卡片与 DAG 边集合。
  - `GET /api/workspace-file?path=...`：在严格限制于 `--root` 目录内安全流式读取工作区媒体文件（越界返回 `403`）。
  - `POST /api/manager/action`：调度跨模态本地处理与云端生成任务并自动写入 `asset_lineage`。
  - 内嵌子工作台模式（`/image/index.html`、`/video/index.html`、`/3d/index.html` 携带 `?embedded=1&source_path=...`）：保持主工作台会话存活，提交后实时回写账本并刷新资产列表与血缘图谱。
