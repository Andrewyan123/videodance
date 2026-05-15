"""Critic retry policy.

策略走 profile.escalation_policy 的顺序(默认 `("seed", "backend", "human")`),
配合 profile.retry_budget 兜底。

时序:
  attempt 0 (initial):  默认 seed + profile.video_backend_preferred
  critic 评分 < threshold → decide_next(retry_count=0) → 走 escalation[0]
  attempt 1: 用 escalation[0] 决策 (e.g. seed → 新 seed)
  critic 仍失败 → decide_next(retry_count=1) → 走 escalation[1]
  attempt 2: 用 escalation[1] 决策 (e.g. backend → fallback backend)
  critic 仍失败 → retry_count >= budget → fail
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


Action = Literal["pass", "retry_seed", "retry_backend", "fail"]


@dataclass
class CriticDecision:
    action: Action
    reason: str
    next_seed: Optional[int] = None
    next_backend: Optional[str] = None  # profile-style name (router 解析)


def _seed_for_retry(retry_count: int) -> int:
    """根据 retry_count 推一个稳定的新 seed (deterministic but different per attempt)."""
    return 42 + (retry_count + 1) * 1000


def decide_next(
    *,
    score: Optional[float],
    threshold: float,
    retry_count: int,  # 已完成的 retry 次数 (0 = 初次失败后第一次决策)
    profile_escalation: tuple[str, ...],
    profile_retry_budget: int,
    profile_video_backend_fallback: tuple[str, ...],
) -> CriticDecision:
    """根据当前 score / 已用 retry 数,决定下一动作.

    Args:
        score: 当前评分 (None = 评分失败, 如 no face detected)
        threshold: profile.consistency_threshold_identity
        retry_count: 已经做过的 retry 次数 (初次评 = 0)
        profile_escalation: profile.escalation_policy ("seed", "backend", "human")
        profile_retry_budget: profile.retry_budget
        profile_video_backend_fallback: profile.video_backend_fallback

    Returns:
        CriticDecision (action 决定 graph 下一节点)
    """
    # 1. 过线 → pass
    if score is not None and score >= threshold:
        return CriticDecision(
            action="pass",
            reason=f"identity {score:.3f} >= threshold {threshold:.3f}",
        )

    score_str = f"{score:.3f}" if score is not None else "None"

    # 2. budget 用尽 → fail
    if retry_count >= profile_retry_budget:
        return CriticDecision(
            action="fail",
            reason=f"score {score_str} < {threshold:.3f}; "
                   f"retry budget {profile_retry_budget} exhausted",
        )

    # 3. 取 escalation 下一步策略
    if not profile_escalation:
        return CriticDecision(
            action="fail",
            reason="empty escalation_policy in profile",
        )
    strategy_idx = min(retry_count, len(profile_escalation) - 1)
    strategy = profile_escalation[strategy_idx]

    if strategy == "seed":
        new_seed = _seed_for_retry(retry_count)
        return CriticDecision(
            action="retry_seed",
            reason=f"score {score_str} < {threshold:.3f}; "
                   f"strategy=seed (escalation[{strategy_idx}]), new_seed={new_seed}",
            next_seed=new_seed,
        )

    if strategy == "backend":
        if not profile_video_backend_fallback:
            return CriticDecision(
                action="fail",
                reason=f"score {score_str} < {threshold:.3f}; "
                       "strategy=backend but no fallback chain in profile",
            )
        # 简化: 取 fallback chain 第一个 (不动态 traverse fallback,够用就行)
        next_backend = profile_video_backend_fallback[0]
        return CriticDecision(
            action="retry_backend",
            reason=f"score {score_str} < {threshold:.3f}; "
                   f"strategy=backend (escalation[{strategy_idx}]) → {next_backend}",
            next_backend=next_backend,
        )

    if strategy == "human":
        return CriticDecision(
            action="fail",
            reason=f"score {score_str} < {threshold:.3f}; "
                   f"escalation[{strategy_idx}]=human gate (UI 未接入, mark fail)",
        )

    return CriticDecision(
        action="fail",
        reason=f"unknown escalation strategy {strategy!r}",
    )
