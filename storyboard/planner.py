"""Storyboard 生成主入口.

generate_storyboard(user_prompt, target_duration_sec, profile) -> Storyboard | ValidationReport

实现 = LLM 调用 + repair + validate + 重试循环:
  1. 用 profile 拼 system prompt, user prompt 包 target_duration
  2. LLM 调用
  3. repair (fence strip + json_repair fallback)
  4. validate (schema-only, 引用解析交给调用方)
  5. schema fail → 把 error 反向喂 LLM, retry (最多 N 次)
  6. 仍失败 → raise StoryboardError 带 last raw output
"""
from __future__ import annotations

from typing import Optional

from profiles.base import Profile
from providers import build_llm_provider, LLMProvider

from .prompts import build_retry_prompt, build_system_prompt, build_user_prompt
from .repair import parse_llm_json
from .schema import Storyboard
from .validator import validate, ValidationReport


_DEFAULT_MAX_RETRIES = 3


class StoryboardError(Exception):
    """Schema-fail 用尽重试仍失败. 调用方可看 last_raw / last_errors."""

    def __init__(self, msg: str, *, last_raw: str = "", last_errors: list[str] | None = None) -> None:
        super().__init__(msg)
        self.last_raw = last_raw
        self.last_errors = last_errors or []


async def generate_storyboard(
    user_prompt: str,
    target_duration_sec: float,
    profile: Profile,
    *,
    llm: Optional[LLMProvider] = None,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    check_store: bool = True,
    verbose: bool = False,
) -> tuple[Storyboard, ValidationReport, dict]:
    """生成 storyboard.

    返回 (storyboard, validation_report, metrics).
    - storyboard 是 schema-valid 的 Storyboard 实例
    - validation_report 含引用解析结果 (missing_chars / warnings)
    - metrics: {n_llm_calls, repair_used, last_repair_mode, raw_lengths}

    schema-fail 用尽重试 → StoryboardError.
    """
    llm = llm or build_llm_provider()
    system_prompt = build_system_prompt(profile)
    user_msg = build_user_prompt(user_prompt, target_duration_sec)

    metrics = {
        "n_llm_calls": 0,
        "repair_used": False,
        "last_repair_mode": None,
        "raw_lengths": [],
    }

    last_raw = ""
    last_errors: list[str] = []

    for attempt in range(max_retries + 1):
        if attempt == 0:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ]
        else:
            # 重试: 把 error 反向喂回
            retry_user = build_retry_prompt(
                last_raw_output=last_raw,
                error_msg="; ".join(last_errors[:3]),
                user_prompt=user_prompt,
                target_duration_sec=target_duration_sec,
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": retry_user},
            ]

        if verbose:
            print(f"[storyboard] LLM call attempt={attempt+1}/{max_retries+1}")

        resp = await llm.ainvoke(messages)
        metrics["n_llm_calls"] += 1
        last_raw = resp.content or ""
        metrics["raw_lengths"].append(len(last_raw))

        # parse / repair
        try:
            data, repair_mode = parse_llm_json(last_raw)
            metrics["last_repair_mode"] = repair_mode
            if repair_mode == "repaired":
                metrics["repair_used"] = True
        except ValueError as e:
            last_errors = [f"json_parse: {e}"]
            if verbose:
                print(f"[storyboard] parse fail: {e}")
            continue

        # validate
        report = validate(data, check_store=check_store)
        if report.schema_ok:
            if verbose:
                print(f"[storyboard] {report.summary()}")
            return report.storyboard, report, metrics

        # schema fail → retry
        last_errors = report.schema_errors
        if verbose:
            print(f"[storyboard] schema fail attempt={attempt+1}: {last_errors[:2]}")

    # 所有重试都失败
    raise StoryboardError(
        f"storyboard generation failed after {max_retries+1} attempts",
        last_raw=last_raw,
        last_errors=last_errors,
    )
