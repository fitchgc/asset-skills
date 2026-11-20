# Asynx Audio 环境与配置

## 授权
JWT 只经 Plaiers 社区凭据桥接取得：安装器在本 Skill 根目录写入 `.plaiers-context.json`，记录 `get-skill-jwt.mjs` 的完整命令（需 `skill_jwt` 权限；或用 `ASYNX_PARTNER_TOKEN` 环境变量）。Skill 不读取 PAT，也不缓存 JWT：
```bash
python3 "<skill-dir>/scripts/asynx.py" whoami
```
未授权或缺少 `.plaiers-context.json` 时返回 `need_login`：经 `plaiers-community` 用 `get_skill` 安装本 Skill，并确认授权勾选了 `skill_jwt`。

## 依赖诊断
检查外部媒体工具 `ffmpeg` 与 `ffprobe` 状态：
```bash
python3 "<skill-dir>/scripts/asynx.py" doctor
```
未安装 ffmpeg 时：
- macOS: `brew install ffmpeg`
- Windows: `winget install Gyan.FFmpeg`
- Ubuntu / Debian: `sudo apt-get install ffmpeg`
