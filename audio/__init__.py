"""音频 + 口型同步.

Phase 5 (待实现):
  - tts.py:     TTS provider 抽象 (CosyVoice / ElevenLabs / 火山引擎)
  - lipsync.py: Wan2.2-Animate 口型对齐 (stub initially)
  - mixer.py:   多 track 时间轴混音 (对白 + BGM + SFX)

设计原则: dual-stream MMDiT 是趋势, 但 MVP 先用 TTS + Wan2.2-Animate 后期对齐.
"""
