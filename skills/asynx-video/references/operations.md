# Asynx Video 操作参考

## 视频生成参数
- `--prompt`: 镜头运镜、光影与动态描述。
- `--image`: 首帧输入（图生视频核心）。
- `--end-image`: 末帧关键帧控制。
- `--from-task`: 跨模态引用任务结果（例如 `asynx-image-pro` 任务 ID）。
- `--duration`: 生成秒数（如 5s, 10s）。
- `--resolution`: `720p`, `1080p`, `4k`。
- `--aspect-ratio`: `16:9`, `9:16`, `1:1` 等。
- `--fps`: `24`, `30`, `60`。
- `--generate-audio`: 同时生成音效/配乐。

## 序列帧图集 (Frame Sequence Atlas)
`video spritesheet` 与 `ui frame-sequence` 导出工业标准 2D 游戏动画包：
- `atlas.png`: 紧凑拼合的网格大图。
- `manifest.json`: 记录每一帧（`frame-0000.png` ...）的坐标位置、原始尺寸与帧播放时长。
