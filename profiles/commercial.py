"""广告片 (commercial) profile.

特征: 短(15-60s), 强 CTA, 产品为核心(不是角色).
对策: 引入 product 阈值, 角色 identity 放宽, 必须支持 overlay (logo / 字幕).
"""
from __future__ import annotations

import math

from .base import Profile


_SYSTEM_PROMPT = """你是广告创意导演. 根据用户 prompt 生成结构化 shot list, 强调:
- 产品为核心: 至少 30% 的 shot 突出产品 (用 @product_id 引用资产库产品)
- CTA 结构: 末尾 1-2 shot 必须为 call-to-action (slogan / 购买引导)
- 节奏极快: 3-8 秒一 shot, 钩子在前 3 秒

返回严格 JSON: {global_style, characters, shots}.
shots[].cta 可为 null 或 {text, action}. shots[].product_id 引用产品资产."""


COMMERCIAL = Profile(
    profile_id="commercial",
    display_name="广告片 (commercial)",
    system_prompt=_SYSTEM_PROMPT,
    shot_count_formula=lambda d: max(1, math.ceil(d / 5.0)),
    single_shot_duration_range=(3.0, 8.0),
    extra_schema_fields=("product_id", "cta", "logo_overlay"),

    consistency_threshold_identity=0.50,   # 模特一致性不重要
    consistency_threshold_style=0.70,
    consistency_threshold_product=0.90,    # 产品外观必须严格一致

    keyframe_strategy="first_last",
    video_backend_preferred="sora2",
    video_backend_fallback=("seeddance2", "kling3"),

    audio_enabled=True,
    tts_backend="cosyvoice",
    lipsync_required=False,  # 广告片 voiceover 不需要口型同步

    critic_dimensions=frozenset({"product", "cta", "scene", "narrative"}),
    retry_budget=5,

    transition_smoother="concat",
    overlay_renderer="logo_cta",  # 启用 logo / 字幕叠加
    subtitle_burner=True,

    notes="广告片场景在 design/full.md 未单列, 这里基于电商短视频常见模式建模",
)
