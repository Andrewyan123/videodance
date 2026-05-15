"""Storyboard schema validation + `@char_id` 引用解析.

策略 (Phase 2 用户决定: 宽容):
- Pydantic 校验:严格 → 出 ValidationError 是真错(LLM 输出 schema 不合)
- 引用解析:**宽容** → `@char_id` 不在 `assets.store` 里只 warn,不 fail
  让 pipeline 在 cache miss 时 fall back 到 t2i 临时生成(Phase 1 集成的现行行为)

返回 ValidationReport 提供详细信息, 调用方决定怎么用.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .schema import Storyboard


@dataclass
class ValidationReport:
    """校验结果汇总."""
    storyboard: Storyboard | None = None
    schema_errors: list[str] = field(default_factory=list)
    missing_chars: list[str] = field(default_factory=list)  # @char_id 在 shot 中引用但 store 没有
    unreferenced_chars: list[str] = field(default_factory=list)  # characters[] 中声明但没 shot 引用
    warnings: list[str] = field(default_factory=list)

    @property
    def schema_ok(self) -> bool:
        return self.storyboard is not None and not self.schema_errors

    @property
    def refs_ok(self) -> bool:
        return not self.missing_chars

    def summary(self) -> str:
        if not self.schema_ok:
            return f"schema FAIL: {len(self.schema_errors)} errors"
        ok = "OK" if self.refs_ok else "refs-warn"
        return (
            f"{ok}: {len(self.storyboard.shots)} shots, "
            f"{len(self.storyboard.characters)} chars, "
            f"missing_chars={self.missing_chars}, "
            f"unref_chars={self.unreferenced_chars}"
        )


def validate(data: dict[str, Any], *, check_store: bool = True) -> ValidationReport:
    """
    Args:
        data: parse_llm_json 解出来的 dict
        check_store: 是否查 assets.store 解 char_id 引用. 测试时可关掉.

    Returns:
        ValidationReport
    """
    report = ValidationReport()

    # 1. Pydantic schema
    try:
        sb = Storyboard.model_validate(data)
        report.storyboard = sb
    except ValidationError as e:
        # 拆出每个错为单独条目
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            report.schema_errors.append(f"{loc}: {err['msg']}")
        return report  # schema fail 就不查引用了

    # 2. 引用解析(宽容)
    declared_ids = {c.char_id for c in sb.characters}
    referenced_ids: set[str] = set()
    for shot in sb.shots:
        referenced_ids.update(shot.character_ids)
        if shot.dialogue is not None:
            referenced_ids.add(shot.dialogue.speaker)

    # shot 引用了但 characters[] 没声明 → 报到 missing(无论 store 有没有)
    not_declared = referenced_ids - declared_ids
    for cid in sorted(not_declared):
        report.warnings.append(f"shot 引用了 '@{cid}', 但 characters[] 中没声明")

    # 2.5 store 查询
    if check_store:
        from assets import store as asset_store
        for cid in sorted(declared_ids | referenced_ids):
            if asset_store.get_character(cid) is None:
                report.missing_chars.append(cid)

    # 反向: characters 声明了但没 shot 用
    unref = declared_ids - referenced_ids
    report.unreferenced_chars = sorted(unref)

    return report
