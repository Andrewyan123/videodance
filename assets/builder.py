"""Builder: 从 character 描述 → T2I 多视图 → 本地落盘 → 入库.

Flow:
  1. 调 IMAGE_PROVIDER 为每个角度生成图 (得到远程 URL)
  2. 并发下载到 data/assets/<char_id>/<view>.png
  3. 构造 CharacterCard (ref_image_urls 用本地路径), upsert 到 store

dry_run:
  - 不调 T2I,直接写占位 (8 字节空 PNG) + mock URL list 到 card
  - 让下游 (storyboard / generation) 都能 import 但不消耗 token

错误处理 (失败原子性):
  - 任何一步失败 → 清理 char_id 目录 + 不写 DB
  - 同 char_id 重新 build → 旧目录被先清空
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Optional

import aiohttp

from prompts import build_character_ref_prompt

from . import store
from .schema import CharacterCard


# 文件名安全的 angle ID -> T2I 的 prompt 短语
# (prompts.CHARACTER_ANGLES 的短语含空格 / 斜杠, 不能直接当文件名)
_ANGLE_TO_PHRASE: dict[str, str] = {
    "front": "front view",
    "side": "side view",
    "3-quarter": "3/4 view",
    "back": "back view",
}

DEFAULT_ANGLES: tuple[str, ...] = ("front", "side", "3-quarter")


# 1x1 灰色 PNG, 8 字节 header 都不够; 用最小合法 PNG (67 字节)
_PLACEHOLDER_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415478da636060600000000400015e8e1cd00000000049"
    "454e44ae426082"
)


async def _download(session: aiohttp.ClientSession, url: str, dst: Path) -> None:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            async for chunk in r.content.iter_chunked(1 << 15):
                f.write(chunk)


async def build_character(
    char_id: str,
    name: str,
    description: str,
    *,
    profile_id: Optional[str] = None,
    style_tokens: Optional[list[str]] = None,
    angles: Optional[tuple[str, ...]] = None,
    dry_run: bool = False,
) -> CharacterCard:
    """生成多视图 + 写库, 返回入库后的 CharacterCard.

    成功后 data/assets/<char_id>/ 下会有 <angle>.png 多个文件.
    失败抛异常, 清理已落盘的部分.
    """
    angles = angles or DEFAULT_ANGLES
    # 检查 angle 是否合法 (有对应 prompt 短语)
    unknown = [a for a in angles if a not in _ANGLE_TO_PHRASE]
    if unknown:
        raise ValueError(f"unknown angles {unknown}; supported: {list(_ANGLE_TO_PHRASE)}")
    asset_dir = store.asset_dir(char_id)

    # 清理旧资产 (overwrite 模式)
    for old in asset_dir.glob("*.png"):
        old.unlink()

    local_paths: list[Path] = []
    try:
        if dry_run:
            # 占位: 不调 T2I, 直接落最小 PNG
            for angle in angles:
                p = asset_dir / f"{angle}.png"
                p.write_bytes(_PLACEHOLDER_PNG)
                local_paths.append(p)
        else:
            # 真实路径: 调 image provider → 下载
            # 注: 这里不直接 import IMAGE_PROVIDER (在 video_ppl.py 里);
            # 改成在调用方传 IMAGE_PROVIDER 进来更解耦, 但 MVP 先用 build_image_provider
            from providers import build_image_provider
            img = build_image_provider()

            # 顺序生成 (避免 DashScope QPS 限流; 也方便 debug). 3 张图 ~30s.
            remote_urls: list[str] = []
            for angle in angles:
                phrase = _ANGLE_TO_PHRASE[angle]
                prompt = build_character_ref_prompt(description, phrase)
                result = await img.generate(prompt)
                remote_urls.append(result.url)

            # 并发下载
            async with aiohttp.ClientSession() as sess:
                tasks = []
                for angle, url in zip(angles, remote_urls):
                    p = asset_dir / f"{angle}.png"
                    local_paths.append(p)
                    tasks.append(_download(sess, url, p))
                await asyncio.gather(*tasks)

        # 构造并写 store
        card = CharacterCard(
            char_id=char_id,
            name=name,
            description=description,
            style_tokens=style_tokens or [],
            ref_image_urls=[f"file://{p.resolve()}" for p in local_paths],
            required_views=list(angles),
            profile_id=profile_id,
        )
        store.upsert_character(card)
        return card

    except BaseException:
        # 失败 → 清理本次落地的文件 (但保留 dir, 因为 store.asset_dir() 创建)
        for p in local_paths:
            if p.exists():
                p.unlink()
        # 不删 DB row, 因为还没写; 但要确保 partial 不残留
        raise
