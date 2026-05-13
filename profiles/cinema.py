"""电影 (cinematic) profile.

特征: 长片(5-90 分钟)、高质量、复杂运镜、完整音轨.
对策: 双严格 (identity + style 都 0.85), 多 backend fallback, RIFE 转场.
"""
from __future__ import annotations

import math

from .base import Profile


_SYSTEM_PROMPT = """你是电影导演. 根据用户 prompt 生成结构化 shot list, 强调:
- 镜头语言: 每 shot 必须有明确的 shot_type (close_up/medium/wide/extreme_wide) 和 camera (angle/movement)
- 节奏: 5-15 秒一个 shot, 关键情节用 medium close-up + slow dolly
- 灯光与氛围: global_style 写明色温、光照风格、胶片质感

返回严格 JSON: {global_style, characters, shots}."""


CINEMA = Profile(
    profile_id="cinema",
    display_name="电影 (cinematic)",
    system_prompt=_SYSTEM_PROMPT,
    shot_count_formula=lambda d: max(1, math.ceil(d / 10.0)),
    single_shot_duration_range=(5.0, 15.0),
    extra_schema_fields=("camera_movement", "lighting", "shot_type"),

    consistency_threshold_identity=0.85,
    consistency_threshold_style=0.85,

    keyframe_strategy="first_last",
    video_backend_preferred="sora2",
    video_backend_fallback=("kling3", "wanx-i2v-plus"),

    audio_enabled=True,
    tts_backend="elevenlabs",  # 电影对配音质量要求更高
    lipsync_required=True,

    critic_dimensions=frozenset({"identity", "scene", "narrative", "vsa", "lipsync"}),
    retry_budget=5,  # 画质要求高, 允许多次重试

    transition_smoother="rife",  # 电影需要平滑转场
    subtitle_burner=False,        # 用单独 .srt 文件, 不烧录

    notes="对应 design/full.md §一: 七阶段标准流水线, 用 Sora 2 物理真实感",
)
