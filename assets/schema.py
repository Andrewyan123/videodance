"""资产库 schema.

Phase 1 MVP 只做 CharacterCard. Scene / Product 在后续 phase 加.

设计:
- char_id 是稳定的引用键 (用于 storyboard 里的 @char_id)
- ref_image_urls 是按 required_views 顺序的本地文件路径 (file://...)
- embedding 可空 (Phase 1.2 接 InsightFace 后再填)
- 时间字段用 ISO 8601 字符串, 跨语言 / 序列化友好
"""
from __future__ import annotations

import datetime
from typing import Optional

from pydantic import BaseModel, Field


SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class CharacterCard(BaseModel):
    """单个角色的完整档案. 跨 shot 一致性的唯一参考源."""

    model_config = {"frozen": False}  # 允许就地更新 ref_image_urls / embedding

    # --- 标识 ---
    char_id: str = Field(..., description="稳定 ID, e.g. 'char_yan_01'")
    name: str = Field(..., description="人类可读名, 用于 prompt 拼装")

    # --- 描述 ---
    description: str = Field(
        ...,
        description="自然语言外貌描述, T2I prompt 的基础",
    )
    style_tokens: list[str] = Field(
        default_factory=list,
        description='固定风格 token, e.g. ["短发", "丹凤眼", "黑色西装"]',
    )

    # --- 参考资产 ---
    ref_image_urls: list[str] = Field(
        default_factory=list,
        description="多视图本地文件 path (file://... 或纯路径), 顺序对应 required_views",
    )
    required_views: list[str] = Field(
        default_factory=lambda: ["front", "side", "3-quarter"],
    )

    # --- embedding (Phase 1.2) ---
    embedding: Optional[list[float]] = Field(
        default=None,
        description="ArcFace / CLIP embedding, Phase 1.2 接 InsightFace 后填充",
    )
    embedding_kind: Optional[str] = Field(
        default=None,
        description='"arcface" | "clip" 标识 embedding 来源',
    )

    # --- 元信息 ---
    profile_id: Optional[str] = Field(
        default=None,
        description="该 character 建档时关联的 profile (e.g. short_drama)",
    )
    schema_version: int = SCHEMA_VERSION
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    notes: str = ""

    def touch(self) -> None:
        """手动标记更新时间 (mutate)."""
        self.updated_at = _now_iso()
