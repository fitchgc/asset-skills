# 音频模型说明

可以通过 `models` 命令查询当前网关支持的音频模型列表：
```bash
python3 "<skill-dir>/scripts/asynx.py" models
```

常见模型能力：
- `audio.sfx`: 音效生成模型（如 AudioCraft SFX），擅长简短游戏与多媒体交互拟音。
- `audio.music`: 音乐作曲模型（如 Suno / Udio），支持器乐与多语言歌词歌曲创作。
- `audio.speech`: 语音合成与声音克隆模型（如 ElevenLabs），支持富有表现力的台词配音。
