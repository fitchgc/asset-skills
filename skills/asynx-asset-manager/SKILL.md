---
name: asynx-asset-manager
description: 扫描并管理项目工作区中的五类创作 Asset（image、audio、frame_sequence、video、3d），识别由 Asynx Asset Skill 生成或修改的受管 Asset 与外部本地文件，通过 SHA-256 自动重连移动或重命名后的文件并查询上下游 Asset Lineage（血缘关系），支持按需启动统一 Asset Manager Local UI Workbench。
---

# Asynx 资产管理器 (Asset Manager)

使用本 Skill 的 `scripts/asynx.py` 扫描项目工作区资产、追踪跨模态 Asset Lineage（血缘关系）或启动交互工作台。下文 `<skill-dir>` 替换为当前 Skill 的绝对路径；需要 Python 3.10+。`scan`、`list`、`lineage` 为离线操作，无需 API Key 或登录会话即可使用。

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

- 启动本地交互工作台展示项目资产与血缘关系；若宿主不支持自动打开浏览器，可加 `--no-open` 并向用户展示控制台输出的本地 URL。

## 按需读取

| 请求 | 参考 |
| --- | --- |
| 扫描过滤参数、五类分类规则、SHA-256 移动重连与血缘结构 | [操作参考](references/operations.md) |
| Identity Service 登录会话、配置状态、环境诊断与更新 | [配置与维护](references/setup.md) |

## 结果与边界

- 所有命令向标准输出打印结构化 JSON；向用户总结时使用自然语言概括分类数量、受管状态与上下游血缘，不直接转发原始 JSON。
- 不直接读写或手工修改 `~/.local/state/asx/state.db` 或 `~/.config/asynx/config.json`，统一通过脚本命令访问。
