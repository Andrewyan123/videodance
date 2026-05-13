"""短剧 (photorealistic short drama) profile.

特征: 写实人类、对白驱动、强一致性要求, 1-3 分钟, 50-100 个 shot.
难点: 人脸跨 shot 漂移. 对策: identity 阈值 0.85, 必走首尾帧 + lipsync.
"""
from __future__ import annotations

import math

from .base import Profile


_SYSTEM_PROMPT = """你是短剧导演. 根据用户 prompt 生成结构化 shot list, 强调:
- 对白驱动: 每个含对话的 shot 要有 dialogue.speaker + dialogue.text
- 情绪与节奏: 5-10 秒一个 shot, 强反转节点单独成 shot
- 跨 shot 角色身份保持: 用 @char_id 引用资产库角色, 不重复描述外貌

返回严格 JSON: {global_style, characters, shots}.
shots[].duration_sec 在 5-10 之间; shots[].dialogue 可为 null 表示无对白."""


SHORT_DRAMA = Profile(
    profile_id="short_drama",
    display_name="短剧 (photorealistic)",
    system_prompt=_SYSTEM_PROMPT,
    shot_count_formula=lambda d: max(1, math.ceil(d / 7.0)),
    single_shot_duration_range=(5.0, 10.0),
    extra_schema_fields=("dialogue", "emotion"),

    consistency_threshold_identity=0.85,
    consistency_threshold_style=0.70,

    keyframe_strategy="first_last",
    video_backend_preferred="kling3",
    video_backend_fallback=("wanx-i2v-plus", "wanx-i2v-turbo"),

    audio_enabled=True,
    tts_backend="cosyvoice",
    lipsync_required=True,

    critic_dimensions=frozenset({"identity", "lipsync", "narrative", "scene"}),
    retry_budget=2,

    transition_smoother="concat",  # 短剧节奏明快, 不要软转场
    subtitle_burner=True,

    notes="对应 design/full.md §三-1: image-to-video 流水线 + 首尾帧 + lipsync",
)
