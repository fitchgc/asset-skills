# 3D 模型说明

查询当前可用 3D 模型：
```bash
python3 "<skill-dir>/scripts/asynx.py" models
```

当前由 Tripo 提供，均按次计价，提交时必须带 `model`（CLI 自动从目录选取）：
- `tripo-h3.1`: `model.generate`，文生 / 图生 3D，输出 GLB。
- `tripo-rig-check`: `model.rig_check`，绑定检查，只返回结论。
- `tripo-rig`: `model.rig`，骨骼绑定，GLB 或 FBX。
- `tripo-animate`: `model.retarget`，预设动画，GLB 或 FBX。
- `tripo-convert`: `model.convert`，格式转换。
