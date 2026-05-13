"""LangGraph 编织层 / 入口.

待 Phase 1 完成后, video_ppl.py 的图 + 节点会迁移到这里:
  - graph.py: 主图定义 (Planner → Assets → Storyboard → Shots → Audio → Post)
  - state.py: PipelineState / ShotState typed dict
  - nodes.py: 节点函数, 每个节点是某 module 的薄包装

现阶段 video_ppl.py 还是入口, 这个目录为空骨架.
"""
