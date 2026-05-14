"""CLI for asset library.

用法:
  python -m assets create --char-id char_yan --name Yan --description "..." [--profile short_drama] [--dry-run]
  python -m assets list [--profile short_drama]
  python -m assets inspect <char_id>
  python -m assets delete <char_id> [--keep-files]

约定:
  - 命令简洁优先, 不做花哨的 argparse 子解析器组合
  - 输出走 stdout, 错误 / 警告走 stderr, 退出码遵守 unix 习惯 (0 ok, 1 generic err, 2 usage err)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Optional

from . import builder, store
from .schema import CharacterCard


def _print_card(card: CharacterCard, *, verbose: bool = False) -> None:
    if not verbose:
        n_refs = len(card.ref_image_urls)
        print(f"{card.char_id:30s} {card.name:20s} {card.profile_id or '-':20s} {n_refs}")
    else:
        print(card.model_dump_json(indent=2))


# --- Commands ---


def cmd_create(args: argparse.Namespace) -> int:
    async def run():
        return await builder.build_character(
            char_id=args.char_id,
            name=args.name,
            description=args.description,
            profile_id=args.profile,
            style_tokens=args.style_tokens or [],
            dry_run=args.dry_run,
        )

    print(f"creating character {args.char_id}... (dry_run={args.dry_run})", file=sys.stderr)
    card = asyncio.run(run())
    print(f"OK created:", file=sys.stderr)
    _print_card(card, verbose=True)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    cards = list(store.list_characters(profile_id=args.profile))
    if not cards:
        print("(no characters)", file=sys.stderr)
        return 0
    print(f"{'char_id':30s} {'name':20s} {'profile':20s} refs")
    print("-" * 80)
    for c in cards:
        _print_card(c)
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    card = store.get_character(args.char_id)
    if not card:
        print(f"not found: {args.char_id}", file=sys.stderr)
        return 1
    _print_card(card, verbose=True)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    ok = store.delete_character(args.char_id, delete_assets=not args.keep_files)
    if ok:
        print(f"deleted {args.char_id}", file=sys.stderr)
        return 0
    else:
        print(f"not found: {args.char_id}", file=sys.stderr)
        return 1


# --- Argparse ---


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m assets")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="generate character (T2I + store)")
    c.add_argument("--char-id", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--description", required=True)
    c.add_argument("--profile", default=None)
    c.add_argument("--style-tokens", nargs="*", default=None,
                   help="style tokens, space-separated")
    c.add_argument("--dry-run", action="store_true",
                   help="skip T2I, write placeholder PNGs")
    c.set_defaults(func=cmd_create)

    l = sub.add_parser("list", help="list characters")
    l.add_argument("--profile", default=None,
                   help="filter by profile_id")
    l.set_defaults(func=cmd_list)

    i = sub.add_parser("inspect", help="show full card JSON")
    i.add_argument("char_id")
    i.set_defaults(func=cmd_inspect)

    d = sub.add_parser("delete", help="delete character")
    d.add_argument("char_id")
    d.add_argument("--keep-files", action="store_true",
                   help="keep data/assets/<char_id>/ files on disk")
    d.set_defaults(func=cmd_delete)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
