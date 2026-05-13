"""资产库: 角色 / 场景 / 产品的持久化与检索.

Phase 1 (待实现):
  - schema.py:  CharacterCard / SceneCard / ProductCard Pydantic 模型
  - store.py:   SQLite 元数据 + data/assets/ 文件系统
  - faceid.py:  InsightFace embedding 抽取 + 比对
  - builder.py: T2I → 多视图 → 校验 → 入库
  - cli.py:     python -m assets create / list / inspect

设计原则: 所有下游 (storyboard / generation / critic) 都从这里 retrieval 取参考,
永不让 shot N 输出作为 shot N+1 输入. 见 design/architecture.md §3 第 2 条.
"""
