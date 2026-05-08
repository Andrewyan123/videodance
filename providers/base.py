"""Provider protocols and result types. Vendor-agnostic.

每类 provider (LLM / Image / Video) 定义统一的调用契约, 具体实现可换 vendor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class LLMResponse:
    content: str
    raw: dict[str, Any] | None = None


@dataclass
class ImageResult:
    url: str
    cost_usd: float = 0.0


@dataclass
class VideoResult:
    video_url: str
    last_frame_url: str | None = None
    cost_usd: float = 0.0


class LLMProvider(Protocol):
    async def ainvoke(self, messages: list[dict[str, str]]) -> LLMResponse: ...


class ImageProvider(Protocol):
    async def generate(
        self,
        prompt: str,
        *,
        ref_images: list[str] | None = None,
    ) -> ImageResult: ...


class VideoProvider(Protocol):
    async def generate(
        self,
        *,
        prompt: str,
        first_frame_url: str,
        duration_sec: float,
    ) -> VideoResult: ...
