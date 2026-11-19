# Asynx Audio 环境与配置

## 授权
与 `asynx-image-pro` 共享同一授权（`sagein-community` 的 SageIn Agent 授权，需 `skill_jwt` 权限；或 `ASYNX_PARTNER_TOKEN` 环境变量）。运行时自动用 PAT 换取短期 JWT：
```bash
python3 "<skill-dir>/scripts/asynx.py" whoami
```
未授权时使用 `sagein-community` 技能完成授权。

## 依赖诊断
检查外部媒体工具 `ffmpeg` 与 `ffprobe` 状态：
```bash
python3 "<skill-dir>/scripts/asynx.py" doctor
```
未安装 ffmpeg 时：
- macOS: `brew install ffmpeg`
- Windows: `winget install Gyan.FFmpeg`
- Ubuntu / Debian: `sudo apt-get install ffmpeg`
