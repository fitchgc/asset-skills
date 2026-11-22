---
name: plaiers-asynx-video
description: 通过 Asynx 生成文生视频与图生视频，扩展编辑已有视频；支持本地视频抽帧、2D 序列帧图集 (Atlas) 导出、格式转换、修剪与音视频合成；支持浏览器时间轴抽帧去背交互工作台。
---

# Asynx 视频 (Video)

使用本 Skill 的 `scripts/asynx.py` 完成视频创作、跨模态图生视频与本地抽帧后处理。下文 `<skill-dir>` 替换为当前 Skill 的绝对路径；需要 Python 3.10+ 与 `ffmpeg`。

## 项目资产交接

先与用户确定项目根目录，把生成结果的输出目录设在其中。用本 Skill 的 `project budget --root "<项目根目录>" --currency USD --limit <金额>` 设置本身份的项目预算，省略上限参数可查逐任务预占与累计实扣。下载后向用户提供 `python3 "<skill-dir>/scripts/asynx.py" project open --root "<项目根目录>"`，在本机查看、挑选或编辑同项目素材；已打开时可复用该项目与身份的本地窗口。共享运行环境用 `runtime status` 检查，经同意后用 `runtime prepare` 准备。工作台按需打开。

## 积分消耗确认（付费确认）

生成任务会消耗**资产积分**（资产服务的充值额度；与社区原有积分体系、玩造币相互独立，不互转）。每次提交生成任务或批次之前都要先让用户确认预计消耗。**授权 `skill_jwt`、登录或安装本 Skill 都不代表用户同意消耗积分。** PAT、JWT 是访问凭据（令牌），不是积分。

1. 第一次运行生成命令时，不要带 `--confirmed-quote`。CLI 只报价，不提交任务，并返回 `confirmation_required`。`details` 里有本次任务数（`units`）、预计消耗（`total_amount`，单位 `currency`）和剩余积分（`budget`）。数值和单位按接口返回原样展示，不自行换算，也不要把模型的输入/输出 token 数当成积分。
2. 把模型、数量、预计消耗和剩余积分告诉用户，请用户确认**这一次**请求或批次。
3. 用户确认后，带上 `details.confirm_args`（如 `--confirmed-quote USD:0.12`）重新运行同一条命令。CLI 会重新报价。新报价高于用户确认的数值、单位变化或报价过期时，会再次返回 `confirmation_required`，这时要重新征求用户确认，不能自己改大金额。
4. 报价不可用时返回 `quote_unavailable`，不会提交任务。只有用户明确同意在没有报价的情况下提交，才可以加 `--confirm-without-quote`；这个决定会写入请求日志。
5. 报价为 0 时无需确认。

提交后如果出现失败、超时或网络错误，CLI 不会自动重新提交。按错误里的 `next_action_args`（`task recover <local_id>`）恢复，它会沿用同一幂等键。超时先查原任务，不重新提交；下载重试只会重新下载，不会重新生成或再扣积分。之前的确认不能用于其他请求。

任务记录、批次和积分确认只属于提交它们的身份（站点 + Agent + 账号），每个身份有自己的任务账本。`task recover`、`batch poll` 只会重新提交当前身份自己确认过的那一份请求。遇到 `ledger_owner_mismatch`、`ledger_owner_unknown`、`charge_consent_missing`、`charge_consent_mismatch` 或 `task_gateway_mismatch` 时不要换账本、换身份或绕过：把情况告诉用户，需要时重新发起请求并按新报价确认。旧版本留下的任务用 `ledger status` 查看，用户确认归属后才用 `ledger migrate --confirm-ownership` 迁入。

## 视频生成与编辑

```bash
# 文生视频 (Text-to-Video)
python3 "<skill-dir>/scripts/asynx.py" generate --prompt "赛博朋克雨夜街道，霓虹倒影，电影质感" --duration 5.0 --aspect-ratio "16:9"

# 图生视频 (Image-to-Video，使用本地图片作为首帧)
python3 "<skill-dir>/scripts/asynx.py" generate --prompt "让人物微笑着眨眼并转身" --image "/path/to/character.png"

# 跨模态图生视频 (直接使用 plaiers-asynx-image-pro 产出的任务 ID)
python3 "<skill-dir>/scripts/asynx.py" generate --prompt "镜头环绕角色旋转" --from-task "<image-task-id>"

# 首末帧控制生成
python3 "<skill-dir>/scripts/asynx.py" generate --prompt "从白天平滑过渡到黄昏" --image "start.png" --end-image "end.png"

# 视频编辑与扩展
python3 "<skill-dir>/scripts/asynx.py" edit --prompt "将背景天气改为大雪纷飞" --video "/path/to/input.mp4"
```

## 本地后处理 (Keyless Local Post-Processing)

> 本节命令由可选的**本地工作台包**（`plaiers-asynx-workbench`，技能库 ID `plaiers/asynx-workbench`）提供，本 Skill 只含生成、查询与下载。未安装时命令返回 `workbench_not_installed`，生成主流程不受影响；用户需要时，经同意后用 `get_skill` 获取并用 Plaiers 安装器装成同一版本（版本不一致返回 `workbench_version_mismatch`）。

```bash
# 查看视频元数据 (分辨率、时长、帧率、编码)
python3 "<skill-dir>/scripts/asynx.py" video info "clip.mp4"

# 抽取视频帧为图片序列
python3 "<skill-dir>/scripts/asynx.py" video extract-frames "clip.mp4" --output-dir "frames" --fps 12

# 抽帧并打包为 2D 序列帧图集 (atlas.png + manifest.json)
python3 "<skill-dir>/scripts/asynx.py" video spritesheet "clip.mp4" --output-dir "spritesheet" --fps 10 --width 128 --height 128

# 修剪片段
python3 "<skill-dir>/scripts/asynx.py" video trim "clip.mp4" --output "trimmed.mp4" --start 1.0 --duration 3.0

# 转换格式 (mp4, webm, gif)
python3 "<skill-dir>/scripts/asynx.py" video convert "clip.mp4" --output "animation.gif"

# 合成音视频轨道
python3 "<skill-dir>/scripts/asynx.py" video mux --video "clip.mp4" --audio "music.mp3" --output "final.mp4"
```

## 交互工作台 (Local UI Workbench)

> 同样由可选的本地工作台包提供，未安装时返回 `workbench_not_installed`。

```bash
# 时间轴抽帧、去背与图集导出工作台
python3 "<skill-dir>/scripts/asynx.py" ui frame-sequence --video "character.mp4"

# 交互式序列帧图集动画预览
python3 "<skill-dir>/scripts/asynx.py" ui preview-sequence --atlas "atlas.png" --manifest "manifest.json"
```

## 可选运行环境（Pillow）

生成、查询、下载不需要 Pillow。本地参考图、Mask、本地图片处理和图集拼合需要共享运行环境；缺失时命令在提交前返回 `dependency_missing`（或校验失败时返回 `runtime_invalid`），`details.prepare_command` 给出准备命令。

- `prepare` 会联网下载按哈希锁定的 Pillow，**运行前必须先征得用户同意**；不要自动运行，也不要改用 `pip install`。
- 所有 Asset Skill 共用同一份运行环境，已准备时再次 `prepare` 不会重复下载。
- `python3 scripts/asynx.py runtime status` 只读查看状态；`runtime remove` 删除当前平台的运行环境。
