"""Storyboard Pydantic v1 schema.

设计:
- 所有字段都有合理默认值, LLM 漏字段 → 填默认, 不直接 fail
- char_id 引用允许 "@char_yan" 和 "char_yan" 两种写法(去@后等价), 用 _normalize_id
- Camera / shot_type 是 enum-like Literal, 增加类型安全
- model_config = {"extra": "ignore"} 容忍 LLM 多输出字段
- schema_version=1 锁定, 后续演进走 v2
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA_VERSION = 1


def normalize_id(s: str) -> str:
    """'@char_yan' → 'char_yan'; 已经裸的保持不变."""
    return s[1:] if s.startswith("@") else s


# --- enums ---

ShotType = Literal[
    "extreme_close_up", "close_up", "medium_close_up",
    "medium", "medium_wide", "wide", "extreme_wide",
]

CameraAngle = Literal[
    "eye_level", "low", "high", "dutch", "overhead", "worms_eye",
]

CameraMovement = Literal[
    "static", "dolly_in", "dolly_out",
    "pan_left", "pan_right", "tilt_up", "tilt_down",
    "tracking", "handheld", "crane_up", "crane_down",
]


# --- nested ---


class CameraSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    angle: CameraAngle = "eye_level"
    movement: CameraMovement = "static"

    def as_string(self) -> str:
        return f"{self.angle} angle, {self.movement}"


class DialogueSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    speaker: str  # "@char_01" or "char_01"
    text: str

    @field_validator("speaker")
    @classmethod
    def _norm_speaker(cls, v: str) -> str:
        return normalize_id(v)


class CharacterRef(BaseModel):
    """Storyboard 里登场的角色. char_id 是稳定引用键."""
    model_config = ConfigDict(extra="ignore")
    char_id: str
    name: str
    description: str = ""

    @field_validator("char_id")
    @classmethod
    def _norm_id(cls, v: str) -> str:
        return normalize_id(v)


# --- shot ---


class ShotV1(BaseModel):
    """一个镜头. 字段映射到分镜 DSL 的最小可执行子集.

    保持 character_ids / camera 名跟原 video_ppl Shot dataclass 一致, 便于映射.
    """
    model_config = ConfigDict(extra="ignore")

    shot_id: str
    index: int = Field(ge=0)
    duration_sec: float = Field(ge=2.0, le=20.0)

    # 引用
    scene_ref: Optional[str] = None  # "@scene_03_office_night" (Phase 3+ 加 SceneCard)
    character_ids: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)

    # 视觉
    shot_type: ShotType = "medium"
    camera: CameraSpec = Field(default_factory=CameraSpec)
    action: str = ""
    emotion: str = "neutral"

    # Phase 3.1: 动作拆分(LLM 输出, 首尾帧 i2i 用)
    # 为运动型 shot 提供两端态(空间不同), 静态 shot 可 None / 与 action 近似
    action_start: Optional[str] = None
    action_end: Optional[str] = None

    # 音频
    dialogue: Optional[DialogueSpec] = None
    narration: Optional[str] = None

    @field_validator("character_ids", mode="before")
    @classmethod
    def _norm_char_ids(cls, v):
        if v is None:
            return []
        return [normalize_id(x) for x in v]

    @field_validator("scene_ref")
    @classmethod
    def _norm_scene(cls, v):
        return normalize_id(v) if v else None


# --- storyboard ---


class Storyboard(BaseModel):
    """整本 storyboard. 编剧 / 导演 LLM 输出的产物."""
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1] = 1
    global_style: str
    characters: list[CharacterRef] = Field(default_factory=list)
    shots: list[ShotV1]

    @field_validator("shots")
    @classmethod
    def _shots_non_empty(cls, v):
        if not v:
            raise ValueError("shots 至少要有 1 个")
        return v
