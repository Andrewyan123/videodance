"""漫剧 / 动画 (stylized) profile.

特征: 高风格容差、N 宫格分镜可直出, 一致性靠风格而非人脸.
对策: identity 阈值放宽到 0.6, 重点保 style; 优先用 anime LoRA / Wan2.2.
"""
from __future__ import annotations

import math

from .base import Profile


_SYSTEM_PROMPT = """你是漫剧导演. 根据用户 prompt 生成结构化 shot list, 强调:
- 风格延续: global_style 写清楚画风 (例如 "新海诚式逆光、薄雾、青春治愈")
- 场景为先: 角色外貌描述简化, 重点描绘场景氛围
- 节奏: 3-8 秒一个 shot, 动作戏可短

返回严格 JSON: {global_style, characters, shots}."""


ANIME = Profile(
    profile_id="anime",
    display_name="漫剧 / 动画 (stylized)",
    system_prompt=_SYSTEM_PROMPT,
    shot_count_formula=lambda d: max(1, math.ceil(d / 5.0)),
    single_shot_duration_range=(3.0, 8.0),
    extra_schema_fields=("style_ref",),

    consistency_threshold_identity=0.60,  # 卡通脸跨 shot 难达 0.85, 容忍
    consistency_threshold_style=0.85,     # 但风格要严

    keyframe_strategy="single",           # N 宫格分镜直出也可, MVP 走 single
    video_backend_preferred="wan2.2-anime-lora",
    video_backend_fallback=("wanx-i2v-plus", "wanx-i2v-turbo"),

    audio_enabled=False,                  # 动画默认无对白, 用户按需开
    lipsync_required=False,

    critic_dimensions=frozenset({"style", "scene", "narrative"}),
    retry_budget=1,                       # 风格容差大, 一次过就行

    transition_smoother="concat",

    notes="对应 design/full.md §三-2: N 宫格分镜 + anime LoRA",
)
