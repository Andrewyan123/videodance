"""Shot 级提示词构造: 角色参考图 / 关键帧 / 视频运镜."""
from __future__ import annotations


CHARACTER_ANGLES = ("front view", "3/4 view", "side view")


def build_character_ref_prompt(description: str, angle: str) -> str:
    """单角色单角度的参考图 prompt."""
    return f"{description}, {angle}, neutral background, full body"


def build_keyframe_prompt(global_style: str, camera: str, scene: str) -> str:
    """Shot 关键帧 (i2v 的首帧) prompt."""
    return f"{global_style}, {camera}, {scene}"


def build_video_prompt(scene: str) -> str:
    """i2v 的动作描述 prompt. 当前直接传 scene; 后续可叠加运镜/动作 hint."""
    return scene
