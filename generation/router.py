"""Video backend router: profile + shot metadata → 选 backend + fallback chain.

设计:
1. profile.video_backend_preferred 用"产品名"(`kling3` / `sora2` / `wanx-i2v-plus` ...)
2. 这里映射到 providers 工厂的 factory name(`wanx_i2v_plus` / `seeddance` / `jimeng` / `dashscope_i2v`)
3. 若 shot 有 last_frame_url 但选中 backend 不支持双帧,自动升级到支持的
4. 调用 provider 失败 → 按 profile.video_backend_fallback 列表降级,记 warn 不破 pipeline

Phase 3.2 现状:
  - 真正实装: dashscope_i2v (单帧), wanx_i2v_plus (双帧)
  - Stub-with-shape: seeddance, jimeng (volcengine, 等 ARK_API_KEY)
  - 未接: kling3, sora2, wan2.2-anime-lora → 自动 fallback 到 wanx_i2v_plus
"""
from __future__ import annotations

import logging

from profiles.base import Profile
from providers import VideoResult, build_video_provider

log = logging.getLogger("video_pipeline.router")


# profile 产品名 → providers 工厂内部 name
PROFILE_TO_FACTORY: dict[str, str] = {
    # 真接的
    "wanx-i2v-turbo": "dashscope_i2v",
    "wanx-i2v-plus": "wanx_i2v_plus",
    # stub-with-shape (volcengine,等 ARK_API_KEY 后这两条会真接)
    "seeddance2": "seeddance",
    "seeddance": "seeddance",
    "jimeng": "jimeng",
    # 未接,fallback 到 wanx-i2v-plus (双帧能力最近的开放替代品)
    "kling3": "wanx_i2v_plus",
    "sora2": "wanx_i2v_plus",
    "wan2.2-anime-lora": "wanx_i2v_plus",
}


# 支持 last_frame_url 的 factory names (双帧 i2v)
_BACKENDS_WITH_LAST_FRAME = {"wanx_i2v_plus", "seeddance", "jimeng"}


def resolve_backend(profile_pref: str, *, has_last_frame: bool) -> str:
    """profile 产品名 → factory name. has_last_frame=True 时强制升级到双帧 backend."""
    factory_name = PROFILE_TO_FACTORY.get(profile_pref)
    if factory_name is None:
        log.warning("router: unknown backend %r in profile, defaulting to dashscope_i2v",
                    profile_pref)
        factory_name = "dashscope_i2v"

    if has_last_frame and factory_name not in _BACKENDS_WITH_LAST_FRAME:
        log.info("router: shot has last_frame but %s doesn't support it, upgrading to wanx_i2v_plus",
                 factory_name)
        return "wanx_i2v_plus"
    return factory_name


async def generate_video(
    *,
    profile: Profile,
    prompt: str,
    first_frame_url: str,
    last_frame_url: str | None,
    duration_sec: float,
    seed: int | None = None,            # Phase 4.1: critic-driven retry 传 seed
    force_backend: str | None = None,   # Phase 4.1: critic 升级 backend 时覆盖 profile 偏好
) -> tuple[VideoResult, str]:
    """按 profile 偏好 + fallback 链选 backend 调 i2v.

    Args:
        profile: 当前 run 的 Profile (video_backend_preferred + video_backend_fallback)
        first_frame_url: 必填 (i2v 至少要个起始帧)
        last_frame_url: 可选; 若提供, 自动选支持双帧的 backend
        seed: 可选, 透传到 backend (deterministic retry)
        force_backend: 可选, profile-style name; 覆盖 preferred, 优先在 chain 最前

    Returns:
        (VideoResult, factory_name_used)  — Phase 4.1: 也返回真正用了哪个 backend, 便于落库

    Raises:
        RuntimeError: 所有 backend (preferred + fallback) 都失败
    """
    # 把 force_backend 插在 chain 最前
    if force_backend:
        candidates = [force_backend, profile.video_backend_preferred,
                      *profile.video_backend_fallback]
    else:
        candidates = [profile.video_backend_preferred, *profile.video_backend_fallback]

    has_last = bool(last_frame_url)
    seen_factory: set[str] = set()
    last_err: Exception | None = None

    for pref in candidates:
        factory_name = resolve_backend(pref, has_last_frame=has_last)
        if factory_name in seen_factory:
            continue
        seen_factory.add(factory_name)

        try:
            provider = build_video_provider(factory_name)
        except ValueError as e:
            log.warning("router: build %s failed: %s", factory_name, e)
            last_err = e
            continue

        try:
            log.info("router: trying %s (from profile pref=%s, seed=%s)",
                     factory_name, pref, seed)
            result = await provider.generate(
                prompt=prompt,
                first_frame_url=first_frame_url,
                duration_sec=duration_sec,
                last_frame_url=last_frame_url,
                seed=seed,
            )
            return result, factory_name
        except (NotImplementedError, RuntimeError) as e:
            log.warning("router: backend %s failed: %s — falling back",
                        factory_name, str(e)[:200])
            last_err = e
            continue

    raise RuntimeError(
        f"router: all backends failed. profile chain={candidates}. last error: {last_err}"
    )
