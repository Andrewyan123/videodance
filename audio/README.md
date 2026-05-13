# audio/ — 音频 + 口型同步

> Phase 5 (待实现)

## 职责

为短剧 / 电影 / 广告生成对白 + BGM + SFX 三轨,对话片段做口型对齐。

## 计划文件

| 文件 | 职责 |
|---|---|
| `tts.py` | TTSProvider Protocol + CosyVoiceClient + ElevenLabsClient |
| `lipsync.py` | LipSyncProvider Protocol + Wan22AnimateClient |
| `mixer.py` | 时间轴拼接、ducking、normalize |

## Acceptance

- 对白片段口型偏差 <100ms(基于 lip-distance metric)
- BGM 在对白处自动 ducking -6dB

## 注意

- TTS / LipSync 都是独立 provider Protocol(同 LLM/Image/Video 套路)
- 是否启用由 Profile.audio_enabled / lipsync_required 决定
- 动画默认不开音频,广告片只开 BGM + voiceover
