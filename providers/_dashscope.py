"""DashScope async submit-and-poll helper. 给 image 和 video provider 复用."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from typing import Any

import aiohttp

log = logging.getLogger("video_pipeline.dashscope")

# 429 指数退避重试上限. 实际等待时间 = 2^attempt + jitter, 封顶 _MAX_BACKOFF.
_MAX_429_RETRIES = 6
_MAX_BACKOFF = 30.0


async def _post_with_429_retry(
    sess: aiohttp.ClientSession,
    url: str,
    *,
    json_body: dict[str, Any],
    headers: dict[str, str],
    timeout_sec: float = 60.0,
) -> dict[str, Any]:
    """POST + 429 指数退避. 其他 4xx/5xx 直接 raise."""
    for attempt in range(_MAX_429_RETRIES + 1):
        async with sess.post(
            url, json=json_body, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout_sec),
        ) as r:
            text = await r.text()
            if r.status == 429:
                if attempt >= _MAX_429_RETRIES:
                    raise RuntimeError(
                        f"DashScope 429 after {attempt} retries: {text[:500]}"
                    )
                wait = min(2 ** attempt + random.random(), _MAX_BACKOFF)
                log.warning(
                    "DashScope 429, attempt %d/%d, sleep %.1fs",
                    attempt + 1, _MAX_429_RETRIES, wait,
                )
                await asyncio.sleep(wait)
                continue
            if r.status >= 400:
                raise RuntimeError(f"DashScope submit HTTP {r.status}: {text[:500]}")
            return json.loads(text)
    raise AssertionError("unreachable")


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
    submit 阶段命中 429 自动指数退避重试. poll 阶段的 429 也宽容处理 (继续轮询).
    """
    api_key = api_key or os.environ["DASHSCOPE_API_KEY"]
    submit_headers = {
        "Authorization": f"Bearer {api_key}",
        "X-DashScope-Async": "enable",
        "Content-Type": "application/json",
    }
    poll_headers = {"Authorization": f"Bearer {api_key}"}

    async with aiohttp.ClientSession() as sess:
        data = await _post_with_429_retry(
            sess, submit_url, json_body=body, headers=submit_headers, timeout_sec=60.0,
        )

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
                if r.status == 429:
                    # 轮询命中 429 是上游 burst, 跳过这次轮询继续等下次 tick.
                    log.warning("DashScope poll 429 for %s, will retry next tick", task_id)
                    continue
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
