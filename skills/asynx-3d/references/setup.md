# Asynx 3D 环境配置

## 凭证
与 `asynx-image` 等同族 Skill 共享相同的用户配置：
```bash
python3 "<skill-dir>/scripts/asynx.py" configure
```

## 离线解析能力
`model3d info`、`validate` 与 `extract-textures` 采用纯 Python 二进制解析器实现，不需要本地安装 Blender、Maya 或外部 C++ 库。
```bash
python3 "<skill-dir>/scripts/asynx.py" doctor
```
