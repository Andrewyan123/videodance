"""LLM raw output → 干净 dict.

层级 fallback:
  1. strip_fences: 砍掉 ```json ... ``` / ``` ... ``` 包裹
  2. json.loads: 直接解析(成功的话 0 损耗)
  3. json_repair.repair_json: 容错修复(漏逗号 / 单引号 / 截断 / 围栏残留)

repair_json 自身能处理围栏, 但显式 strip 让流程更可控 + 日志更清晰.
"""
from __future__ import annotations

import json
import re
from typing import Any

from json_repair import repair_json


_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n(.*?)\n```\s*$",
    re.DOTALL,
)


def strip_fences(text: str) -> str:
    """剥 ```json ... ``` 围栏. 没围栏就原样返."""
    m = _FENCE_RE.match(text.strip())
    if m:
        return m.group(1).strip()
    # 也兼容只有起始围栏没结束的
    t = text.strip()
    if t.startswith("```"):
        # 砍第一行
        t = t.split("\n", 1)[1] if "\n" in t else t
        # 砍尾部 ```
        if t.rstrip().endswith("```"):
            t = t.rstrip()[: -len("```")]
    return t.strip()


def parse_llm_json(raw: str) -> tuple[Any, str]:
    """LLM 输出 → dict.

    返回 (parsed_dict, repair_mode), repair_mode ∈ {"direct", "repaired"}.
    raise ValueError 时表示连 repair_json 也救不回来.
    """
    cleaned = strip_fences(raw)
    if not cleaned:
        raise ValueError("empty content after fence strip")

    # 1. 直接 parse
    try:
        return json.loads(cleaned), "direct"
    except json.JSONDecodeError:
        pass

    # 2. json_repair 救一次
    repaired = repair_json(cleaned)
    try:
        return json.loads(repaired), "repaired"
    except json.JSONDecodeError as e:
        raise ValueError(f"json_repair fallback also failed: {e}") from e
