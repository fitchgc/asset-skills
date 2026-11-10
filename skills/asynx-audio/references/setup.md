# Asynx Audio 环境与配置

## 凭证
与 `asynx-image` 共享相同的 API 凭证与用户配置（`~/.config/asynx/config.json` 或 `ASYNX_API_KEY` 环境变量）。配置一次即可多模态通用：
```bash
python3 "<skill-dir>/scripts/asynx.py" configure
```

## 依赖诊断
检查外部媒体工具 `ffmpeg` 与 `ffprobe` 状态：
```bash
python3 "<skill-dir>/scripts/asynx.py" doctor
```
未安装 ffmpeg 时：
- macOS: `brew install ffmpeg`
- Windows: `winget install Gyan.FFmpeg`
- Ubuntu / Debian: `sudo apt-get install ffmpeg`
