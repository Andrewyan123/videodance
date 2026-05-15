"""Pydantic models for candidate variants + interactive session state.

设计:
- Session 是 interactive 模式的根; 一个 thread_id 一个 Session
- CharacterCandidate / ShotCandidate 用 (thread_id, char_id|shot_id, variant_index) 三元组定位
- variant_index 从 0 起,propose 续编号(同 char 已有 4 个,再 propose 2 个 → 编号 4, 5)
- selected: 同一 (thread_id, char_id) 下最多 1 行 selected=True
"""
from __future__ import annotations

import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class Session(BaseModel):
    thread_id: str
    user_prompt: str
    target_duration_sec: float
    profile_id: str
    storyboard_json: str  # 整 Storyboard 的 model_dump_json
    status: Literal["planning", "proposing", "stitching", "done", "failed"] = "planning"
    final_video_url: Optional[str] = None
    total_cost_usd: float = 0.0
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class CharacterCandidate(BaseModel):
    """单个角色的候选(一次 propose 出 N 张就是 N 行)."""
    id: Optional[int] = None  # SQLite rowid, insert 时由 store 填
    thread_id: str
    char_id: str
    variant_index: int  # 0-indexed

    # 来源
    description_used: str  # 实际喂给 t2i 的 prompt (可能有微调 / seed)
    seed: Optional[int] = None

    # 产物
    ref_image_url: str  # file:// 本地 (Phase 1.2 builder 落盘)
    embedding: Optional[list[float]] = None  # ArcFace 512-d
    consistency_score: Optional[float] = None  # builder 内的 cross-view 一致性

    # 选中
    selected: bool = False
    cost_usd: float = 0.0

    created_at: str = Field(default_factory=_now_iso)


class ShotCandidate(BaseModel):
    """单个 shot 的视频候选."""
    id: Optional[int] = None
    thread_id: str
    shot_id: str
    variant_index: int

    # i2v 输入
    first_keyframe_url: Optional[str] = None
    last_keyframe_url: Optional[str] = None
    seed: Optional[int] = None
    backend_used: Optional[str] = None  # router 选出来的 factory name

    # i2v 产物
    video_url: str
    identity_score: Optional[float] = None  # critic identity 分

    # 选中
    selected: bool = False
    cost_usd: float = 0.0

    error: Optional[str] = None  # 失败的 candidate 也存,err message
    created_at: str = Field(default_factory=_now_iso)
