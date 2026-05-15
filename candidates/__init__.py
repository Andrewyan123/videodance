"""Candidate variants store —— interactive pipeline 的核心数据层.

每个 character / shot 可以有多个候选(variants), 用户从中挑一个 selected.
所有候选完整存在 SQLite, 刷新 / 重启 server 不丢. 选中的会被拷到 assets.store
作为下游 i2i anchor / 拼接 source.

模块结构:
  schema.py   — CharacterCandidate / ShotCandidate / Session Pydantic
  store.py    — SQLite 三表 CRUD (sessions, character_candidates, shot_candidates)
  builder.py  — 真正生成候选的协程 (调 t2i / i2i / i2v)
"""
