"""
Video Generation Pipeline - LangGraph
=====================================

架构:
    Planner -> CharacterSheets -> [Shot Subgraph x N (parallel)] -> Stitcher
                                       |
                                       └── Keyframe -> Video -> Critic -> (retry?)

模型/提示词解耦:
    - providers/  各家 backend (LLM / Image / Video) 实现, env 切换
    - prompts/    提示词模板 (planner, critic, shot builders)

依赖:
    pip install langgraph langgraph-checkpoint-sqlite anthropic openai aiofiles aiohttp python-dotenv

配置:
    所有 key 和 provider 选择从项目根 .env 加载 (见 .env.example).
    Shell env 优先级 > .env, 方便 CI / 临时覆盖.
"""
from __future__ import annotations

import asyncio
import json
import operator
import os
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, TypedDict

import aiohttp
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from prompts import (
    CHARACTER_ANGLES,
    PLANNER_SYSTEM,
    build_character_ref_prompt,
    build_keyframe_prompt,
    build_planner_user,
    build_video_prompt,
)
from providers import (
    ImageEditResult,
    ImageResult,
    VideoResult,
    build_image_edit_provider,
    build_image_provider,
    build_llm_provider,
    build_video_provider,
)

# =============================================================================
# 1. State Schema
# =============================================================================


@dataclass
class CharacterSheet:
    """角色多角度参考, 跨 shot 维持一致性的核心"""
    char_id: str
    name: str
    description: str
    ref_image_urls: list[str] = field(default_factory=list)


@dataclass
class Shot:
    """单个镜头的完整描述.

    Phase 3.1 加了富字段(shot_type / camera_angle / camera_movement / emotion / scene_ref /
    dialogue_speaker / dialogue_text)和 action_start/end + last_keyframe_url.
    Phase 4.1 加了 critic-driven retry 字段 (retry_seed / retry_force_backend / last_backend_used).
    向后兼容: 旧 `prompt` 仍 = action, 旧 `camera` 仍 = 拼好的字符串.
    """
    shot_id: str
    index: int
    prompt: str
    duration_sec: float
    character_ids: list[str]
    camera: str
    # === 生成产物 ===
    keyframe_url: str | None = None             # 单帧策略 / first_last 的首帧
    last_keyframe_url: str | None = None        # Phase 3.1: first_last 的尾帧
    video_url: str | None = None
    last_frame_url: str | None = None           # i2v 输出的末帧 (链式 first→last)
    # === Phase 3.1 富字段 (来自 ShotV1) ===
    shot_type: str = "medium"
    camera_angle: str = "eye_level"
    camera_movement: str = "static"
    emotion: str = "neutral"
    scene_ref: str | None = None
    action_start: str | None = None
    action_end: str | None = None
    dialogue_speaker: str | None = None
    dialogue_text: str | None = None
    # === Phase 4.1 critic-driven retry (critic_node 写, video_node 读, 用完清) ===
    retry_seed: int | None = None
    retry_force_backend: str | None = None      # profile-style name (router 解析)
    last_backend_used: str | None = None        # factory name (DB 落库用)
    # === 状态 ===
    status: Literal["pending", "generating", "done", "failed"] = "pending"
    retry_count: int = 0
    cost_usd: float = 0.0
    critic_notes: str = ""


def merge_shots(left: dict[str, Shot], right: dict[str, Shot]) -> dict[str, Shot]:
    """Reducer: 并行 shot worker 写回时按 shot_id merge, 后写覆盖前写"""
    return {**left, **right}


class PipelineState(TypedDict):
    user_prompt: str
    target_duration_sec: float
    profile_id: str  # Phase 2 加: short_drama / anime / cinema / commercial
    thread_id: str   # Phase 4.1: critic store 落库需要
    global_style: str
    character_sheets: dict[str, CharacterSheet]
    shot_list: list[Shot]
    shots: Annotated[dict[str, Shot], merge_shots]
    final_video_url: str | None
    total_cost_usd: Annotated[float, operator.add]
    dry_run: bool


