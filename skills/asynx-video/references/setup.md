# Asynx Video 环境配置

## 登录
与所有 Asynx Skill 共享同一登录会话（Identity Service 签发的 Partner Token）：
```bash
python3 "<skill-dir>/scripts/asynx.py" login
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
