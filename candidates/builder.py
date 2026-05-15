"""Propose character / shot 候选的协程.

复用 Phase 1.2 assets.builder + Phase 3.x generation/router + Phase 4.1 critic/identity.
每个 propose 内可以并发,但典型用法是用户驱动按需触发,不强求快.

emit() callback 可选:用于把进度推到 server SSE (开口给前端进度条).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from . import store
from .schema import CharacterCandidate, ShotCandidate

EmitFn = Optional[Callable[[dict], Awaitable[None]]]


async def _noop_emit(_event: dict) -> None:
    pass


# === Character variants ===


async def propose_character_variants(
    *,
    thread_id: str,
    char_id: str,
    n: int,
    description: str,
    profile_id: str = "short_drama",
    emit: EmitFn = None,
) -> list[CharacterCandidate]:
    """为单个 character 生成 n 个候选, 入库, 返回新增的 CharacterCandidate 列表.

    每个 variant 用 char_id__vK 作为 assets 库的真实 char_id, 写到 data/assets/{char_id}__v{K}/.
    用户 select 后, 调用方负责把 selected variant 的数据 promote 到 canonical char_id.
    """
    emit = emit or _noop_emit
    from assets import builder as asset_builder
    from assets import store as asset_store

    start_index = store.next_character_variant_index(thread_id, char_id)
    variants: list[CharacterCandidate] = []

    await emit({"type": "propose_character_start", "char_id": char_id,
                "n": n, "start_index": start_index})

    # 串行(t2i 同 char 多次同时跑容易撞 QPS)
    for i in range(n):
        variant_idx = start_index + i
        sub_char_id = f"{char_id}__v{variant_idx}"
        try:
            await emit({"type": "propose_character_progress", "char_id": char_id,
                        "variant_index": variant_idx, "stage": "t2i"})
            card = await asset_builder.build_character(
                char_id=sub_char_id,
                name=sub_char_id,
                description=description,
                profile_id=profile_id,
                dry_run=False,
            )
            # 用 front view 作 ref_image_url (后续 i2i 的 anchor)
            ref_url = card.ref_image_urls[0]  # 已经是 file://path

            cand = CharacterCandidate(
                thread_id=thread_id, char_id=char_id, variant_index=variant_idx,
                description_used=description,
                ref_image_url=ref_url,
                embedding=card.embedding,
                consistency_score=None,  # 暂不取(card.notes 有 cross-view summary,但不是 single value)
                selected=False,
                cost_usd=0.06,  # 估 3 t2i × $0.02
            )
            cid = store.insert_character_candidate(cand)
            cand.id = cid
            variants.append(cand)
            await emit({"type": "propose_character_done_variant", "char_id": char_id,
                        "variant_index": variant_idx, "ref_image_url": ref_url})
        except Exception as e:
            await emit({"type": "propose_character_error", "char_id": char_id,
                        "variant_index": variant_idx, "error": str(e)[:200]})
            # 单个 variant 失败不阻塞下一个
            continue

    await emit({"type": "propose_character_complete", "char_id": char_id,
                "produced": len(variants)})
    return variants


def promote_character_to_canonical(
    *,
    thread_id: str,
    char_id: str,
) -> None:
    """把当前 selected variant 的数据 promote 到 canonical char_id, 供下游 i2i / asset_store 使用.

    实现:简单地 upsert 一个 CharacterCard 到 assets.store, char_id 是 canonical 名,
    ref_image_urls 用 selected variant 的(因为已经是本地文件,共享物理存储).
    """
    from assets import store as asset_store
    from assets.schema import CharacterCard

    cands = store.list_character_candidates(thread_id, char_id)
    selected = [c for c in cands if c.selected]
    if not selected:
        raise RuntimeError(
            f"no selected character_candidate for thread={thread_id} char_id={char_id}"
        )
    s = selected[0]
    # 用 description + ref + embedding 构造 canonical CharacterCard
    canonical = CharacterCard(
        char_id=char_id,
        name=char_id,
        description=s.description_used,
        ref_image_urls=[s.ref_image_url],  # 只放 selected 的 front view, 后续 i2i 取 [0]
        embedding=s.embedding,
        embedding_kind="arcface_buffalo_l",
        profile_id=None,
        notes=f"promoted from variant {s.variant_index} (thread={thread_id})",
    )
    asset_store.upsert_character(canonical)


# === Shot variants ===


async def propose_shot_variants(
    *,
    thread_id: str,
    shot_id: str,
    shot: Any,  # video_ppl.Shot, 含 Phase 3.1 富字段
    n: int,
    profile_id: str = "short_drama",
    emit: EmitFn = None,
) -> list[ShotCandidate]:
    """为单个 shot 生成 n 个视频候选.

    每个候选:
      1. i2i 出 (first, last) 双帧 (依 profile.keyframe_strategy)
      2. router.generate_video 出 mp4
      3. critic identity 评分 (vs selected character anchor)
      4. 入库

    seed: 第 k 个 variant 用 seed_base + k * 1000.
    """
    emit = emit or _noop_emit
    from profiles import get_profile
    from generation.keyframe_prompt import (
        build_first_last_pair,
        build_keyframe_instruction,
    )
    from generation.video_prompt import build_video_instruction
    from generation.router import generate_video
    from providers import build_image_edit_provider
    from assets import store as asset_store

    profile = get_profile(profile_id)
    img_edit = build_image_edit_provider()

    start_index = store.next_shot_variant_index(thread_id, shot_id)
    variants: list[ShotCandidate] = []

    # 取 selected character anchor (用第一个 char_id; 多角色暂只支持单 anchor)
    anchor_path: Optional[str] = None
    char_emb: Optional[list[float]] = None
    if shot.character_ids:
        cid = shot.character_ids[0]
        card = asset_store.get_character(cid)
        if card and card.ref_image_urls:
            url0 = card.ref_image_urls[0]
            if url0.startswith("file://"):
                p = url0[len("file://"):]
                if os.path.isfile(p):
                    anchor_path = p
            char_emb = card.embedding

    await emit({"type": "propose_shot_start", "shot_id": shot_id, "n": n,
                "anchor": anchor_path[-40:] if anchor_path else None})

    for i in range(n):
        variant_idx = start_index + i
        seed = 42 + variant_idx * 1000
        try:
            await emit({"type": "propose_shot_progress", "shot_id": shot_id,
                        "variant_index": variant_idx, "stage": "keyframe"})
            # 生成 keyframe (1 or 2 张)
            first_url: Optional[str] = None
            last_url: Optional[str] = None
            if anchor_path:
                if profile.keyframe_strategy == "first_last":
                    first_instr, last_instr = build_first_last_pair(
                        global_style="",
                        shot_type=shot.shot_type,
                        camera_angle=shot.camera_angle,
                        camera_movement=shot.camera_movement,
                        action=shot.prompt,
                        action_start=shot.action_start,
                        action_end=shot.action_end,
                        emotion=shot.emotion,
                    )
                    r1 = await img_edit.edit(anchor_path, first_instr)
                    r2 = await img_edit.edit(anchor_path, last_instr)
                    first_url, last_url = r1.url, r2.url
                else:
                    instr = build_keyframe_instruction(
                        global_style="",
                        shot_type=shot.shot_type,
                        camera_angle=shot.camera_angle,
                        camera_movement=shot.camera_movement,
                        action=shot.prompt,
                        emotion=shot.emotion,
                    )
                    r = await img_edit.edit(anchor_path, instr)
                    first_url = r.url
            else:
                # 没 anchor: t2i 兜底 (跟现 keyframe_node 一致)
                from providers import build_image_provider
                from prompts.shots import build_keyframe_prompt as legacy_kp
                img_t2i = build_image_provider()
                r = await img_t2i.generate(legacy_kp("", shot.camera, shot.prompt))
                first_url = r.url

            # 生成视频
            await emit({"type": "propose_shot_progress", "shot_id": shot_id,
                        "variant_index": variant_idx, "stage": "i2v",
                        "seed": seed})
            video_instr = build_video_instruction(
                action=shot.prompt,
                action_start=shot.action_start, action_end=shot.action_end,
                camera_movement=shot.camera_movement, emotion=shot.emotion,
                dialogue_text=shot.dialogue_text,
            )
            vresult, backend_used = await generate_video(
                profile=profile,
                prompt=video_instr,
                first_frame_url=first_url,
                last_frame_url=last_url,
                duration_sec=shot.duration_sec,
                seed=seed,
            )

            # critic identity score
            identity_score: Optional[float] = None
            if char_emb:
                await emit({"type": "propose_shot_progress", "shot_id": shot_id,
                            "variant_index": variant_idx, "stage": "score"})
                identity_score = await _score_identity(vresult.video_url, char_emb)

            cand = ShotCandidate(
                thread_id=thread_id, shot_id=shot_id, variant_index=variant_idx,
                first_keyframe_url=first_url, last_keyframe_url=last_url,
                seed=seed, backend_used=backend_used,
                video_url=vresult.video_url,
                identity_score=identity_score,
                selected=False,
                cost_usd=vresult.cost_usd + 0.05 * (2 if last_url else 1),  # i2v + i2i
            )
            cid = store.insert_shot_candidate(cand)
            cand.id = cid
            variants.append(cand)
            await emit({"type": "propose_shot_done_variant", "shot_id": shot_id,
                        "variant_index": variant_idx,
                        "video_url": vresult.video_url,
                        "identity_score": identity_score})

        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            # 把失败的 candidate 也存 (variant_index, error)
            cand = ShotCandidate(
                thread_id=thread_id, shot_id=shot_id, variant_index=variant_idx,
                video_url="",  # 没视频
                error=err[:500],
            )
            try:
                store.insert_shot_candidate(cand)
            except Exception:
                pass
            await emit({"type": "propose_shot_error", "shot_id": shot_id,
                        "variant_index": variant_idx, "error": err[:200]})
            continue

    await emit({"type": "propose_shot_complete", "shot_id": shot_id,
                "produced": len(variants)})
    return variants


async def _score_identity(video_url: str, embedding: list[float]) -> Optional[float]:
    """下载视频 + 抽帧 + ArcFace cos sim. 失败返 None."""
    import tempfile

    import aiohttp

    from critic.identity import score_shot_identity

    fd, tmp = tempfile.mkstemp(suffix=".mp4", prefix="cand_")
    os.close(fd)
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(
                video_url, timeout=aiohttp.ClientTimeout(total=180),
            ) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    async for chunk in r.content.iter_chunked(1 << 16):
                        f.write(chunk)
        r = score_shot_identity(tmp, embedding)
        return r["score"]
    except Exception:
        return None
    finally:
        try: os.unlink(tmp)
        except OSError: pass
