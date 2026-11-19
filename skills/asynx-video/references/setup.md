# Asynx Video 环境配置

## 授权
与所有 Asynx Skill 共享同一 Plaiers Agent 授权（需 `skill_jwt` 权限），经 `.plaiers-context.json` 记录的社区凭据桥接取得短期 JWT，不读取 PAT：
```bash
python3 "<skill-dir>/scripts/asynx.py" whoami
```
未授权或缺少 `.plaiers-context.json` 时返回 `need_login`：经 `plaiers-community` 用 `get_skill` 安装本 Skill，并确认授权勾选了 `skill_jwt`。

## 依赖诊断
视频处理依赖系统中的 `ffmpeg` 与 `ffprobe`：
```bash
python3 "<skill-dir>/scripts/asynx.py" doctor
```
安装建议：
- macOS: `brew install ffmpeg`
- Windows: `winget install Gyan.FFmpeg`
- Ubuntu / Debian: `sudo apt-get install ffmpeg`
