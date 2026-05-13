# storyboard/ — 分镜 DSL

> Phase 2 (待实现)

## 职责

把用户口语化 prompt + 选定的 Profile,变成一份**严格结构化、可被下游确定性消费**的 shot list JSON。

## Shot v1 schema 草案

```json
{
  "shot_id": "S03-007",
  "duration_sec": 6.5,
  "scene_ref": "@scene_03_office_night",
  "characters": ["@char_01_yan", "@char_02_boss"],
  "props": ["@prop_05_laptop"],
  "shot_type": "medium_close_up",
  "camera": {"angle": "low", "movement": "dolly_in"},
  "action": "yan looks up from laptop, frowns",
  "dialogue": {"speaker": "@char_01_yan", "text": "..."},
  "emotion": "tense",
  "narration": null
}
```

字段是否启用由 Profile.extra_schema_fields 决定(短剧有 dialogue,广告片有 product_id + cta)。

## 计划文件

| 文件 | 职责 |
|---|---|
| `schema.py` | Pydantic v2 `Shot`, `Storyboard`, version 字段 |
| `planner.py` | LLM 调用 + JSON 解析 + 重试 |
| `validator.py` | schema 校验 + `@id` 引用解析(查 `assets/store`) |
| `repair.py` | jsonrepair + few-shot fallback |
| `prompts.py` | system prompt 模板 + per-profile override |
| `cli.py` | `python -m storyboard generate ...` |

## Acceptance(Phase 2 完成定义)

20 个 prompt × 4 profile 测试,JSON schema 合法率 >95%,`@id` 引用解析失败率 <5%。
