# Asynx Audio 环境与配置

## 登录
与 `asynx-image-pro` 共享同一登录会话（`~/.config/asynx/config.json` 或 `ASYNX_PARTNER_TOKEN` 环境变量）。登录一次即可多模态通用，由用户在浏览器中完成：
```bash
python3 "<skill-dir>/scripts/asynx.py" login
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
