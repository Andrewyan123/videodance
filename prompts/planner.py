"""Planner 提示词: 用户 prompt -> 结构化 shot list + characters + global style."""
from __future__ import annotations


PLANNER_SYSTEM = """你是电影导演. 根据用户 prompt 生成结构化 shot list.
返回严格 JSON: {global_style, characters: [{char_id, name, description}], shots: [{shot_id, index, prompt, duration_sec, character_ids, camera}]}
约束: shot 数量 = ceil(target_duration / 6), 每个 shot 5-8 秒."""


def build_planner_user(user_prompt: str, target_duration_sec: float) -> str:
    return f"prompt: {user_prompt}\ntarget_duration: {target_duration_sec}s"
