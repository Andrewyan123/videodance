"""Critic 评审层: 多维度质量打分 + 重试升级策略.

Phase 4 (待实现):
  - identity.py:  ArcFace 跨 shot 一致性 (依赖 assets/faceid)
  - scene.py:     CLIP image-image 相邻 shot 场景比对
  - narrative.py: VLM caption → LLM 判叙事
  - vsa.py:       Visual-Script Alignment 评分
  - lipsync.py:   口型偏差 (短剧 / 电影必查)
  - policy.py:    重试升级 seed → backend → human
  - prompts.py:   critic rubric per profile

设计原则: critic 输出从 day 1 起就是结构化分数 (0-1),
后续 RL 训练 ScripterAgent / RouterAgent 时直接拿来当 reward.
见 design/architecture.md §3 第 4 条.
"""