class ShotState(TypedDict):
    """子图 state. 键名故意与 PipelineState 错开 — 子图退出时同名键会回写父级,
    并行 fanout 时多个子图同步写非 Annotated 父键会触发 InvalidUpdateError."""
    shot: Shot
    char_sheets: dict[str, CharacterSheet]
    style: str
    prev_last_frame_url: str | None
    is_dry_run: bool
    keyframe_strategy: str  # Phase 3.1: "single" | "first_last" | "n_grid"
    pipeline_profile_id: str  # Phase 3.2: video_node 用 router 时需要 (避开父 state 同名键碰撞)
    pipeline_thread_id: str  # Phase 4.1: critic store 落库需要


# =============================================================================
# 2. Provider singletons + dry-run wrappers
#    Provider 在 import 时构造一次; 切换 vendor 改 env 即可, 不动节点代码.
# =============================================================================

LLM_PROVIDER = build_llm_provider()
IMAGE_PROVIDER = build_image_provider()
IMAGE_EDIT_PROVIDER = build_image_edit_provider()
VIDEO_PROVIDER = build_video_provider()


async def _gen_image(
    prompt: str, *, dry_run: bool, ref_images: list[str] | None = None,
) -> ImageResult:
    if dry_run:
        await asyncio.sleep(0.05)
        return ImageResult(url=f"mock://image/{uuid.uuid4().hex[:8]}.png")
    return await IMAGE_PROVIDER.generate(prompt, ref_images=ref_images)


async def _gen_image_edit(
    anchor_path: str, instruction: str, *, dry_run: bool,
) -> ImageEditResult:
    """i2i wrapper, 同 _gen_image 的范式 (dry_run 短路, 真路径调 provider)."""
    if dry_run:
        await asyncio.sleep(0.05)
        return ImageEditResult(url=f"mock://image_edit/{uuid.uuid4().hex[:8]}.png")
    return await IMAGE_EDIT_PROVIDER.edit(anchor_path, instruction)


async def _gen_video(
    *, prompt: str, first_frame_url: str, duration_sec: float, dry_run: bool,
) -> VideoResult:
    if dry_run:
        await asyncio.sleep(0.1)
        vid = uuid.uuid4().hex[:8]
        return VideoResult(
            video_url=f"mock://video/{vid}.mp4",
            last_frame_url=f"mock://image/{vid}_last.png",
        )
    return await VIDEO_PROVIDER.generate(
        prompt=prompt, first_frame_url=first_frame_url, duration_sec=duration_sec,
    )


async def _vlm_critic_check(
    keyframe_url: str, video_url: str, expected_chars: list[CharacterSheet],
    *, dry_run: bool,
) -> tuple[bool, str, float]:
    """VLM 检查桩. 暂时直 pass; 接入真 VLM 后改成走 LLM_PROVIDER 或独立 vlm provider."""
    if dry_run:
        await asyncio.sleep(0.02)
        return True, "ok (dry-run)", 0.0
    return True, "skipped (no VLM wired)", 0.0


# =============================================================================
# 3. Nodes
# =============================================================================


