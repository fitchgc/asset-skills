# Asynx Video 环境配置

## 凭证
与所有 Asynx Skill 共享同一份用户凭据：
```bash
python3 "<skill-dir>/scripts/asynx.py" configure
```

## 依赖诊断
视频处理依赖系统中的 `ffmpeg` 与 `ffprobe`：
```bash
python3 "<skill-dir>/scripts/asynx.py" doctor
```
安装建议：
- macOS: `brew install ffmpeg`
- Windows: `winget install Gyan.FFmpeg`
- Ubuntu / Debian: `sudo apt-get install ffmpeg`
