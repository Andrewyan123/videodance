"""Profile dataclass + 默认值.

Profile 是流派级配置. 每类视频(短剧/动画/电影/广告)对应一个 Profile,
集中决定 planner / generation / critic / post 的差异化行为.

Profile 只是配置 —— 不导入任何模块类,用字符串引用 backend ID / strategy name,
避免循环依赖、保持可序列化.

加新流派 = 继承 Profile 重写需要的字段; 见 short_drama.py 等.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Literal


KeyframeStrategy = Literal["single", "first_last", "n_grid"]
TransitionSmoother = Literal["concat", "rife", "crossfade"]


@dataclass(frozen=True)
class Profile:
    # --- 标识 ---
    profile_id: str
    display_name: str

    # --- Planner ---
    system_prompt: str = (
        "你是电影导演. 根据用户 prompt 生成结构化 shot list. "
        "返回严格 JSON: {global_style, characters, shots}. 每个 shot 5-8 秒."
    )
    shot_count_formula: Callable[[float], int] = lambda d: max(1, math.ceil(d / 6.0))
    single_shot_duration_range: tuple[float, float] = (5.0, 8.0)
    extra_schema_fields: tuple[str, ...] = ()  # 占位, Phase 2 改成 Pydantic Field

    # --- Assets ---
    consistency_threshold_identity: float = 0.85
    consistency_threshold_style: float = 0.70
    consistency_threshold_product: float | None = None
    required_views: tuple[str, ...] = ("front", "side", "3-quarter")

    # --- Generation ---
    keyframe_strategy: KeyframeStrategy = "single"
    video_backend_preferred: str = "wanx-i2v-turbo"
    video_backend_fallback: tuple[str, ...] = ()
    image_backend_preferred: str = "wanx2.1-t2i-turbo"

    # --- Audio ---
    audio_enabled: bool = False
    tts_backend: str = "cosyvoice"
    lipsync_required: bool = False

    # --- Critic ---
    critic_dimensions: frozenset[str] = frozenset({"identity", "scene", "narrative", "vsa"})
    retry_budget: int = 2
    escalation_policy: tuple[str, ...] = ("seed", "backend", "human")

    # --- Post ---
    transition_smoother: TransitionSmoother = "concat"
    overlay_renderer: str | None = None
    subtitle_burner: bool = False

    # --- Meta ---
    notes: str = ""
