"""FastAPI server exposing the video pipeline.

Endpoints:
    POST /api/runs                  start a pipeline, returns {thread_id}
    GET  /api/runs/{tid}/events     SSE stream of high-level events
    GET  /api/runs/{tid}/state      current state snapshot (JSON)
    GET  /api/video                 serve local stitch outputs (?path=/tmp/...mp4)
    GET  /                          frontend index.html (static)

设计:
- Run 状态保存在内存 dict (单进程, 重启即丢). 多 run 并行 OK.
- 事件流通过 asyncio.Queue 解耦生产 (pipeline) 和消费 (SSE consumer).
- 客户端可以中途连入 SSE; 已发出的事件不会重放 (简单起见).
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import traceback
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger("video_pipeline.server")

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from video_ppl import build_main_graph, PipelineState

# =============================================================================
# Run state
# =============================================================================


@dataclasses.dataclass
class RunContext:
    thread_id: str
    queue: asyncio.Queue
    state: dict[str, Any]
    done: bool = False
    error: str | None = None
    task: asyncio.Task | None = None  # 保 ref 防 GC 把 task 当垃圾回收掉


_runs: dict[str, RunContext] = {}


def _to_jsonable(o: Any) -> Any:
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(o).items()}
    if isinstance(o, dict):
        return {k: _to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_to_jsonable(x) for x in o]
    return o


# =============================================================================
# Pipeline executor
# =============================================================================


async def _execute(ctx: RunContext, req: "RunRequest") -> None:
    """Run the LangGraph pipeline, push high-level events to ctx.queue."""

    async def emit(event: dict[str, Any]) -> None:
        await ctx.queue.put(event)

    try:
        await emit({"type": "started", "thread_id": ctx.thread_id, "phase": "plan"})

        async with AsyncSqliteSaver.from_conn_string("./video_pipeline.db") as cp:
            graph = build_main_graph(cp)
            initial: PipelineState = {
                "user_prompt": req.user_prompt,
                "target_duration_sec": req.target_duration_sec,
                "global_style": "",
                "character_sheets": {},
                "shot_list": [],
                "shots": {},
                "final_video_url": None,
                "total_cost_usd": 0.0,
                "dry_run": req.dry_run,
            }
            config = {"configurable": {"thread_id": ctx.thread_id}}

            async for chunk in graph.astream(
                initial, config=config, stream_mode="updates", subgraphs=True,
            ):
                # subgraphs=True 形式: (namespace_tuple, update_dict)
                # 不带 subgraphs: 直接是 update_dict. 兼容两种.
                if isinstance(chunk, tuple) and len(chunk) == 2:
                    namespace, update = chunk
                else:
                    namespace, update = (), chunk

                await _handle_update(ctx, namespace, update, emit)

            final = await graph.aget_state(config)
            await emit({
                "type": "completed",
                "final_video_url": final.values.get("final_video_url"),
                "total_cost_usd": final.values.get("total_cost_usd", 0.0),
                "shots": [
                    _to_jsonable(s)
                    for s in sorted(final.values.get("shots", {}).values(),
                                    key=lambda s: s.index)
                ],
            })
    except asyncio.CancelledError:
        # 客户端断开 / 服务关闭 — 把任务跑完, 不算错误
        log.warning("run %s cancelled", ctx.thread_id)
        ctx.error = "cancelled"
        await emit({"type": "error", "error": "cancelled (client disconnected or server shutdown)"})
        raise
    except BaseException as e:
        tb = traceback.format_exc()
        log.error("run %s failed:\n%s", ctx.thread_id, tb)
        msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        ctx.error = msg
        await emit({"type": "error", "error": msg, "traceback": tb})
    finally:
        ctx.done = True
        await emit({"type": "done"})


async def _handle_update(
    ctx: RunContext,
    namespace: tuple,
    update: Any,
    emit,
) -> None:
    """Translate raw LangGraph update into high-level frontend event."""
    if not isinstance(update, dict):
        return

    is_parent = not namespace or namespace == ()

    for node_name, node_update in update.items():
        if not isinstance(node_update, dict):
            continue

        if is_parent:
            if node_name == "plan":
                shots = node_update.get("shot_list", [])
                chars = node_update.get("character_sheets", {})
                ctx.state["phase"] = "character_sheets"
                await emit({
                    "type": "plan_done",
                    "global_style": node_update.get("global_style", ""),
                    "shot_count": len(shots),
                    "characters": [
                        {"char_id": c.char_id, "name": c.name, "description": c.description}
                        for c in chars.values()
                    ],
                    "shots": [
                        {
                            "shot_id": s.shot_id, "index": s.index,
                            "duration_sec": s.duration_sec, "camera": s.camera,
                            "prompt": s.prompt,
                        }
                        for s in shots
                    ],
                })
            elif node_name == "character_sheets":
                chars = node_update.get("character_sheets", {})
                ctx.state["phase"] = "shots"
                await emit({
                    "type": "character_sheets_done",
                    "characters": [
                        {"char_id": c.char_id, "name": c.name,
                         "ref_image_urls": c.ref_image_urls}
                        for c in chars.values()
                    ],
                })
            elif node_name == "stitch":
                ctx.state["phase"] = "done"
                await emit({
                    "type": "stitch_done",
                    "final_video_url": node_update.get("final_video_url"),
                })
            elif node_name == "shot_subgraph":
                # 父级看到的子图整体事件 (update 一般为空 dict 或 None), 忽略
                pass

        else:
            # 子图内部节点: keyframe_node / video_node / critic_node / finalize_shot
            shot = node_update.get("shot")
            if shot is not None:
                await emit({
                    "type": "shot_progress",
                    "node": node_name,
                    "shot": _to_jsonable(shot),
                })

            shots_update = node_update.get("shots")
            if shots_update:
                for sid, sh in shots_update.items():
                    await emit({
                        "type": "shot_progress",
                        "node": "finalize_shot",
                        "shot": _to_jsonable(sh),
                    })


# =============================================================================
# HTTP API
# =============================================================================


class RunRequest(BaseModel):
    user_prompt: str
    target_duration_sec: float = 15.0
    dry_run: bool = True
    profile_id: str = "short_drama"
    mode: str = "auto"  # "auto" (legacy full pipeline) | "interactive" (storyboard then pause)


app = FastAPI(title="Video Pipeline")


@app.post("/api/runs")
async def start_run(req: RunRequest) -> dict[str, Any]:
    """启动一个 run.
    - mode=auto: 跟现有行为一致, 全自动跑到底, SSE 推进度
    - mode=interactive: 只跑 planner 出 storyboard, 入库 sessions 表, 后续由
      /characters/.../propose + /shots/.../propose + /stitch 接力
    """
    thread_id = f"run-{uuid.uuid4().hex[:8]}"
    ctx = RunContext(
        thread_id=thread_id,
        queue=asyncio.Queue(),
        state={"phase": "starting"},
    )
    _runs[thread_id] = ctx

    if req.mode == "interactive":
        # 同步跑 planner, 返回 storyboard
        from profiles import get_profile
        from storyboard.planner import generate_storyboard
        from candidates import store as cand_store
        from candidates.schema import Session

        profile = get_profile(req.profile_id)
        if req.dry_run:
            # dry_run 不调 LLM, 构造一个最小 mock storyboard
            sb_json = (
                '{"schema_version":1,"global_style":"mock","characters":'
                '[{"char_id":"alice","name":"Alice","description":"mock young woman"}],'
                '"shots":[{"shot_id":"S01","index":0,"duration_sec":5,'
                '"character_ids":["alice"],"action":"mock action",'
                '"shot_type":"medium","camera":{"angle":"eye_level","movement":"static"},'
                '"emotion":"neutral"}]}'
            )
        else:
            sb, _report, _metrics = await generate_storyboard(
                user_prompt=req.user_prompt,
                target_duration_sec=req.target_duration_sec,
                profile=profile,
                check_store=False,  # 还没建 character, 跳过 store 检查
            )
            sb_json = sb.model_dump_json()

        sess = Session(
            thread_id=thread_id,
            user_prompt=req.user_prompt,
            target_duration_sec=req.target_duration_sec,
            profile_id=req.profile_id,
            storyboard_json=sb_json,
            status="proposing",
        )
        cand_store.upsert_session(sess)

        return {
            "thread_id": thread_id,
            "mode": "interactive",
            "storyboard": json.loads(sb_json),
        }

    # mode == "auto": existing behavior
    ctx.task = asyncio.create_task(_execute(ctx, req))
    return {"thread_id": thread_id, "mode": "auto"}


@app.get("/api/runs/{thread_id}/events")
async def stream_events(thread_id: str):
    ctx = _runs.get(thread_id)
    if ctx is None:
        raise HTTPException(404, f"unknown thread_id: {thread_id}")

    async def gen():
        # SSE: 每条消息以 'data: <json>\n\n' 结束
        while True:
            try:
                evt = await asyncio.wait_for(ctx.queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                # heartbeat 防止反向代理掐连接
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt.get("type") == "done":
                break

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/runs/{thread_id}/state")
async def get_state(thread_id: str) -> dict[str, Any]:
    ctx = _runs.get(thread_id)
    if ctx is None:
        raise HTTPException(404)
    return {
        "thread_id": thread_id,
        "done": ctx.done,
        "error": ctx.error,
        "state": ctx.state,
    }


@app.get("/api/video")
async def serve_video(path: str = Query(...)):
    """Serve a local stitch output (file:// URL 的 path 部分).
    Whitelist 限制只能读 STITCH_OUT_DIR 下的文件, 防 path traversal.
    """
    out_dir = os.path.realpath(os.environ.get("STITCH_OUT_DIR", "/tmp/video1.0_stitch"))
    real = os.path.realpath(path)
    if not real.startswith(out_dir + os.sep):
        raise HTTPException(403, "path outside STITCH_OUT_DIR")
    if not os.path.isfile(real):
        raise HTTPException(404)
    return FileResponse(real, media_type="video/mp4")


# =============================================================================
# Interactive endpoints (Phase 5.1)
# =============================================================================


def _emit_factory(thread_id: str):
    """给 builder 用的 emit callback: 把事件推到 SSE 队列(如果有)."""
    ctx = _runs.get(thread_id)
    async def emit(event: dict):
        if ctx:
            await ctx.queue.put(event)
    return emit


def _get_session_or_404(thread_id: str):
    from candidates import store as cand_store
    s = cand_store.get_session(thread_id)
    if s is None:
        raise HTTPException(404, f"unknown session {thread_id}")
    return s


def _get_storyboard_from_session(s) -> dict:
    return json.loads(s.storyboard_json)


@app.get("/api/runs/{thread_id}/storyboard")
async def get_storyboard(thread_id: str) -> dict[str, Any]:
    s = _get_session_or_404(thread_id)
    return _get_storyboard_from_session(s)


@app.post("/api/runs/{thread_id}/characters/{char_id}/propose")
async def propose_chars(thread_id: str, char_id: str, n: int = 4) -> dict[str, Any]:
    from candidates.builder import propose_character_variants
    from candidates import store as cand_store

    s = _get_session_or_404(thread_id)
    sb = _get_storyboard_from_session(s)

    # 找 char_id 对应的 description
    chars = sb.get("characters", [])
    target = next((c for c in chars if c["char_id"] == char_id), None)
    if target is None:
        raise HTTPException(404, f"char_id {char_id} not in storyboard")

    variants = await propose_character_variants(
        thread_id=thread_id, char_id=char_id, n=n,
        description=target.get("description", target.get("name", "")),
        profile_id=s.profile_id,
        emit=_emit_factory(thread_id),
    )
    return {"variants": [v.model_dump() for v in variants]}


@app.post("/api/runs/{thread_id}/characters/{char_id}/select")
async def select_char(thread_id: str, char_id: str, variant: int = Query(...)) -> dict[str, Any]:
    from candidates import store as cand_store
    from candidates.builder import promote_character_to_canonical
    _get_session_or_404(thread_id)
    if not cand_store.select_character_candidate(thread_id, char_id, variant):
        raise HTTPException(404, f"variant {variant} not found for char {char_id}")
    # 把 selected 提升到 canonical assets/store, 供下游 i2i 用
    promote_character_to_canonical(thread_id=thread_id, char_id=char_id)
    return {"selected": variant, "char_id": char_id, "promoted_to_assets": True}


def _build_shot_from_storyboard(sb: dict, shot_id: str):
    """从 storyboard JSON 构造 video_ppl.Shot 用于 propose."""
    from video_ppl import Shot
    shots = sb.get("shots", [])
    raw = next((s for s in shots if s["shot_id"] == shot_id), None)
    if raw is None:
        raise HTTPException(404, f"shot_id {shot_id} not in storyboard")
    cam = raw.get("camera") or {}
    dlg = raw.get("dialogue") or {}
    return Shot(
        shot_id=raw["shot_id"], index=raw["index"],
        prompt=raw.get("action", ""),
        duration_sec=raw["duration_sec"],
        character_ids=raw.get("character_ids", []),
        camera=f"{raw.get('shot_type', 'medium')}, {cam.get('angle','eye_level')}, {cam.get('movement','static')}",
        shot_type=raw.get("shot_type", "medium"),
        camera_angle=cam.get("angle", "eye_level"),
        camera_movement=cam.get("movement", "static"),
        emotion=raw.get("emotion", "neutral"),
        action_start=raw.get("action_start"),
        action_end=raw.get("action_end"),
        dialogue_speaker=dlg.get("speaker") if dlg else None,
        dialogue_text=dlg.get("text") if dlg else None,
    )


@app.post("/api/runs/{thread_id}/shots/{shot_id}/propose")
async def propose_shots(thread_id: str, shot_id: str, n: int = 3) -> dict[str, Any]:
    from candidates.builder import propose_shot_variants
    s = _get_session_or_404(thread_id)
    sb = _get_storyboard_from_session(s)
    shot = _build_shot_from_storyboard(sb, shot_id)
    variants = await propose_shot_variants(
        thread_id=thread_id, shot_id=shot_id, shot=shot, n=n,
        profile_id=s.profile_id,
        emit=_emit_factory(thread_id),
    )
    return {"variants": [v.model_dump() for v in variants]}


@app.post("/api/runs/{thread_id}/shots/{shot_id}/select")
async def select_shot(thread_id: str, shot_id: str, variant: int = Query(...)) -> dict[str, Any]:
    from candidates import store as cand_store
    _get_session_or_404(thread_id)
    if not cand_store.select_shot_candidate(thread_id, shot_id, variant):
        raise HTTPException(404, f"variant {variant} not found for shot {shot_id}")
    return {"selected": variant, "shot_id": shot_id}


@app.get("/api/runs/{thread_id}/tree")
async def get_tree(thread_id: str) -> dict[str, Any]:
    """Run 的完整树状态: storyboard + 每个 char/shot 的所有 candidates + selection state."""
    from candidates import store as cand_store
    s = _get_session_or_404(thread_id)
    sb = _get_storyboard_from_session(s)

    chars_data = []
    for c in sb.get("characters", []):
        cid = c["char_id"]
        cands = cand_store.list_character_candidates(thread_id, cid)
        chars_data.append({
            "char_id": cid,
            "name": c["name"],
            "description": c.get("description", ""),
            "candidates": [v.model_dump() for v in cands],
        })

    shots_data = []
    for sh in sb.get("shots", []):
        sid = sh["shot_id"]
        cands = cand_store.list_shot_candidates(thread_id, sid)
        shots_data.append({
            "shot_id": sid,
            "index": sh["index"],
            "duration_sec": sh["duration_sec"],
            "action": sh.get("action", ""),
            "shot_type": sh.get("shot_type", "medium"),
            "character_ids": sh.get("character_ids", []),
            "candidates": [v.model_dump() for v in cands],
        })

    return {
        "thread_id": thread_id,
        "user_prompt": s.user_prompt,
        "profile_id": s.profile_id,
        "status": s.status,
        "global_style": sb.get("global_style", ""),
        "characters": chars_data,
        "shots": shots_data,
        "final_video_url": s.final_video_url,
    }


@app.post("/api/runs/{thread_id}/stitch")
async def stitch_selected(thread_id: str) -> dict[str, Any]:
    """按 storyboard.shots 顺序拉 selected video URLs, 下载 + ffmpeg concat."""
    import uuid as _uuid
    import aiohttp
    from candidates import store as cand_store
    s = _get_session_or_404(thread_id)
    sb = _get_storyboard_from_session(s)

    # 按 shot index 顺序拉 selected
    ordered_shot_ids = [x["shot_id"] for x in sorted(sb.get("shots", []), key=lambda x: x["index"])]
    selected_urls: list[str] = []
    missing: list[str] = []
    for sid in ordered_shot_ids:
        cands = cand_store.list_shot_candidates(thread_id, sid)
        sel = [c for c in cands if c.selected]
        if sel and sel[0].video_url:
            selected_urls.append(sel[0].video_url)
        else:
            missing.append(sid)

    if missing:
        raise HTTPException(400, f"missing selection for shots: {missing}")
    if not selected_urls:
        raise HTTPException(400, "no shots selected")

    # 下载 + concat (复用 video_ppl stitch 逻辑)
    out_dir = Path(os.environ.get("STITCH_OUT_DIR", "/tmp/video1.0_stitch"))
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = _uuid.uuid4().hex[:8]
    work_dir = out_dir / f"interactive_{run_id}"
    work_dir.mkdir()

    local_paths = []
    async with aiohttp.ClientSession() as sess:
        for i, url in enumerate(selected_urls):
            p = work_dir / f"shot_{i:03d}.mp4"
            local_paths.append(p)
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=300)) as r:
                r.raise_for_status()
                with open(p, "wb") as f:
                    async for chunk in r.content.iter_chunked(1 << 16):
                        f.write(chunk)

    list_path = work_dir / "concat.txt"
    list_path.write_text("\n".join(f"file '{p}'" for p in local_paths))
    final_path = out_dir / f"final_interactive_{run_id}.mp4"

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c", "copy", str(final_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        # 回退 re-encode
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", str(final_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(500, f"ffmpeg failed: {err.decode('utf-8', errors='replace')[-400:]}")

    final_url = f"file://{final_path}"
    # 更新 session
    s.final_video_url = final_url
    s.status = "done"
    cand_store.upsert_session(s)

    return {
        "final_video_url": final_url,
        "size_bytes": final_path.stat().st_size,
        "shot_count": len(selected_urls),
    }


# =============================================================================
# Static frontend
# =============================================================================

_FRONTEND = Path(__file__).resolve().parent / "frontend"
if _FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
