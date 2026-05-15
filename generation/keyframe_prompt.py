"""Keyframe i2i instruction 拼装.

Phase 3.1: 把 ShotV1 / Shot 的富字段 (shot_type / camera / emotion / dialogue / action_start/end)
拼成一段供 i2i 模型消费的 instruction.

设计:
- build_keyframe_instruction(...) 用于 single 策略, 输出 1 个 instruction
- build_first_last_pair(...) 用于 first_last 策略, 输出 (first_instr, last_instr) 两个
- 缺 action_start/end 时优雅 fallback 为 action 重复(规则法兜底)
"""
from __future__ import annotations

from typing import Optional


def _camera_phrase(angle: str, movement: str) -> str:
    """把 camera_angle / movement 拼成自然语言短语."""
    # 将下划线变空格, 让 t2i/i2i prompt 更自然
    a = angle.replace("_", " ")
    m = movement.replace("_", " ")
    return f"{a} angle, {m} camera"


def _wrap_emotion(emotion: str) -> str:
    """情绪修饰 — neutral 不加, 其他作为 mood/expression 修饰."""
    e = (emotion or "").strip().lower()
    if not e or e == "neutral":
        return ""
    return f", {e} mood"


def build_keyframe_instruction(
    *,
    global_style: str,
    shot_type: str = "medium",
    camera_angle: str = "eye_level",
    camera_movement: str = "static",
    action: str,
    emotion: str = "neutral",
    scene_ref: Optional[str] = None,
) -> str:
    """单帧策略 (single): 拼一个完整 i2i instruction.

    形如:
      "{global_style}, {shot_type} shot, {camera}, {action}{, emotion mood}"
    """
    parts = [global_style] if global_style else []
    parts.append(f"{shot_type.replace('_', ' ')} shot")
    parts.append(_camera_phrase(camera_angle, camera_movement))
    parts.append(action)
    emo = _wrap_emotion(emotion)
    if emo:
        # _wrap_emotion 已经带 ", " 前缀, 直接拼到最后
        return ", ".join(parts) + emo
    return ", ".join(parts)


def build_first_last_pair(
    *,
    global_style: str,
    shot_type: str = "medium",
    camera_angle: str = "eye_level",
    camera_movement: str = "static",
    action: str,
    action_start: Optional[str],
    action_end: Optional[str],
    emotion: str = "neutral",
    scene_ref: Optional[str] = None,
) -> tuple[str, str]:
    """首尾帧策略 (first_last): 返回 (first_instr, last_instr).

    优先用 LLM 输出的 action_start/end (语义起止), 缺时 fallback 到规则法:
      first = action + ", frozen at the beginning moment, initial body pose"
      last  = action + ", frozen at the ending moment, final body pose"

    fallback 是为了让流程在 LLM 漏字段时不崩, 不是首选路径.
    """
    # camera / shot_type / emotion 是两帧共享的"视觉骨架"
    common_prefix_parts = [global_style] if global_style else []
    common_prefix_parts.append(f"{shot_type.replace('_', ' ')} shot")
    common_prefix_parts.append(_camera_phrase(camera_angle, camera_movement))
    common = ", ".join(common_prefix_parts)
    emo = _wrap_emotion(emotion)

    if action_start and action_end:
        # LLM 法: 两端态语义独立
        first = f"{common}, {action_start}{emo}"
        last = f"{common}, {action_end}{emo}"
    else:
        # 规则法兜底
        first = f"{common}, {action}, frozen at the BEGINNING moment, initial body pose{emo}"
        last = f"{common}, {action}, frozen at the ENDING moment, final body pose after completing the action{emo}"

    return first, last
