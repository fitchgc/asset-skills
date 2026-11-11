---
name: asynx-video
description: 通过 Asynx 生成文生视频与图生视频，扩展编辑已有视频；支持本地视频抽帧、2D 序列帧图集 (Atlas) 导出、格式转换、修剪与音视频合成；支持浏览器时间轴抽帧去背交互工作台。
---

# Asynx 视频 (Video)

使用本 Skill 的 `scripts/asynx.py` 完成视频创作、跨模态图生视频与本地抽帧后处理。下文 `<skill-dir>` 替换为当前 Skill 的绝对路径；需要 Python 3.10+ 与 `ffmpeg`。

## 视频生成与编辑

```bash
# 文生视频 (Text-to-Video)
python3 "<skill-dir>/scripts/asynx.py" generate --prompt "赛博朋克雨夜街道，霓虹倒影，电影质感" --duration 5.0 --aspect-ratio "16:9"

# 图生视频 (Image-to-Video，使用本地图片作为首帧)
python3 "<skill-dir>/scripts/asynx.py" generate --prompt "让人物微笑着眨眼并转身" --image "/path/to/character.png"

# 跨模态图生视频 (直接使用 asynx-image-pro 产出的任务 ID)
python3 "<skill-dir>/scripts/asynx.py" generate --prompt "镜头环绕角色旋转" --from-task "<image-task-id>"

# 首末帧控制生成
python3 "<skill-dir>/scripts/asynx.py" generate --prompt "从白天平滑过渡到黄昏" --image "start.png" --end-image "end.png"

# 视频编辑与扩展
python3 "<skill-dir>/scripts/asynx.py" edit --prompt "将背景天气改为大雪纷飞" --video "/path/to/input.mp4"
```

## 本地后处理 (Keyless Local Post-Processing)

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

```bash
# 时间轴抽帧、去背与图集导出工作台
python3 "<skill-dir>/scripts/asynx.py" ui frame-sequence --video "character.mp4"

# 交互式序列帧图集动画预览
python3 "<skill-dir>/scripts/asynx.py" ui preview-sequence --atlas "atlas.png" --manifest "manifest.json"
```
