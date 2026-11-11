# Asynx 3D 操作参考

## 3D 生成核心参数
- `--prompt`: 模型外形、结构、材质与细节描述。
- `--image`: 单张正面或概念参考图。
- `--reference`: 多视角参考图（可重复传入）。
- `--from-task`: 跨模态引用任务（例如引用 `asynx-image-pro` 的概念图产物）。
- `--face-limit`: 控制输出模型三角面数（如 10000, 25000），适合移动端或实时游戏优化。
- `--pbr`: 是否生成标准 PBR 材质（Base Color, Metallic, Roughness, Normal）。
- `--output-format`: `glb` (推荐), `fbx`, `obj`, `usdz`。

## 角色骨骼与动画
- `rig --rig-type`: 骨骼结构模板：
  - `biped`: 人形双足
  - `quadruped`: 四足动物
  - `hexapod`: 六足昆虫
  - `octopod`: 八足生物
  - `serpentine`: 蛇形游走
  - `aquatic`: 水生鱼类
  - `auto`: 自动识别
- `animate`:
  - `--animation`: 单个预设动作（如 `idle`, `walk`, `run`, `jump`, `slash`, `cast_spell`）。
  - `--animations`: 组合多个动作导出到同一模型。
  - `--animate-in-place`: 烘焙原地循环位移动作，方便游戏角色移动控制器驱动。
