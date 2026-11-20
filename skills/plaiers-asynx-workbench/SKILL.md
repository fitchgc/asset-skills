---
name: plaiers-asynx-workbench
description: 扫描并管理项目工作区中的五类创作 Asset（image、audio、frame_sequence、video、3d），识别由 Asynx Asset Skill 生成或修改的受管 Asset 与外部本地文件，通过 SHA-256 自动重连移动或重命名后的文件并查询上下游 Asset Lineage（血缘关系），支持按需启动统一 Asset Manager Local UI Workbench。
---

# Asynx 资产管理器 (Asset Manager)

本 Skill 同时是 Asynx 的**本地工作台包**：图片、视频、3D 的浏览器工作台和各模态本地后处理只在这里打包一次，其他 Asynx Skill 按需从同一 Skills 目录加载，需与它们保持同一版本。

使用本 Skill 的 `scripts/asynx.py` 扫描项目工作区资产、追踪跨模态 Asset Lineage（血缘关系）或启动交互工作台。下文 `<skill-dir>` 替换为当前 Skill 的绝对路径；需要 Python 3.10+。`scan`、`list`、`lineage` 为离线操作，无需授权即可使用。

## 工作区扫描与分类检索

```bash
# 扫描当前项目工作区，输出五类创作资产统计与清单
python3 "<skill-dir>/scripts/asynx.py" scan --root "."

# list 与 scan 等价，可按类别、来源与关键词过滤
python3 "<skill-dir>/scripts/asynx.py" list --root "/path/to/project" --category frame_sequence
python3 "<skill-dir>/scripts/asynx.py" scan --root "/path/to/project" --managed-only
python3 "<skill-dir>/scripts/asynx.py" scan --root "/path/to/project" --external-only --query "knight"
```

- **五类标准资产**：`image`、`audio`、`frame_sequence`（Frame Sequence Atlas，即 `atlas.png` 等精灵大图与含 `"frames"` 的 `manifest.json` 配对，不会重复归入 `image`）、`video`、`3d`。
- **自动排除工程目录**：扫描会自动跳过 `.git`、`node_modules`、`.venv`、`__pycache__`、`dist`、`.scratch`、`.pytest_cache`、`.pi`、`venv`、`.mypy_cache`。
- **受管标记与来源**：在共享状态账本中登记的 Asset 会标记 `managed: true` 与 `source_kind`（`cloud_task`、`local_process`、`ui_export`），并附带 `model`、`operation`、`prompt`、`task_id`、`parents` 与 `children`；外部文件标记为 `managed: false`、`source_kind: "external"`。
- **移动/重命名自愈**：若由 Asynx Skill 生成或修改的 Asset 在项目内被移动或重命名，扫描时会自动通过 SHA-256 摘要匹配缺失路径记录，原子更新账本路径与 `asset_lineage` 引用，保持受管标记与父子血缘不断裂。

## 查询 Asset Lineage（血缘关系）

```bash
# 通过文件路径查询直接父节点、直接子节点与完整上下游血缘链
python3 "<skill-dir>/scripts/asynx.py" lineage "/path/to/project/sprites/atlas.png"

# 通过 artifact_id 查询
python3 "<skill-dir>/scripts/asynx.py" lineage "artifact_0123456789abcdef"
```

## 交互工作台 (Local UI Workbench)

```bash
python3 "<skill-dir>/scripts/asynx.py" ui --root "/path/to/project"
```

- 启动统一 Asset Manager Local UI Workbench，可在离线状态下浏览全部五类资产（`All` / `Image` / `Audio` / `Frame Sequence` / `Video` / `3D Model`）、按来源筛选（`All` / `Asynx Skill` / `External`）与搜索，并查看元数据、可点击上下游血缘卡片与交互式 DAG 血缘图谱。
- 在详情检查器中可直接调用跨模态工作流：
  - **Image**：局部重绘 (`region_edit`)、图片工具 (`image_tools`)、自由批注 (`image_annotate`)、Prompt AI 编辑 (`image.edit`)、AI 精灵图生成 / 确定性网格切分为 `frame_sequence`、图生视频 (`video.generate`)、图生 3D (`model.generate`)；
  - **Frame Sequence**：交互式序列帧动画播放器（FPS、方向、帧进度条、洋葱皮、网格）、解包单帧与重新切分打包；
  - **Video**：内嵌视频抽帧去背工作台 (`video_frame_sequence`)、`video.edit`、本地 `trim` / `convert` / `mux` / `spritesheet`；
  - **3D Model**：内嵌 WebGL 3D 检查与多视角序列帧渲染工作台 (`model3d_viewer` / `render-sequence`)、云端 `rig-check` / `rig` / `animate` / `convert` 与本地 `info` / `validate` / `extract-textures`；
  - **Audio**：波形播放器、参考音色语音合成 (`audio.speech`) 与本地 `trim` / `fade` / `normalize` / `convert`。
- 所有在工作台内创建或修改的资产均自动保存至项目工作区并记录父子 `asset_lineage`，无需重启服务即可实时刷新显示；顶部账号栏显示授权状态与额度/并发；云端操作需要 SageIn Agent 授权（`skill_jwt` 权限）。
- 若宿主不支持自动打开浏览器，可加 `--no-open` 并向用户展示控制台输出的本地 URL。

## 按需读取

| 请求 | 参考 |
| --- | --- |
| 扫描过滤参数、五类分类规则、SHA-256 移动重连与血缘结构 | [操作参考](references/operations.md) |
| SageIn Agent 授权、配置状态、环境诊断与更新 | [配置与维护](references/setup.md) |

## 结果与边界

- 所有命令向标准输出打印结构化 JSON；向用户总结时使用自然语言概括分类数量、受管状态与上下游血缘，不直接转发原始 JSON。
- 不直接读写或手工修改 `~/.local/state/asx/state.db` 或 `~/.config/asynx/config.json`，统一通过脚本命令访问。

## 可选运行环境（Pillow）

生成、查询、下载不需要 Pillow。本地参考图、Mask、本地图片处理和图集拼合需要共享运行环境；缺失时命令在提交前返回 `dependency_missing`（或校验失败时返回 `runtime_invalid`），`details.prepare_command` 给出准备命令。

- `prepare` 会联网下载按哈希锁定的 Pillow，**运行前必须先征得用户同意**；不要自动运行，也不要改用 `pip install`。
- 所有 Asset Skill 共用同一份运行环境，已准备时再次 `prepare` 不会重复下载。
- `python3 scripts/asynx.py runtime status` 只读查看状态；`runtime remove` 删除当前平台的运行环境。
