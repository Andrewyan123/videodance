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
class ImageEditResult:
    """图像编辑 / i2i 输出. 跟 ImageResult 同形, 但语义上是"基于 anchor 派生".
    分开是为了类型语义清晰, 也便于将来加 'preserved_face_score' 等编辑特有字段."""
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


class ImageEditProvider(Protocol):
    """图像编辑 / i2i. 以 anchor 图为基, instruction 描述目标编辑.

    设计上把这个跟 ImageProvider 分开:
      - ImageProvider.generate(prompt) → 纯 t2i, 抽卡式
      - ImageEditProvider.edit(anchor_path, instruction) → i2i, 保留 anchor 主体

    anchor_path 是**本地文件路径** (provider 内部负责 base64 编码 / 上传).
    上层不需要关心传输格式.
    """
    async def edit(
        self,
        anchor_path: str,
        instruction: str,
        *,
        size: str | None = None,
    ) -> ImageEditResult: ...


class VideoProvider(Protocol):
    async def generate(
        self,
        *,
        prompt: str,
        first_frame_url: str,
        duration_sec: float,
    ) -> VideoResult: ...