async def plan_node(state: PipelineState) -> dict[str, Any]:
    """Planner: 用户 prompt -> shot list + character sheets + global style.

    Phase 2: 改用 storyboard.generate_storyboard (严格 Pydantic schema + jsonrepair
    + 引用解析). 输出新 schema (含 shot_type/camera.angle/movement/emotion/dialogue
    等丰富字段) 后, 转换成现有 Shot/CharacterSheet dataclass 保持下游兼容.
    多余字段 (dialogue/emotion/scene_ref/props) 暂时丢弃, 等 Phase 3+ 接入 keyframe /
    audio 节点时再串起来.
    """
    if state["dry_run"]:
        chars = {"alice": CharacterSheet("alice", "Alice", "young woman, red hair")}
        shots = [
            Shot(shot_id=f"s{i:02d}", index=i,
                 prompt=f"Alice walks through scene {i}",
                 duration_sec=5.0, character_ids=["alice"],
                 camera="medium shot")
            for i in range(3)
        ]
        return {
            "global_style": "cinematic, golden hour, 35mm film",
            "character_sheets": chars,
            "shot_list": shots,
            "shots": {s.shot_id: s for s in shots},
        }

    from profiles import get_profile
    from storyboard.planner import generate_storyboard

    profile = get_profile(state.get("profile_id") or "short_drama")
    sb, report, metrics = await generate_storyboard(
        user_prompt=state["user_prompt"],
        target_duration_sec=state["target_duration_sec"],
        profile=profile,
    )
    print(f"[plan] {report.summary()} (LLM calls={metrics['n_llm_calls']}, "
          f"repair={metrics['last_repair_mode']})")
    if report.missing_chars:
        print(f"[plan] WARN @char_id 未在 asset 库: {report.missing_chars} "
              f"(运行时会走 t2i fallback)")

    # 转换 v1 schema → 现有 dataclass
    chars = {
        c.char_id: CharacterSheet(
            char_id=c.char_id, name=c.name, description=c.description,
        )
        for c in sb.characters
    }
    shots = [
        Shot(
            shot_id=s.shot_id, index=s.index,
            prompt=s.action,  # ShotV1.action -> Shot.prompt (向后兼容)
            duration_sec=s.duration_sec,
            character_ids=s.character_ids,
            camera=f"{s.shot_type}, {s.camera.as_string()}",  # 拼字符串(向后兼容)
            # Phase 3.1: 完整富字段
            shot_type=s.shot_type,
            camera_angle=s.camera.angle,
            camera_movement=s.camera.movement,
            emotion=s.emotion,
            scene_ref=s.scene_ref,
            action_start=s.action_start,
            action_end=s.action_end,
            dialogue_speaker=s.dialogue.speaker if s.dialogue else None,
            dialogue_text=s.dialogue.text if s.dialogue else None,
        )
        for s in sb.shots
    ]
    return {
        "global_style": sb.global_style,
        "character_sheets": chars,
        "shot_list": shots,
        "shots": {s.shot_id: s for s in shots},
        "total_cost_usd": 0.01,  # rough planner cost
    }


async def character_sheet_node(state: PipelineState) -> dict[str, Any]:
    """为每个角色生成多角度参考图. 一次性, 之后所有 shot 共用.

    Phase 1 集成: 若 char_id 已在资产库 (assets.store) 中存在且有 ref_image_urls,
    直接 retrieval, 跳过 T2I. 不存在则保持原 in-pipeline 生成行为 (临时, 不写库).
    要持久化建档, 用 `python -m assets create ...`.
    """
    from assets import store as asset_store

    sheets = state["character_sheets"]
    cost = 0.0

    async def gen_one(char: CharacterSheet) -> CharacterSheet:
        nonlocal cost

        # 1. 尝试从 store 拿
        existing = asset_store.get_character(char.char_id)
        if existing and existing.ref_image_urls:
            char.ref_image_urls = existing.ref_image_urls
            print(f"[character_sheet] cache hit {char.char_id} "
                  f"({len(existing.ref_image_urls)} refs from store)")
            return char

        # 2. miss → 临时生成 (不写库, 因为 t2i 返回的 URL 会过期)
        urls = []
        for angle in CHARACTER_ANGLES:
            result = await _gen_image(
                build_character_ref_prompt(char.description, angle),
                dry_run=state["dry_run"],
            )
            urls.append(result.url)
            cost += result.cost_usd
        char.ref_image_urls = urls
        return char

    updated = await asyncio.gather(*[gen_one(c) for c in sheets.values()])
    return {
        "character_sheets": {c.char_id: c for c in updated},
        "total_cost_usd": cost,
    }


