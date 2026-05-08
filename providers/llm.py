"""LLM providers."""
from __future__ import annotations

import json
import os
from typing import Any

import aiohttp

from .base import LLMResponse


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
            async with sess.post(
                url, json=body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout_sec),
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"LLM HTTP {resp.status}: {text[:500]}")
                data = json.loads(text)

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
            async with sess.post(
                url, json=body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout_sec),
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"LLM HTTP {resp.status}: {text[:500]}")
                data = json.loads(text)

        content = data["choices"][0]["message"].get("content") or ""
        return LLMResponse(content=content, raw=data)
