---
name: asynx-3d
description: 通过 Asynx 生成 3D 资产、检查可绑定性 (rig-check)、自动绑定骨骼 (rig)、重定向动画 (animate)、重新贴图 (texture) 与格式转换；支持本地纯 Python 3D 模型元数据检查、规范校验与 PBR 贴图提取；支持浏览器 WebGL 3D 检查与多角度序列帧渲染工作台。
---

# Asynx 3D (3D Models & Animation)

使用本 Skill 的 `scripts/asynx.py` 完成 3D 模型生成、角色骨骼绑定、动画重定向与纯 Python 离线模型后处理。下文 `<skill-dir>` 替换为当前 Skill 的绝对路径；纯 Python 检查无需安装 Blender。

## 3D 生成与动画管线

```bash
# 文生 3D (Text-to-3D)
python3 "<skill-dir>/scripts/asynx.py" generate --prompt "中世纪骑士双手大剑，带有符文雕刻，PBR 材质" --output-format glb

# 图生 3D (Image-to-3D)
python3 "<skill-dir>/scripts/asynx.py" generate --image "/path/to/character.png" --face-limit 15000

# 跨模态图生 3D (使用 asynx-image 产出的概念图任务 ID)
python3 "<skill-dir>/scripts/asynx.py" generate --from-task "<image-task-id>"

# 骨骼绑定前置检查
python3 "<skill-dir>/scripts/asynx.py" rig-check --model-file "character.glb"

# 自动骨骼绑定与蒙皮
python3 "<skill-dir>/scripts/asynx.py" rig --from-task "<gen-task-id>" --rig-type biped

# 动画重定向 (让角色执行预设动作)
python3 "<skill-dir>/scripts/asynx.py" animate --from-task "<rig-task-id>" --animation "walk" --animate-in-place

# 重新生成表面材质与贴图
python3 "<skill-dir>/scripts/asynx.py" texture --model-file "mesh.glb" --prompt "赛博朋克风，哑光碳纤维与霓虹发光线条"
```

## 本地纯 Python 后处理 (Keyless Local Post-Processing)

无需 API Key，无需安装 Blender 或第三方 DCC 软件：

```bash
# 检查模型元数据 (顶点数、三角面数、包围盒尺寸、材质槽、内嵌贴图、骨骼关节、动画剪辑)
python3 "<skill-dir>/scripts/asynx.py" model3d info "character.glb"

# 校验模型规范 (游戏引擎多边形面数限制、必须包含 PBR 材质、动画限制等)
python3 "<skill-dir>/scripts/asynx.py" model3d validate "character.glb" --max-faces 20000 --require-pbr --require-animations

# 从 GLB 文件中直接解包提取内嵌 PBR 贴图至磁盘
python3 "<skill-dir>/scripts/asynx.py" model3d extract-textures "character.glb" --output-dir "textures"
```

## 交互工作台 (Local UI Workbench)

```bash
# WebGL 3D 模型轨道旋转、动画播放与 PBR 通道检查工作台
python3 "<skill-dir>/scripts/asynx.py" ui viewer --model-file "character.glb"

# 多方向 (1向侧视/4向/8向正交俯视与等轴测) 渲染 2D 序列帧图集工作台
python3 "<skill-dir>/scripts/asynx.py" ui render-sequence --model-file "character.glb"
```
