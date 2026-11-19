# Asynx 3D 环境配置

## 授权
与 `asynx-image-pro` 等同族 Skill 共享同一 Plaiers Agent 授权（需 `skill_jwt` 权限），经 `.plaiers-context.json` 记录的社区凭据桥接取得短期 JWT，不读取 PAT：
```bash
python3 "<skill-dir>/scripts/asynx.py" whoami
```
未授权或缺少 `.plaiers-context.json` 时返回 `need_login`：经 `plaiers-community` 用 `get_skill` 安装本 Skill，并确认授权勾选了 `skill_jwt`。

## 离线解析能力
`model3d info`、`validate` 与 `extract-textures` 采用纯 Python 二进制解析器实现，不需要本地安装 Blender、Maya 或外部 C++ 库。
```bash
python3 "<skill-dir>/scripts/asynx.py" doctor
```
