# Asynx 3D 环境配置

## 授权
与 `asynx-image-pro` 等同族 Skill 共享同一 SageIn Agent 授权（需 `skill_jwt` 权限），自动换取短期 JWT：
```bash
python3 "<skill-dir>/scripts/asynx.py" whoami
```
未授权时使用 `sagein-community` 技能完成授权。

## 离线解析能力
`model3d info`、`validate` 与 `extract-textures` 采用纯 Python 二进制解析器实现，不需要本地安装 Blender、Maya 或外部 C++ 库。
```bash
python3 "<skill-dir>/scripts/asynx.py" doctor
```
