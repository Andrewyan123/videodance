"""CLI for storyboard module.

用法:
  python -m storyboard generate --prompt "..." --duration 20 --profile short_drama
  python -m storyboard validate < storyboard.json   # 从 stdin 读 JSON, 校验

约定:
  - generate 把 Storyboard JSON 输出到 stdout (便于 `> sb.json` 或 `| jq`)
  - 进度信息 / 错误 走 stderr
  - 退出码: 0 OK / 1 schema 失败 / 2 usage 错
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Optional

from profiles import get_profile, list_profiles

from .planner import StoryboardError, generate_storyboard
from .repair import parse_llm_json
from .schema import Storyboard
from .validator import validate


def cmd_generate(args: argparse.Namespace) -> int:
    profile = get_profile(args.profile)
    print(f"[storyboard] profile={profile.profile_id}, target={args.duration}s",
          file=sys.stderr)

    async def run():
        return await generate_storyboard(
            user_prompt=args.prompt,
            target_duration_sec=args.duration,
            profile=profile,
            max_retries=args.max_retries,
            check_store=not args.no_check_store,
            verbose=True,
        )

    try:
        sb, report, metrics = asyncio.run(run())
    except StoryboardError as e:
        print(f"\n[storyboard] FAILED: {e}", file=sys.stderr)
        print(f"  last_errors: {e.last_errors[:3]}", file=sys.stderr)
        if args.dump_raw:
            print(f"  last_raw[:500]: {e.last_raw[:500]}", file=sys.stderr)
        return 1

    print(f"\n[storyboard] {report.summary()}", file=sys.stderr)
    print(f"[storyboard] metrics: {metrics}", file=sys.stderr)
    if report.missing_chars:
        print(f"[storyboard] WARN missing @char_id 未在 asset 库: {report.missing_chars}",
              file=sys.stderr)

    # 真数据走 stdout
    print(sb.model_dump_json(indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """从 stdin 读 raw text → repair → validate."""
    raw = sys.stdin.read()
    try:
        data, mode = parse_llm_json(raw)
        print(f"[validate] parse mode = {mode}", file=sys.stderr)
    except ValueError as e:
        print(f"[validate] JSON parse failed: {e}", file=sys.stderr)
        return 1

    report = validate(data, check_store=not args.no_check_store)
    print(f"[validate] {report.summary()}", file=sys.stderr)
    if report.schema_errors:
        for err in report.schema_errors:
            print(f"  schema error: {err}", file=sys.stderr)
        return 1
    if report.warnings:
        for w in report.warnings:
            print(f"  warn: {w}", file=sys.stderr)
    if report.missing_chars:
        print(f"  missing in asset lib: {report.missing_chars}", file=sys.stderr)
    # echo 重新序列化的标准格式到 stdout
    print(report.storyboard.model_dump_json(indent=2))
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    """列出可用 profile."""
    print(f"{'profile_id':15s} {'display_name':25s} keyframe_strategy   video_backend_preferred")
    print("-" * 100)
    for p in list_profiles():
        print(f"{p.profile_id:15s} {p.display_name:25s} "
              f"{p.keyframe_strategy:18s}  {p.video_backend_preferred}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m storyboard")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="LLM 生成 storyboard")
    g.add_argument("--prompt", required=True, help="用户需求(自然语言)")
    g.add_argument("--duration", type=float, default=15.0, help="目标总时长秒")
    g.add_argument("--profile", default="short_drama",
                   help="流派: short_drama | anime | cinema | commercial")
    g.add_argument("--max-retries", type=int, default=3)
    g.add_argument("--no-check-store", action="store_true",
                   help="跳过 asset 库引用解析 (offline / testing)")
    g.add_argument("--dump-raw", action="store_true",
                   help="失败时打印 LLM raw output")
    g.set_defaults(func=cmd_generate)

    v = sub.add_parser("validate", help="从 stdin 读 JSON 校验 schema")
    v.add_argument("--no-check-store", action="store_true")
    v.set_defaults(func=cmd_validate)

    pp = sub.add_parser("profiles", help="列出可用 profile")
    pp.set_defaults(func=cmd_profiles)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
