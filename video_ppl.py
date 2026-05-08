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
    ImageResult,
    VideoResult,
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
    """单个镜头的完整描述"""
    shot_id: str
    index: int
    prompt: str
    duration_sec: float
    character_ids: list[str]
    camera: str
    keyframe_url: str | None = None
    video_url: str | None = None
    last_frame_url: str | None = None
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


# =============================================================================
# 2. Provider singletons + dry-run wrappers
#    Provider 在 import 时构造一次; 切换 vendor 改 env 即可, 不动节点代码.
# =============================================================================

LLM_PROVIDER = build_llm_provider()
IMAGE_PROVIDER = build_image_provider()
VIDEO_PROVIDER = build_video_provider()


async def _gen_image(
    prompt: str, *, dry_run: bool, ref_images: list[str] | None = None,
) -> ImageResult:
    if dry_run:
        await asyncio.sleep(0.05)
        return ImageResult(url=f"mock://image/{uuid.uuid4().hex[:8]}.png")
    return await IMAGE_PROVIDER.generate(prompt, ref_images=ref_images)


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
    """Planner: 用户 prompt -> shot list + character sheets + global style"""
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

    msg = await LLM_PROVIDER.ainvoke([
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content": build_planner_user(
            state["user_prompt"], state["target_duration_sec"],
        )},
    ])
    raw = msg.content.strip()
    if raw.startswith("```"):
        # 去掉首行 ```json 和末行 ```
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    plan = json.loads(raw)

    chars = {c["char_id"]: CharacterSheet(**c) for c in plan["characters"]}
    shots = [Shot(**s) for s in plan["shots"]]
    return {
        "global_style": plan["global_style"],
        "character_sheets": chars,
        "shot_list": shots,
        "shots": {s.shot_id: s for s in shots},
        "total_cost_usd": 0.01,  # rough planner cost
    }


async def character_sheet_node(state: PipelineState) -> dict[str, Any]:
    """为每个角色生成多角度参考图. 一次性, 之后所有 shot 共用"""
    sheets = state["character_sheets"]
    cost = 0.0

    async def gen_one(char: CharacterSheet) -> CharacterSheet:
        nonlocal cost
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
    sends = []
    sorted_shots = sorted(state["shot_list"], key=lambda s: s.index)
    for shot in sorted_shots:
        sends.append(Send("shot_subgraph", {
            "shot": shot,
            "char_sheets": state["character_sheets"],
            "style": state["global_style"],
            "prev_last_frame_url": None,
            "is_dry_run": state["dry_run"],
        }))
    return sends


# ---- Shot subgraph nodes ----

async def keyframe_node(state: ShotState) -> dict[str, Any]:
    """根据 shot prompt + char refs + global style 生成关键帧"""
    shot = state["shot"]
    refs = []
    for cid in shot.character_ids:
        refs.extend(state["char_sheets"][cid].ref_image_urls)

    prompt = build_keyframe_prompt(state["style"], shot.camera, shot.prompt)
    result = await _gen_image(prompt, dry_run=state["is_dry_run"], ref_images=refs)
    shot.keyframe_url = result.url
    shot.cost_usd += result.cost_usd
    shot.status = "generating"
    return {"shot": shot}


async def video_node(state: ShotState) -> dict[str, Any]:
    """关键帧 -> i2v 视频"""
    shot = state["shot"]
    first_frame = state.get("prev_last_frame_url") or shot.keyframe_url
    assert first_frame, "need a first frame"

    result = await _gen_video(
        prompt=build_video_prompt(shot.prompt),
        first_frame_url=first_frame,
        duration_sec=shot.duration_sec,
        dry_run=state["is_dry_run"],
    )
    shot.video_url = result.video_url
    shot.last_frame_url = result.last_frame_url
    shot.cost_usd += result.cost_usd
    return {"shot": shot}


async def critic_node(state: ShotState) -> dict[str, Any]:
    """VLM 检查. 二元 pass/fail"""
    shot = state["shot"]
    chars = [state["char_sheets"][c] for c in shot.character_ids]
    passed, notes, cost = await _vlm_critic_check(
        shot.keyframe_url, shot.video_url, chars, dry_run=state["is_dry_run"],
    )
    shot.cost_usd += cost
    shot.critic_notes = notes
    shot.status = "done" if passed else "failed"
    return {"shot": shot}


def critic_router(state: ShotState) -> Literal["video_node", "finalize_shot"]:
    """失败且未超过 retry 上限 -> 重生成视频; 否则结束"""
    shot = state["shot"]
    MAX_RETRIES = 2
    if shot.status == "failed" and shot.retry_count < MAX_RETRIES:
        shot.retry_count += 1
        shot.video_url = None
        return "video_node"
    return "finalize_shot"


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
