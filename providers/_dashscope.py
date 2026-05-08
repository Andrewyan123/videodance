"""DashScope async submit-and-poll helper. 给 image 和 video provider 复用."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import aiohttp


async def submit_and_poll(
    submit_url: str,
    body: dict[str, Any],
    *,
    api_key: str | None = None,
    poll_interval: float = 3.0,
    timeout_sec: float = 900.0,
) -> dict[str, Any]:
    """提交 DashScope 异步任务并轮询. 返回最终 output dict.
    各模型 output 结构不同 (t2i 用 results[0].url, i2v 用 video_url), 由调用方解析.
    """
    api_key = api_key or os.environ["DASHSCOPE_API_KEY"]
    submit_headers = {
        "Authorization": f"Bearer {api_key}",
        "X-DashScope-Async": "enable",
        "Content-Type": "application/json",
    }
    poll_headers = {"Authorization": f"Bearer {api_key}"}

    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            submit_url, json=body, headers=submit_headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            text = await r.text()
            if r.status >= 400:
                raise RuntimeError(f"DashScope submit HTTP {r.status}: {text[:500]}")
            data = json.loads(text)

        task_id = data["output"]["task_id"]
        poll_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        deadline = asyncio.get_event_loop().time() + timeout_sec

        while True:
            await asyncio.sleep(poll_interval)
            async with sess.get(
                poll_url, headers=poll_headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                text = await r.text()
                if r.status >= 400:
                    raise RuntimeError(f"DashScope poll HTTP {r.status}: {text[:500]}")
                tdata = json.loads(text)

            out = tdata.get("output", {})
            status = out.get("task_status")
            if status == "SUCCEEDED":
                return out
            if status in ("FAILED", "UNKNOWN", "CANCELED"):
                raise RuntimeError(f"DashScope task {task_id} {status}: {out}")
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(f"DashScope task {task_id} timeout {timeout_sec}s")
