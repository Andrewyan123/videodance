"""LLM providers."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from typing import Any

import aiohttp

from .base import LLMResponse

log = logging.getLogger("video_pipeline.llm")

# 429 退避: 同 _dashscope, 等价指数退避 + jitter
_MAX_429_RETRIES = 6
_MAX_BACKOFF = 30.0


_RATE_LIMIT_HINTS = ("rate limit", "rate_limit", "Throttling",
                     "exceed", "limit_requests", "too many", "429")

# 上游 5xx 类(infrastructure 故障, 跟限流处理逻辑一致 — 退避+重试)
_TRANSIENT_HINTS = ("Overloaded", "Internal server error",
                    "service unavailable", "service_unavailable",
                    "Bad Gateway", "Gateway Timeout",
                    "500", "502", "503", "504", "529")


def _is_retryable(status: int, body_text: str) -> bool:
    """判定是否退避重试. 涵盖:
    - 429 直接限流
    - 4xx/5xx wrap 上游 429 (DashScope 经常用 418 wrap)
    - 5xx (500/502/503/504/529) 临时故障
    排除:"per day / quota / daily" 这种永久性, 退避救不回.
    """
    low = body_text.lower()
    # 永久性 quota 不重试 (在 status check 前先排除)
    if "per day" in low or "daily" in low or ("quota" in low and "exceed" in low):
        return False

    if status == 429:
        return True

    if status >= 500:
        # 直 5xx, retry
        return True

    if status >= 400:
        # 4xx 包括 418-wrapped: 看 body 有限流 / 上游 5xx 关键词
        if any(h.lower() in low for h in _RATE_LIMIT_HINTS):
            return True
        if any(h.lower() in low for h in _TRANSIENT_HINTS):
            return True
    return False


# 保留旧名给可能的外部引用
_is_rate_limited = _is_retryable


# 网络层 transient: aiohttp 抛 ClientConnectionError 系列; asyncio.TimeoutError
# 在 ClientTimeout 触发时由 aiohttp wrap 抛出. 都跟 5xx 一样退避 + 重试.
_NETWORK_TRANSIENT_EXC: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    aiohttp.ServerTimeoutError,
    aiohttp.ClientConnectionError,  # 含 ClientConnectorError / ServerDisconnectedError
    aiohttp.ClientPayloadError,
)


async def _post_chat_completion(
    sess: aiohttp.ClientSession,
    url: str,
    *,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout_sec: float,
) -> dict[str, Any]:
    """POST /chat/completions + rate-limit + network-transient 指数退避.

    重试触发条件 (任一):
      - HTTP 状态码 _is_retryable (429 / 4xx-wrapped 限流 / 5xx)
      - asyncio.TimeoutError / aiohttp 网络层异常
    其他 4xx 直接 raise.
    """
    for attempt in range(_MAX_429_RETRIES + 1):
        try:
            async with sess.post(
                url, json=body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_sec),
            ) as resp:
                text = await resp.text()
                if _is_retryable(resp.status, text):
                    if attempt >= _MAX_429_RETRIES:
                        raise RuntimeError(
                            f"LLM retry exhausted after {attempt} attempts (status={resp.status}): {text[:300]}"
                        )
                    wait = min(2 ** attempt + random.random(), _MAX_BACKOFF)
                    log.warning("LLM transient (status=%d), attempt %d/%d, sleep %.1fs",
                                resp.status, attempt + 1, _MAX_429_RETRIES, wait)
                    await asyncio.sleep(wait)
                    continue
                if resp.status >= 400:
                    raise RuntimeError(f"LLM HTTP {resp.status}: {text[:500]}")
                return json.loads(text)
        except _NETWORK_TRANSIENT_EXC as e:
            if attempt >= _MAX_429_RETRIES:
                raise RuntimeError(
                    f"LLM network-transient retry exhausted after {attempt} attempts: {type(e).__name__}: {e}"
                ) from e
            wait = min(2 ** attempt + random.random(), _MAX_BACKOFF)
            log.warning("LLM %s (timeout=%.0fs), attempt %d/%d, sleep %.1fs",
                        type(e).__name__, timeout_sec, attempt + 1, _MAX_429_RETRIES, wait)
            await asyncio.sleep(wait)
            continue
    raise AssertionError("unreachable")


class AnthropicCompatClient:
    """Aliyun DashScope compatible-mode 适配 Claude 系列.
    路径是 /chat/completions, 但响应是 Anthropic 原生 schema (content blocks, stop_reason).
    默认指向 dashscope compatible-mode; ANTHROPIC_BASE_URL 可覆盖.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout_sec: float = 120.0,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ["ANTHROPIC_API_KEY"]
        self.base_url = (
            base_url
            or os.environ.get("ANTHROPIC_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_sec = timeout_sec

    async def ainvoke(self, messages: list[dict[str, str]]) -> LLMResponse:
        system_parts: list[str] = []
        chat: list[dict[str, str]] = []
        for m in messages:
            if m["role"] == "system":
                system_parts.append(m["content"])
            else:
                chat.append({"role": m["role"], "content": m["content"]})

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": chat,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        async with aiohttp.ClientSession() as sess:
            data = await _post_chat_completion(
                sess, url, body=body, headers=headers, timeout_sec=self.timeout_sec,
            )

        content = "".join(
            b.get("text", "")
            for b in data.get("content", [])
            if b.get("type") == "text"
        )
        return LLMResponse(content=content, raw=data)


class OpenAICompatClient:
    """OpenAI-compatible chat completions (gpt-5.x, deepseek, qwen via dashscope, etc.).
    响应走标准 OpenAI schema: choices[0].message.content.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout_sec: float = 120.0,
    ) -> None:
        self.model = model
        self.api_key = (
            api_key
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")  # 同 key 多端点的常见配置
        )
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY (or ANTHROPIC_API_KEY) not set")
        self.base_url = (
            base_url
            or os.environ.get("OPENAI_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_sec = timeout_sec

    async def ainvoke(self, messages: list[dict[str, str]]) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"

        async with aiohttp.ClientSession() as sess:
            data = await _post_chat_completion(
                sess, url, body=body, headers=headers, timeout_sec=self.timeout_sec,
            )

        content = data["choices"][0]["message"].get("content") or ""
        return LLMResponse(content=content, raw=data)