def fanout_shots(state: PipelineState) -> list[Send]:
    """用 Send API 并行启动所有 shot subgraph.
    第一版并行生成所有 shots, 用 character_sheets 做一致性. 严格 last->first 链需改 sequential.
    """
    from profiles import get_profile
    profile_id = state.get("profile_id") or "short_drama"
    profile = get_profile(profile_id)

    sends = []
    sorted_shots = sorted(state["shot_list"], key=lambda s: s.index)
    for shot in sorted_shots:
        sends.append(Send("shot_subgraph", {
            "shot": shot,
            "char_sheets": state["character_sheets"],
            "style": state["global_style"],
            "prev_last_frame_url": None,
            "is_dry_run": state["dry_run"],
            "keyframe_strategy": profile.keyframe_strategy,
            "pipeline_profile_id": profile_id,  # Phase 3.2: video_node router 用
            "pipeline_thread_id": state.get("thread_id") or "ad-hoc",  # Phase 4.1: critic DB
        }))
    return sends


# ---- Shot subgraph nodes ----

async def keyframe_node(state: ShotState) -> dict[str, Any]:
    """根据 shot prompt + char refs + global style 生成关键帧.

    Phase 1.3 i2i 范式: 有 local anchor → IMAGE_EDIT_PROVIDER + instruction;
    没 anchor → t2i 回退.
    Phase 3.1: profile.keyframe_strategy 决定单帧 vs 首尾双帧:
      - "single":      1 帧 → shot.keyframe_url
      - "first_last":  2 帧 → shot.keyframe_url (first) + shot.last_keyframe_url (last),
                       两端态用 LLM 给的 shot.action_start / action_end 拼;
                       缺 LLM 字段时规则法 fallback.
    富字段 (shot_type / camera_angle / camera_movement / emotion) 都进 i2i instruction.
    """
    import os
    from generation.keyframe_prompt import build_keyframe_instruction, build_first_last_pair

    shot = state["shot"]
    strategy = state.get("keyframe_strategy") or "single"

    # 找第一个能用的本地 anchor (Phase 1.3 行为不变)
    anchor_path = None
    for cid in shot.character_ids:
        for url in state["char_sheets"][cid].ref_image_urls:
            if url.startswith("file://"):
                p = url[len("file://"):]
                if os.path.isfile(p):
                    anchor_path = p
                    break
        if anchor_path:
            break

    # 拼 instruction 时优先用富字段 (Phase 3.1); 没有就 fallback 到 shot.camera 字符串
    use_rich = bool(shot.shot_type and shot.camera_angle)

    if strategy == "first_last":
        if use_rich:
            first_instr, last_instr = build_first_last_pair(
                global_style=state["style"],
                shot_type=shot.shot_type,
                camera_angle=shot.camera_angle,
                camera_movement=shot.camera_movement,
                action=shot.prompt,
                action_start=shot.action_start,
                action_end=shot.action_end,
                emotion=shot.emotion,
                scene_ref=shot.scene_ref,
            )
        else:
            # legacy path (无富字段): 退到老 build_keyframe_prompt + 规则法 hint
            base = build_keyframe_prompt(state["style"], shot.camera, shot.prompt)
            first_instr = base + ", frozen at the BEGINNING moment, initial body pose"
            last_instr = base + ", frozen at the ENDING moment, final body pose"

        if anchor_path and not state["is_dry_run"]:
            print(f"[keyframe] first_last i2i shot={shot.shot_id} anchor={os.path.basename(anchor_path)}")
            r1 = await _gen_image_edit(anchor_path, first_instr, dry_run=state["is_dry_run"])
            r2 = await _gen_image_edit(anchor_path, last_instr, dry_run=state["is_dry_run"])
            shot.keyframe_url = r1.url
            shot.last_keyframe_url = r2.url
            shot.cost_usd += r1.cost_usd + r2.cost_usd
        else:
            if not state["is_dry_run"]:
                print(f"[keyframe] first_last t2i fallback shot={shot.shot_id}")
            refs = []
            for cid in shot.character_ids:
                refs.extend(state["char_sheets"][cid].ref_image_urls)
            r1 = await _gen_image(first_instr, dry_run=state["is_dry_run"], ref_images=refs)
            r2 = await _gen_image(last_instr, dry_run=state["is_dry_run"], ref_images=refs)
            shot.keyframe_url = r1.url
            shot.last_keyframe_url = r2.url
            shot.cost_usd += r1.cost_usd + r2.cost_usd

    else:  # "single" or unknown → single
        if use_rich:
            instruction = build_keyframe_instruction(
                global_style=state["style"],
                shot_type=shot.shot_type,
                camera_angle=shot.camera_angle,
                camera_movement=shot.camera_movement,
                action=shot.prompt,
                emotion=shot.emotion,
                scene_ref=shot.scene_ref,
            )
        else:
            instruction = build_keyframe_prompt(state["style"], shot.camera, shot.prompt)

        if anchor_path and not state["is_dry_run"]:
            print(f"[keyframe] single i2i shot={shot.shot_id} anchor={os.path.basename(anchor_path)}")
            result_edit = await _gen_image_edit(anchor_path, instruction, dry_run=state["is_dry_run"])
            shot.keyframe_url = result_edit.url
            shot.cost_usd += result_edit.cost_usd
        else:
            if not state["is_dry_run"]:
                print(f"[keyframe] single t2i fallback shot={shot.shot_id}")
            refs = []
            for cid in shot.character_ids:
                refs.extend(state["char_sheets"][cid].ref_image_urls)
            result = await _gen_image(instruction, dry_run=state["is_dry_run"], ref_images=refs)
            shot.keyframe_url = result.url
            shot.cost_usd += result.cost_usd

    shot.status = "generating"
    return {"shot": shot}


