# Asynx Video 环境配置

## 授权
与所有 Asynx Skill 共享同一 SageIn Agent 授权（需 `skill_jwt` 权限），自动换取短期 JWT：
```bash
python3 "<skill-dir>/scripts/asynx.py" whoami
```
未授权时使用 `sagein-community` 技能完成授权。

## 依赖诊断
视频处理依赖系统中的 `ffmpeg` 与 `ffprobe`：
```bash
python3 "<skill-dir>/scripts/asynx.py" doctor
```
安装建议：
- macOS: `brew install ffmpeg`
- Windows: `winget install Gyan.FFmpeg`
- Ubuntu / Debian: `sudo apt-get install ffmpeg`
