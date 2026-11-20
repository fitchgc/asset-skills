---
name: plaiers-asynx-3d
description: 通过 Asynx 生成 3D 资产、检查可绑定性 (rig-check)、自动绑定骨骼 (rig)、套用预设动画 (animate) 与格式转换；支持本地纯 Python 3D 模型元数据检查、规范校验与 PBR 贴图提取；支持浏览器 WebGL 3D 检查与多角度序列帧渲染工作台。
---

# Asynx 3D (3D Models & Animation)

使用本 Skill 的 `scripts/asynx.py` 完成 3D 模型生成、角色骨骼绑定、动画重定向与纯 Python 离线模型后处理。下文 `<skill-dir>` 替换为当前 Skill 的绝对路径；纯 Python 检查无需安装 Blender。

## 积分消耗确认（付费确认）

生成任务会消耗**积分**（资产服务的充值额度；与社区原有积分体系、玩造币相互独立，不互转）。每次提交生成任务或批次之前都要先让用户确认预计消耗。**授权 `skill_jwt`、登录或安装本 Skill 都不代表用户同意消耗积分。** PAT、JWT 是访问凭据（令牌），不是积分。

1. 第一次运行生成命令时，不要带 `--confirmed-quote`。CLI 只报价，不提交任务，并返回 `confirmation_required`。`details` 里有本次任务数（`units`）、预计消耗（`total_amount`，单位 `currency`）和剩余积分（`budget`）。数值和单位按接口返回原样展示，不自行换算，也不要把模型的输入/输出 token 数当成积分。
2. 把模型、数量、预计消耗和剩余积分告诉用户，请用户确认**这一次**请求或批次。
3. 用户确认后，带上 `details.confirm_args`（如 `--confirmed-quote USD:0.12`）重新运行同一条命令。CLI 会重新报价。新报价高于用户确认的数值、单位变化或报价过期时，会再次返回 `confirmation_required`，这时要重新征求用户确认，不能自己改大金额。
4. 报价不可用时返回 `quote_unavailable`，不会提交任务。只有用户明确同意在没有报价的情况下提交，才可以加 `--confirm-without-quote`；这个决定会写入请求日志。
5. 报价为 0 时无需确认。

提交后如果出现失败、超时或网络错误，CLI 不会自动重新提交。按错误里的 `next_action_args`（`task recover <local_id>`）恢复，它会沿用同一幂等键。超时先查原任务，不重新提交；下载重试只会重新下载，不会重新生成或再扣积分。之前的确认不能用于其他请求。

任务记录、批次和积分确认只属于提交它们的身份（站点 + Agent + 账号），每个身份有自己的任务账本。`task recover`、`batch poll` 只会重新提交当前身份自己确认过的那一份请求。遇到 `ledger_owner_mismatch`、`ledger_owner_unknown`、`charge_consent_missing`、`charge_consent_mismatch` 或 `task_gateway_mismatch` 时不要换账本、换身份或绕过：把情况告诉用户，需要时重新发起请求并按新报价确认。旧版本留下的任务用 `ledger status` 查看，用户确认归属后才用 `ledger migrate --confirm-ownership` 迁入。

## 3D 生成与动画管线

```bash
# 文生 3D (Text-to-3D)
python3 "<skill-dir>/scripts/asynx.py" generate --prompt "中世纪骑士双手大剑，带有符文雕刻，PBR 材质" --output-format glb

# 图生 3D (Image-to-3D，只接受一张图，不能同时填 --prompt)
python3 "<skill-dir>/scripts/asynx.py" generate --image "/path/to/character.png" --face-limit 15000

# 跨模态图生 3D (使用 plaiers-asynx-image-pro 产出的概念图任务 ID)
python3 "<skill-dir>/scripts/asynx.py" generate --from-task "<image-task-id>"

# 已有本地模型 (GLB/FBX/OBJ/STL，≤150MB) 可用 --model-file 上传为 file_id，适用于 rig-check / rig / convert
python3 "<skill-dir>/scripts/asynx.py" rig --model-file "robot.glb"

# 绑定检查 (只返回 data.riggable / recommended_rig_type，不交付文件，当前不收费)
python3 "<skill-dir>/scripts/asynx.py" rig-check --from-task "<gen-task-id>"

# 自动骨骼绑定 (来源：生成任务)
python3 "<skill-dir>/scripts/asynx.py" rig --from-task "<gen-task-id>" --rig-type biped --skeleton-profile mixamo

# 套用一个预设动画 (来源：绑定任务；每个任务只能一个动画)
python3 "<skill-dir>/scripts/asynx.py" animate --from-task "<rig-task-id>" --animation "walk"

# 格式转换 (来源：生成/绑定/动画任务；fbx|gltf|obj|usdz|stl)
python3 "<skill-dir>/scripts/asynx.py" convert --from-task "<task-id>" --output-format fbx
```

流程：生成 → (绑定检查) → 骨骼绑定 → 动画 → 格式转换，每步是独立计费的 Task，来源必须是当前账户已成功且文件仍在保留期内的 Task。动画只接受 rig 任务（本地模型先 rig 再 animate）；不支持 gltf 文件上传与 texture（重新贴图）。

## 本地纯 Python 后处理 (Keyless Local Post-Processing)

> 本节命令由可选的**本地工作台包**（`plaiers-asynx-workbench`，技能库 ID `plaiers/asynx-workbench`）提供，本 Skill 只含生成、查询与下载。未安装时命令返回 `workbench_not_installed`，生成主流程不受影响；用户需要时，经同意后用 `get_skill` 获取并用 Plaiers 安装器装成同一版本（版本不一致返回 `workbench_version_mismatch`）。

无需授权，无需安装 Blender 或第三方 DCC 软件：

```bash
# 检查模型元数据 (顶点数、三角面数、包围盒尺寸、材质槽、内嵌贴图、骨骼关节、动画剪辑)
python3 "<skill-dir>/scripts/asynx.py" model3d info "character.glb"

# 校验模型规范 (游戏引擎多边形面数限制、必须包含 PBR 材质、动画限制等)
python3 "<skill-dir>/scripts/asynx.py" model3d validate "character.glb" --max-faces 20000 --require-pbr --require-animations

# 从 GLB 文件中直接解包提取内嵌 PBR 贴图至磁盘
python3 "<skill-dir>/scripts/asynx.py" model3d extract-textures "character.glb" --output-dir "textures"
```

## 交互工作台 (Local UI Workbench)

> 同样由可选的本地工作台包提供，未安装时返回 `workbench_not_installed`。

```bash
# WebGL 3D 模型轨道旋转、动画播放与 PBR 通道检查工作台
python3 "<skill-dir>/scripts/asynx.py" ui viewer --model-file "character.glb"

# 多方向 (1向侧视/4向/8向正交俯视与等轴测) 渲染 2D 序列帧图集工作台
python3 "<skill-dir>/scripts/asynx.py" ui render-sequence --model-file "character.glb"
```

## 可选运行环境（Pillow）

生成、查询、下载不需要 Pillow。本地参考图、Mask、本地图片处理和图集拼合需要共享运行环境；缺失时命令在提交前返回 `dependency_missing`（或校验失败时返回 `runtime_invalid`），`details.prepare_command` 给出准备命令。

- `prepare` 会联网下载按哈希锁定的 Pillow，**运行前必须先征得用户同意**；不要自动运行，也不要改用 `pip install`。
- 所有 Asset Skill 共用同一份运行环境，已准备时再次 `prepare` 不会重复下载。
- `python3 scripts/asynx.py runtime status` 只读查看状态；`runtime remove` 删除当前平台的运行环境。
