# 3D 模型说明

查询当前可用 3D 模型：
```bash
python3 "<skill-dir>/scripts/asynx.py" models
```

主要模型能力：
- `3d.generate`: 文生 3D、图生 3D。
- `3d.rig_check` / `3d.rig`: 拓扑结构检测与骨骼权重蒙皮绑定。
- `3d.retarget`: 角色骨骼动画重定向。
- `3d.texture`: 表面纹理绘制与烘焙。
