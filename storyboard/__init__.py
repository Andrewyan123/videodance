"""分镜 DSL: 用户 prompt + Profile → 结构化 shot list.

Phase 2 (待实现):
  - schema.py:    Shot v1 schema (Pydantic, versioned)
  - planner.py:   LLM → JSON (system prompt 由 Profile 注入)
  - validator.py: schema check + @id 引用解析 (依赖 assets/store)
  - repair.py:    jsonrepair + 5 次重试兜底
  - prompts.py:   per-profile system prompt 模板
  - cli.py:       python -m storyboard generate --prompt ... --profile ...

设计原则: 分镜是"程序", 下游所有 AI 模块都是这份程序的执行器.
见 design/architecture.md §1 与 design/full.md "分镜即程序".
"""
