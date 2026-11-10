---
name: asynx-audio
description: 通过 Asynx 生成音效 (sfx)、背景音乐 (music) 与角色语音 (speech)，管理音频任务批次与进度；支持本地音频格式转换、修剪、淡入淡出、响度标准化与拼接。
---

# Asynx 音频 (Audio)

使用本 Skill 的 `scripts/asynx.py` 完成音频创作与处理操作。下文 `<skill-dir>` 替换为当前 Skill 的绝对路径；需要 Python 3.10+ 与 `ffmpeg`。

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

无需 API Key 即可在本地确定性处理音频文件：

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
