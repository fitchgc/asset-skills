# Asynx Audio 操作参考

详细命令参数与高级工作流参考：

## 音效生成 (sfx)
- `--prompt`: 音效意图，支持具体材质、环境与动作（例如金属碰撞、魔法火球、UI 确认音）。
- `--duration`: 期望音效时长（秒）。
- `--output-format`: `mp3`、`wav` 或 `ogg`。

## 音乐生成 (music)
- `--prompt`: 曲风、配器、调性、BPM与场景情绪描述。
- `--lyrics`: 自定义歌词；省略时由模型生成或器乐处理。
- `--instrumental`: 纯器乐无演唱。
- `--duration`: 音频长度。

## 语音合成 (speech)
- `--text`: 需朗读的文本或剧本台词。
- `--voice`: 指定预设音色。
- `--reference-audio`: 目标音色参考音频，支持音色克隆。
- `--from-task`: 引用前序任务的音频产物。
- `--speed`: 语速调节（0.5 - 2.0）。
- `--emotion`: 指定情感语调。

## 本地音频处理 (audio)
完全离线执行，不消耗 API Key 或网络流量：
- `audio info`: 读取采样率、声道、比特率、格式与振幅统计。
- `audio convert`: 基于 ffmpeg 进行多格式快速重采样与编码。
- `audio trim`: 截取有效声音区间。
- `audio fade`: 消除音频起止咔哒杂音或制作无缝渐变。
- `audio normalize`: 按照工业标准 (-14 LUFS) 统一多条素材音量。
- `audio concat`: 批量拼接多个音频轨道。