async def video_node(state: ShotState) -> dict[str, Any]:
    """关键帧 -> i2v 视频.

    Phase 3.2: 用 generation/router.py 按 profile 选 backend.
    Phase 4.1: 读 shot.retry_seed / retry_force_backend, 传给 router (critic 设置的 retry 提示).
    Phase 4.2: 用 generation/video_prompt.py 拼富字段 instruction (motion arc / 镜头运动 /
               情绪 / 对白), 而不是只传 action 一句话.
    """
    from generation.video_prompt import build_video_instruction

    shot = state["shot"]
    first_frame = state.get("prev_last_frame_url") or shot.keyframe_url
    assert first_frame, "need a first frame"

    # 双帧策略时, 把 last_keyframe_url 传给 router
    last_frame_in = shot.last_keyframe_url if state.get("keyframe_strategy") == "first_last" else None

    # Phase 4.1: 读 critic 上次的 retry 提示
    seed = shot.retry_seed
    force_backend = shot.retry_force_backend

    # Phase 4.2: 富字段 instruction (有富字段就用, 否则 fallback 到旧 build_video_prompt)
    if shot.shot_type:  # Phase 3.1+ Shot 才有这些字段; 老 Shot 没有时用 fallback
        video_instr = build_video_instruction(
            action=shot.prompt,
            action_start=shot.action_start,
            action_end=shot.action_end,
            camera_movement=shot.camera_movement,
            emotion=shot.emotion,
            dialogue_text=shot.dialogue_text,
        )
    else:
        video_instr = build_video_prompt(shot.prompt)

    if state["is_dry_run"]:
        # dry_run 短路: 不调 router
        result = await _gen_video(
            prompt=video_instr,
            first_frame_url=first_frame,
            duration_sec=shot.duration_sec,
            dry_run=True,
        )
        shot.last_backend_used = "mock"
    else:
        from profiles import get_profile
        from generation.router import generate_video
        profile = get_profile(state.get("pipeline_profile_id") or "short_drama")

        if shot.retry_count > 0:
            print(f"[video] retry #{shot.retry_count} shot={shot.shot_id} "
                  f"seed={seed} force_backend={force_backend}")

        result, backend_used = await generate_video(
            profile=profile,
            prompt=video_instr,
            first_frame_url=first_frame,
            last_frame_url=last_frame_in,
            duration_sec=shot.duration_sec,
            seed=seed,
            force_backend=force_backend,
        )
        shot.last_backend_used = backend_used

    # 用过 retry 提示就清掉, 防止下次错用
    shot.retry_seed = None
    shot.retry_force_backend = None

    shot.video_url = result.video_url
    shot.last_frame_url = result.last_frame_url
    shot.cost_usd += result.cost_usd
    return {"shot": shot}


