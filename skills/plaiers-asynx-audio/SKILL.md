---
name: plaiers-asynx-audio
description: 通过 Asynx 生成音效 (sfx)、背景音乐 (music) 与角色语音 (speech)，管理音频任务批次与进度；支持本地音频格式转换、修剪、淡入淡出、响度标准化与拼接。
---

# Asynx 音频 (Audio)

使用本 Skill 的 `scripts/asynx.py` 完成音频创作与处理操作。下文 `<skill-dir>` 替换为当前 Skill 的绝对路径；需要 Python 3.10+ 与 `ffmpeg`。

## 付费确认

每次提交付费的生成任务或批次之前都要先确认费用。**授权 `skill_jwt`、登录或安装本 Skill 都不代表用户同意付费。**

1. 第一次运行生成命令时，不要带 `--confirmed-quote`。CLI 只报价，不提交任务，并返回 `confirmation_required`。`details` 里有本次任务数（`units`）、币种、总价（`total_amount`）和剩余额度（`budget`）。
2. 把模型、数量、总价和剩余额度告诉用户，请用户确认**这一次**请求或批次。
3. 用户确认后，带上 `details.confirm_args`（如 `--confirmed-quote USD:0.12`）重新运行同一条命令。CLI 会重新报价。新报价高于用户确认的金额、币种变化或报价过期时，会再次返回 `confirmation_required`，这时要重新征求用户确认，不能自己改大金额。
4. 报价不可用时返回 `quote_unavailable`，不会提交任务。只有用户明确同意在没有报价的情况下提交，才可以加 `--confirm-without-quote`；这个决定会写入请求日志。
5. 报价为 0 时无需确认。

提交后如果出现失败、超时或网络错误，CLI 不会自动重新提交。按错误里的 `next_action_args`（`task recover <local_id>`）恢复，它会沿用同一幂等键。下载重试只会重新下载，不会再收费。之前确认过的费用不能用于其他请求。

## 创作与合成

```bash
# 生成音效 (sfx)
python3 "<skill-dir>/scripts/asynx.py" sfx --prompt "宝箱开启金色闪光声效" --duration 2.5

# 生成背景音乐 (music)
python3 "<skill-dir>/scripts/asynx.py" music --prompt "8-bit 像素地牢战斗芯片音乐，快节奏" --duration 30 --instrumental

# 合成台词与角色语音 (speech)
python3 "<skill-dir>/scripts/asynx.py" speech --text "勇士，欢迎来到艾泽拉斯！" --voice "warrior" --speed 1.0

# 使用之前任务产出的参考音频进行声音克隆/合成
python3 "<skill-dir>/scripts/asynx.py" speech --text "前进吧！" --from-task "<task-id>"
```

## 本地后处理 (Keyless Local Post-Processing)

> 本节命令由可选的**本地工作台包**（`plaiers-asynx-workbench`，技能库 ID `plaiers/asynx-workbench`）提供，本 Skill 只含生成、查询与下载。未安装时命令返回 `workbench_not_installed`，生成主流程不受影响；用户需要时，经同意后用 `get_skill` 获取并用 Plaiers 安装器装成同一版本（版本不一致返回 `workbench_version_mismatch`）。

无需授权 即可在本地确定性处理音频文件：

```bash
# 查看音频元数据
python3 "<skill-dir>/scripts/asynx.py" audio info "sound.wav"

# 转换格式 (wav / mp3 / ogg / m4a / flac)
python3 "<skill-dir>/scripts/asynx.py" audio convert "input.wav" --output "output.mp3"

# 修剪片段
python3 "<skill-dir>/scripts/asynx.py" audio trim "input.mp3" --output "trimmed.mp3" --start 1.0 --duration 3.5

# 淡入淡出包络
python3 "<skill-dir>/scripts/asynx.py" audio fade "input.mp3" --output "faded.mp3" --fade-in 0.5 --fade-out 1.0

# 响度标准化 (EBU R128)
python3 "<skill-dir>/scripts/asynx.py" audio normalize "input.mp3" --output "normalized.mp3" --target-lufs -14.0

# 拼接多个音频
python3 "<skill-dir>/scripts/asynx.py" audio concat "intro.mp3" "loop.mp3" "outro.mp3" --output "full_track.mp3"
```

## 查询任务与产物

```bash
python3 "<skill-dir>/scripts/asynx.py" recent --latest
python3 "<skill-dir>/scripts/asynx.py" task list
python3 "<skill-dir>/scripts/asynx.py" task status "<task-id>" --refresh
python3 "<skill-dir>/scripts/asynx.py" wait "<task-id>"
```

## 可选运行环境（Pillow）

本 Skill 的功能不需要 Pillow。`runtime status|prepare|remove` 管理所有 Asset Skill 共用的可选运行环境，只在其他 Skill 需要、且用户同意后才运行 `prepare`。
