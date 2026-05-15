"""Storyboard 用 system prompt 构造.

策略:
- 通用骨架 (schema 描述 + 输出格式约束) 写死, 必须返 JSON
- profile-specific 部分从 profile.system_prompt 取
- profile.extra_schema_fields 拼到 "重点字段" 提示里
- 末尾附少量 few-shot, 让 LLM 知道字段长啥样

不引入 prompts/ 包里的旧模板 (那些保留给原 video_ppl), 这里独立维护.
"""
from __future__ import annotations

from profiles.base import Profile


SCHEMA_GUIDE = """\
你必须返回**严格的 JSON**(不要 markdown 围栏, 不要解释文字). Schema 如下:

{
  "schema_version": 1,
  "global_style": "<整体风格描述>",
  "characters": [
    {"char_id": "<稳定 id, e.g. char_yan>", "name": "<人类可读名>", "description": "<外貌+服装>"}
  ],
  "shots": [
    {
      "shot_id": "<S01-001 样式>",
      "index": 0,
      "duration_sec": 5.0,
      "scene_ref": "<可空>",
      "character_ids": ["<同 char_id, 不要带@>"],
      "props": [],
      "shot_type": "<close_up|medium_close_up|medium|medium_wide|wide|extreme_wide|extreme_close_up>",
      "camera": {
        "angle": "<eye_level|low|high|dutch|overhead|worms_eye>",
        "movement": "<static|dolly_in|dolly_out|pan_left|pan_right|tilt_up|tilt_down|tracking|handheld|crane_up|crane_down>"
      },
      "action": "<这个 shot 里角色的动作描述, 一句话>",
      "emotion": "<情绪标签, e.g. tense, joyful, calm>",
      "dialogue": null,
      "narration": null
    }
  ]
}

字段约束:
- duration_sec 必须在 [2, 20] 之间
- shot_type / camera.angle / camera.movement 必须从上面给定的枚举值中选
- character_ids 里的 id 必须出现在 characters 数组里
- shot.index 从 0 开始连续递增
- 至少 1 个 shot
"""


FEWSHOT_EXAMPLE = """\
示例(仅作字段格式参考):

```
{
  "schema_version": 1,
  "global_style": "warm afternoon light, cozy interior, photorealistic",
  "characters": [
    {"char_id": "char_yan", "name": "Yan", "description": "young Chinese man, short black hair, white shirt"}
  ],
  "shots": [
    {
      "shot_id": "S01-001", "index": 0, "duration_sec": 5.0,
      "character_ids": ["char_yan"], "props": [],
      "shot_type": "medium_close_up",
      "camera": {"angle": "eye_level", "movement": "static"},
      "action": "Yan sits at his desk, opens a book", "emotion": "calm",
      "dialogue": null, "narration": null
    }
  ]
}
```
"""


def build_system_prompt(profile: Profile) -> str:
    """拼一份 profile-specific 的 system prompt.

    结构:
      1. profile.system_prompt (流派导向, 例如"短剧重对白")
      2. profile 的关键字段提示 (extra_schema_fields 列出来)
      3. SCHEMA_GUIDE (字段定义 + 约束)
      4. duration / shot count 数字提示 (profile 决定)
      5. few-shot 示例
    """
    extras = ", ".join(profile.extra_schema_fields) if profile.extra_schema_fields else "(无 profile 特有字段)"
    dur_lo, dur_hi = profile.single_shot_duration_range

    parts = [
        f"# 流派: {profile.display_name}",
        "",
        profile.system_prompt.strip(),
        "",
        f"## 本流派关键字段(优先填写)",
        f"  {extras}",
        "",
        f"## Shot 时长建议",
        f"  每个 shot **{dur_lo:.0f}-{dur_hi:.0f}** 秒",
        "",
        "## 输出 schema",
        SCHEMA_GUIDE,
        "",
        FEWSHOT_EXAMPLE,
    ]
    return "\n".join(parts)


def build_user_prompt(user_prompt: str, target_duration_sec: float) -> str:
    return (
        f"用户需求: {user_prompt}\n"
        f"目标总时长: {target_duration_sec}s\n"
        f"请返回严格 JSON 格式的 storyboard."
    )


def build_retry_prompt(
    last_raw_output: str,
    error_msg: str,
    user_prompt: str,
    target_duration_sec: float,
) -> str:
    """LLM 上一次输出 schema-fail, 把错误反向喂回, 让它修."""
    raw_excerpt = last_raw_output[:1500]
    return (
        f"上一次你的 JSON 输出未通过 schema 校验:\n"
        f"  错误: {error_msg}\n\n"
        f"原 user prompt: {user_prompt}\n"
        f"目标时长: {target_duration_sec}s\n\n"
        f"你上次的输出 (片段):\n{raw_excerpt}\n\n"
        f"请修正并重新返回严格 JSON, **不要任何额外解释 / 围栏**."
    )