async def _download_video_for_critic(url: str) -> tuple[str, bool]:
    """拿到本地路径给 critic 抽帧. 返回 (path, owns_tmp_file).
    owns_tmp_file=True 时调用方负责清理; False 表示这是用户已有的文件.
    """
    if url.startswith("file://"):
        return url[len("file://"):], False
    import tempfile
    fd, tmp_path = tempfile.mkstemp(suffix=".mp4", prefix="critic_")
    os.close(fd)
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url, timeout=aiohttp.ClientTimeout(total=300)) as r:
            r.raise_for_status()
            with open(tmp_path, "wb") as f:
                async for chunk in r.content.iter_chunked(1 << 16):
                    f.write(chunk)
    return tmp_path, True


async def critic_node(state: ShotState) -> dict[str, Any]:
    """Phase 4.1: 真实 identity 评分驱动 retry.

    流程:
      1. dry_run / 没 video / 没 character embedding → 直 pass
      2. 下载视频 → InsightFace embedding → cos sim vs character.embedding
      3. 落库 (data/critic_scores.db) 不论 pass/fail 都记一笔
      4. 根据 profile.consistency_threshold_identity 和 retry_count 决定:
         pass / retry_seed / retry_backend / fail
      5. 通过 shot.retry_seed / retry_force_backend 把决策传给下次 video_node
    """
    shot = state["shot"]

    # dry_run: mock pass
    if state["is_dry_run"]:
        shot.status = "done"
        shot.critic_notes = "ok (dry-run)"
        return {"shot": shot}

    # 没视频: 直接 fail (video_node 出错了)
    if not shot.video_url:
        shot.status = "failed"
        shot.critic_notes = "no video_url (upstream failure)"
        return {"shot": shot}

    # 找 character embedding
    from assets import store as asset_store
    char_emb = None
    char_id_for_log = None
    for cid in shot.character_ids:
        card = asset_store.get_character(cid)
        if card and card.embedding:
            char_emb = card.embedding
            char_id_for_log = cid
            break

    if char_emb is None:
        # 没参考: 跳过 identity 检查 (后续 Phase 4.2 加 scene/narrative 维度可填补)
        shot.status = "done"
        shot.critic_notes = "skipped (no character embedding in asset store)"
        return {"shot": shot}

    # 下载视频 + 抽帧 + ArcFace score
    from critic.identity import score_shot_identity
    from critic import store as critic_store
    from critic.policy import decide_next
    from profiles import get_profile

    tmp_path, owns_tmp = await _download_video_for_critic(shot.video_url)
    try:
        score_result = score_shot_identity(tmp_path, char_emb)
    finally:
        if owns_tmp:
            try: os.unlink(tmp_path)
            except OSError: pass

    score = score_result["score"]
    profile = get_profile(state.get("pipeline_profile_id") or "short_drama")
    threshold = profile.consistency_threshold_identity
    passed = (score is not None) and (score >= threshold)

    # 落库 (无论 pass/fail)
    critic_store.record_score(
        thread_id=state.get("pipeline_thread_id") or "ad-hoc",
        shot_id=shot.shot_id,
        dimension="identity",
        score=score,
        threshold=threshold,
        passed=passed,
        backend=shot.last_backend_used,
        retry_count=shot.retry_count,
        video_url=shot.video_url,
        detail={
            "char_id": char_id_for_log,
            "frame_time_sec": score_result.get("frame_time_sec"),
            "reason": score_result.get("reason"),
        },
    )

    # 决策
    decision = decide_next(
        score=score,
        threshold=threshold,
        retry_count=shot.retry_count,
        profile_escalation=profile.escalation_policy,
        profile_retry_budget=profile.retry_budget,
        profile_video_backend_fallback=profile.video_backend_fallback,
    )
    shot.critic_notes = (
        f"identity={score if score is None else f'{score:.3f}'} "
        f"thr={threshold:.2f} → {decision.action}: {decision.reason}"
    )
    print(f"[critic] shot={shot.shot_id} retry={shot.retry_count} {shot.critic_notes}")

    if decision.action == "pass":
        shot.status = "done"
    elif decision.action == "retry_seed":
        shot.status = "failed"
        shot.retry_seed = decision.next_seed
    elif decision.action == "retry_backend":
        shot.status = "failed"
        shot.retry_force_backend = decision.next_backend
    else:  # "fail"
        shot.status = "failed"
        # 不设 retry 提示 → critic_router 看到 failed + 无提示, finalize 收尾

    return {"shot": shot}


