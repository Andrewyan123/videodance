"""Image generation providers."""
from __future__ import annotations

from . import _dashscope
from .base import ImageResult


class DashScopeT2I:
    """Aliyun DashScope wanx t2i / qwen-image. Async submit-and-poll.
    wanx2.1-t2i-turbo 是纯 t2i, ref_images 参数会被忽略.
    要做参考图条件生成请用 i2i 模型 (例如 wanx2.1-i2i / qwen-image-edit) 实现独立 provider.
    """

    def __init__(
        self,
        model: str = "wanx2.1-t2i-turbo",
        *,
        size: str = "1280*720",
        n: int = 1,
        cost_per_image: float = 0.02,
        timeout_sec: float = 180.0,
    ) -> None:
        self.model = model
        self.size = size
        self.n = n
        self.cost_per_image = cost_per_image
        self.timeout_sec = timeout_sec

    async def generate(
        self,
        prompt: str,
        *,
        ref_images: list[str] | None = None,
    ) -> ImageResult:
        out = await _dashscope.submit_and_poll(
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
            body={
                "model": self.model,
                "input": {"prompt": prompt[:800]},
                "parameters": {"size": self.size, "n": self.n},
            },
            poll_interval=2.0,
            timeout_sec=self.timeout_sec,
        )
        return ImageResult(url=out["results"][0]["url"], cost_usd=self.cost_per_image)


class OpenAIImageClient:
    """Stub for OpenAI gpt-image-2 / gpt-image-1.
    TODO: 接入时按 OpenAI Images API 实现 (POST /v1/images/generations, 返回 b64 或 url).
    """

    def __init__(
        self,
        model: str = "gpt-image-2",
        *,
        size: str = "1024x1024",
        cost_per_image: float = 0.04,
    ) -> None:
        self.model = model
        self.size = size
        self.cost_per_image = cost_per_image

    async def generate(
        self,
        prompt: str,
        *,
        ref_images: list[str] | None = None,
    ) -> ImageResult:
        raise NotImplementedError("OpenAIImageClient: wire OpenAI Images API here")
