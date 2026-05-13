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


app = FastAPI(title="Video Pipeline")


@app.post("/api/runs")
async def start_run(req: RunRequest) -> dict[str, str]:
    thread_id = f"run-{uuid.uuid4().hex[:8]}"
    ctx = RunContext(
        thread_id=thread_id,
        queue=asyncio.Queue(),
        state={"phase": "starting"},
    )
    _runs[thread_id] = ctx
    ctx.task = asyncio.create_task(_execute(ctx, req))
    return {"thread_id": thread_id}


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
# Static frontend
# =============================================================================

_FRONTEND = Path(__file__).resolve().parent / "frontend"
if _FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