def critic_router(state: ShotState) -> Literal["video_node", "finalize_shot"]:
    """Phase 4.1: 按 critic 决策路由.

    critic_node 已经设好 shot.status + (retry_seed 或 retry_force_backend).
      status=done                    → finalize
      status=failed + 有 retry 提示  → video_node (retry_count++, 清 video_url)
      status=failed + 无 retry 提示  → finalize (mark failed, budget 用尽 or human gate)
    """
    shot = state["shot"]
    if shot.status == "done":
        return "finalize_shot"

    has_hint = shot.retry_seed is not None or shot.retry_force_backend is not None
    if has_hint:
        shot.retry_count += 1
        shot.video_url = None  # 重生成
        return "video_node"

    return "finalize_shot"  # failed, 无 retry 提示, 收尾


async def finalize_shot_node(state: ShotState) -> dict[str, Any]:
    """写回全局 state - 通过 reducer 安全 merge"""
    shot = state["shot"]
    if shot.status != "done":
        shot.status = "failed"
    return {
        "shots": {shot.shot_id: shot},
        "total_cost_usd": shot.cost_usd,
    }


# ---- Stitcher ----

async def _download(session: aiohttp.ClientSession, url: str, dst: str) -> None:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            async for chunk in r.content.iter_chunked(1 << 16):
                f.write(chunk)


async def stitch_node(state: PipelineState) -> dict[str, Any]:
    """ffmpeg 拼接所有完成的 shot. 失败的 shot 跳过"""
    sorted_shots = sorted(state["shots"].values(), key=lambda s: s.index)
    valid = [s for s in sorted_shots if s.status == "done" and s.video_url]

    if state["dry_run"]:
        await asyncio.sleep(0.05)
        return {"final_video_url": f"mock://final/{uuid.uuid4().hex[:8]}.mp4"}

    if not valid:
        print("[stitch] no valid shots, skipping")
        return {"final_video_url": None}

    out_dir = os.environ.get("STITCH_OUT_DIR", "/tmp/video1.0_stitch")
    os.makedirs(out_dir, exist_ok=True)
    run_id = uuid.uuid4().hex[:8]
    work_dir = os.path.join(out_dir, run_id)
    os.makedirs(work_dir, exist_ok=True)

    print(f"[stitch] downloading {len(valid)} shots -> {work_dir}")
    local_paths: list[str] = []
    async with aiohttp.ClientSession() as sess:
        tasks = []
        for s in valid:
            local = os.path.join(work_dir, f"shot_{s.index:03d}.mp4")
            local_paths.append(local)
            tasks.append(_download(sess, s.video_url, local))
        await asyncio.gather(*tasks)

    list_path = os.path.join(work_dir, "concat.txt")
    with open(list_path, "w") as f:
        for p in local_paths:
            f.write(f"file '{p}'\n")

    final_path = os.path.join(out_dir, f"final_{run_id}.mp4")

    async def _ffmpeg(args: list[str]) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        return proc.returncode or 0, err.decode("utf-8", errors="replace")

    code, err = await _ffmpeg([
        "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", final_path,
    ])
    if code != 0:
        print(f"[stitch] -c copy failed (code={code}), retrying with re-encode")
        print(f"  ffmpeg stderr (last 400 chars): {err[-400:]}")
        code, err = await _ffmpeg([
            "-y", "-f", "concat", "-safe", "0", "-i", list_path,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            final_path,
        ])
        if code != 0:
            raise RuntimeError(f"ffmpeg concat failed: {err[-800:]}")

    size = os.path.getsize(final_path)
    print(f"[stitch] OK: {final_path} ({size} bytes, {len(valid)} shots)")
    return {"final_video_url": f"file://{final_path}"}


