"""VLM critic 提示词: 用结构化 rubric 而不是 free-form review, 避免 over-critical reject."""
from __future__ import annotations


CRITIC_RUBRIC = """评估以下视频, 对每条返回 PASS/FAIL + 一行原因:
1. character_identity: 角色脸型与参考图一致
2. character_count: 角色数量正确
3. anatomy: 手指数量、肢体比例正常
4. style_consistency: 整体风格匹配 global_style
5. physical_plausibility: 没有明显穿模/光照断裂
返回 JSON: {"checks": {...}, "overall_pass": bool, "notes": str}
"""
