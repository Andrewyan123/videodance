"""i2v instruction 拼装.

跟 keyframe_prompt 分工:
  keyframe_prompt: 静态构图 (shot_type, camera_angle, lighting...) — 这些已经焊进首/末帧
  video_prompt:    动作 (motion arc) + 镜头运动 + 情绪表演 + 对白 (有则给嘴部 hint)

设计:
  - action 是核心 (必填)
  - action_start/end 时给 i2v 模型明确的动作起止 (Phase 3.1 LLM 法已拆好)
  - camera_movement: static 不进 prompt, 其他 (dolly_in, pan, tracking 等) 进
  - emotion: neutral 不进, 其他作为 mood 修饰
  - dialogue_text: 有就提示"speaking: ...", 帮 i2v 模型生成嘴部动作 (即使没真实口型同步)
"""
from __future__ import annotations

from typing import Optional


def build_video_instruction(
    *,
    action: str,
    action_start: Optional[str] = None,
    action_end: Optional[str] = None,
    camera_movement: str = "static",
    emotion: str = "neutral",
    dialogue_text: Optional[str] = None,
) -> str:
    """拼一个 i2v 模型用的 instruction.

    Args:
        action: 这个 shot 的核心动作描述 (必填)
        action_start/end: Phase 3.1 LLM 拆出的动作两端态. 两个都有且不同 → 写进 prompt
                          帮模型理解动作轨迹 (与 keyframe 首尾呼应)
        camera_movement: static (不写) | dolly_in | pan_left ... → 写镜头运动
        emotion: neutral (不写) | tense / joyful / ... → 加 mood 修饰
        dialogue_text: 角色对白文本. 有就提示 "speaking: \\"...\\""

    Returns:
        逗号分隔的 instruction, 适合 DashScope wanx i2v 端点.
    """
    parts: list[str] = [action.strip()]

    # 动作弧 — 起止显著不同时写, 否则跟 action 重复
    if action_start and action_end:
        a_start = action_start.strip()
        a_end = action_end.strip()
        if a_start != a_end and a_start.lower() != a_end.lower():
            parts.append(f"motion arc: {a_start} → {a_end}")

    # 镜头运动 — static 跳过
    cm = (camera_movement or "").strip().lower()
    if cm and cm != "static":
        parts.append(f"camera {cm.replace('_', ' ')}")

    # 情绪 — neutral 跳过
    em = (emotion or "").strip().lower()
    if em and em != "neutral":
        parts.append(f"{em} mood")

    # 对白 — 给嘴部动作 hint (无真实口型同步, 但模型会试着动嘴)
    if dialogue_text:
        # 截短 + 转义可能干扰的字符
        clean = dialogue_text.strip().replace('"', "'")[:100]
        if clean:
            parts.append(f'character speaking: "{clean}"')

    return ", ".join(parts)