# =============================================================================
# 4. Build the graph
# =============================================================================


def build_shot_subgraph():
    sg = StateGraph(ShotState)
    sg.add_node("keyframe_node", keyframe_node)
    sg.add_node("video_node", video_node)
    sg.add_node("critic_node", critic_node)
    sg.add_node("finalize_shot", finalize_shot_node)

    sg.add_edge(START, "keyframe_node")
    sg.add_edge("keyframe_node", "video_node")
    sg.add_edge("video_node", "critic_node")
    sg.add_conditional_edges("critic_node", critic_router,
                             {"video_node": "video_node", "finalize_shot": "finalize_shot"})
    sg.add_edge("finalize_shot", END)
    return sg.compile()


def build_main_graph(checkpointer):
    g = StateGraph(PipelineState)
    g.add_node("plan", plan_node)
    g.add_node("character_sheets", character_sheet_node)
    g.add_node("shot_subgraph", build_shot_subgraph())
    g.add_node("stitch", stitch_node)

    g.add_edge(START, "plan")
    g.add_edge("plan", "character_sheets")
    g.add_conditional_edges("character_sheets", fanout_shots, ["shot_subgraph"])
    g.add_edge("shot_subgraph", "stitch")
    g.add_edge("stitch", END)

    return g.compile(checkpointer=checkpointer)


# =============================================================================
# 5. Entry point
# =============================================================================


async def run_pipeline(
    user_prompt: str,
    target_duration_sec: float = 30.0,
    profile_id: str = "short_drama",
    dry_run: bool = True,
    thread_id: str | None = None,
) -> PipelineState:
    thread_id = thread_id or f"video-{uuid.uuid4().hex[:8]}"

    async with AsyncSqliteSaver.from_conn_string("./video_pipeline.db") as cp:
        graph = build_main_graph(cp)

        config = {"configurable": {"thread_id": thread_id}}
        initial: PipelineState = {
            "user_prompt": user_prompt,
            "target_duration_sec": target_duration_sec,
            "profile_id": profile_id,
            "thread_id": thread_id,  # Phase 4.1: critic DB 落库用
            "global_style": "",
            "character_sheets": {},
            "shot_list": [],
            "shots": {},
            "final_video_url": None,
            "total_cost_usd": 0.0,
            "dry_run": dry_run,
        }

        async for event in graph.astream(initial, config=config, stream_mode="updates"):
            for node_name, update in event.items():
                keys = list(update.keys()) if isinstance(update, dict) else "(subgraph)"
                print(f"[{node_name}] keys updated: {keys}")

        final = await graph.aget_state(config)
        return final.values


async def resume_pipeline(thread_id: str) -> PipelineState:
    """断点续跑. 从最后一个成功的 checkpoint 继续"""
    async with AsyncSqliteSaver.from_conn_string("./video_pipeline.db") as cp:
        graph = build_main_graph(cp)
        config = {"configurable": {"thread_id": thread_id}}
        async for event in graph.astream(None, config=config, stream_mode="updates"):
            for node_name, update in event.items():
                keys = list(update.keys()) if isinstance(update, dict) else "(subgraph)"
                print(f"[resume:{node_name}] {keys}")
        final = await graph.aget_state(config)
        return final.values


if __name__ == "__main__":
    result = asyncio.run(run_pipeline(
        user_prompt="A young woman with red hair walks through a misty forest at dawn, "
                    "discovers a glowing crystal, picks it up.",
        target_duration_sec=30.0,
        dry_run=True,
    ))
    print("\n=== Final ===")
    print(f"Final video: {result['final_video_url']}")
    print(f"Total cost: ${result['total_cost_usd']:.4f}")
    print(f"Shots completed: {sum(1 for s in result['shots'].values() if s.status == 'done')}/{len(result['shots'])}")
