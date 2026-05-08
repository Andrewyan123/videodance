"""Prompt templates for the video pipeline."""
from .critic import CRITIC_RUBRIC
from .planner import PLANNER_SYSTEM, build_planner_user
from .shots import (
    CHARACTER_ANGLES,
    build_character_ref_prompt,
    build_keyframe_prompt,
    build_video_prompt,
)

__all__ = [
    "PLANNER_SYSTEM", "build_planner_user",
    "CRITIC_RUBRIC",
    "CHARACTER_ANGLES",
    "build_character_ref_prompt",
    "build_keyframe_prompt",
    "build_video_prompt",
]
