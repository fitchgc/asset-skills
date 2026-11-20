# Asynx 3D 操作参考

## generate（model.generate）
- `--prompt` / `--negative-prompt`: 文字生成（prompt 1-1024 字符）。
- `--image`: 单张参考图（本地路径或 URL）；与 prompt/negative-prompt 互斥。
- `--from-task`: 引用 `plaiers-asynx-image-pro` 的图片任务产物作为参考图。
- `--face-limit`: 面数上限 1-2000000，是预算而非精确目标。
- `--no-texture` / `--no-pbr`: 关闭贴图 / PBR（无贴图时不能有 PBR）。
- 只输出 GLB；其他格式用 `convert`。

## 来源
`--from-task` 填成功的 Task ID；`rig-check` / `rig` / `convert` 也可用 `--model-file` 上传本地 GLB/FBX/OBJ/STL（purpose=model，≤150MB，不接受 .gltf）。`animate` 不接受文件，需先 rig。Asynx 生成且未改动的文件会自动按其 Task 作为来源。

## rig-check（model.rig_check）
- `--from-task`: 已成功的生成任务。结论在 `data`：`riggable`、`recommended_rig_type`。`riggable=false` 仍是成功任务。

## rig（model.rig）
- `--from-task`: 已成功的生成任务。
- `--rig-type`: `biped`、`quadruped`、`hexapod`、`octopod`、`avian`、`serpentine`、`aquatic`、`others`、`auto`（由服务决定）。
- `--skeleton-profile`: `tripo|mixamo|actorcore|unity|unreal|vrm`，仅 biped 或 auto。
- `--output-format`: `glb`（默认）或 `fbx`。

## animate（model.retarget）
- `--from-task`: 已成功的 rig 任务。
- `--animation`: 单个动画标识，如 `walk`（自动补为 `preset:walk`）；四足用 `preset:quadruped:walk`。多个动画需分别提交；不支持的标识任务会 failed（不计费）。
- `--output-format`: `glb` 或 `fbx`。

## convert（model.convert）
- `--from-task`: 已成功的生成、rig 或 animate 任务（转换结果不能再作为来源）。
- `--output-format`: `fbx|gltf|obj|usdz|stl`，只能一种。`obj` 交付 zip 压缩包（不解包）。
