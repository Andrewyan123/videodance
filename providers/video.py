"""Video generation providers (image-to-video)."""
from __future__ import annotations

from . import _dashscope
from .base import VideoResult


class DashScopeI2V:
    """Aliyun DashScope wanx i2v.
    wanx2.1-i2v-turbo 当前 duration ∈ [3, 5]; -plus 支持到 10s. duration 自动 clamp.
    turbo 不返回末帧, last_frame_url=None.
    """

    def __init__(
        self,
        model: str = "wanx2.1-i2v-turbo",
        *,
        duration_min: int = 3,
        duration_max: int = 5,
        cost_per_sec: float = 0.5,
        timeout_sec: float = 900.0,
    ) -> None:
        self.model = model
        self.duration_min = duration_min
        self.duration_max = duration_max
        self.cost_per_sec = cost_per_sec
        self.timeout_sec = timeout_sec

    async def generate(
        self,
        *,
        prompt: str,
        first_frame_url: str,
        duration_sec: float,
    ) -> VideoResult:
        duration_int = max(
            self.duration_min,
            min(self.duration_max, int(round(duration_sec))),
        )
        out = await _dashscope.submit_and_poll(
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
            body={
                "model": self.model,
                "input": {"prompt": prompt[:800], "img_url": first_frame_url},
                "parameters": {"duration": duration_int},
            },
            poll_interval=5.0,
            timeout_sec=self.timeout_sec,
        )
        return VideoResult(
            video_url=out["video_url"],
            last_frame_url=None,
            cost_usd=self.cost_per_sec * duration_int,
        )


class SeedDanceClient:
    """Stub for ByteDance SeedDance / Doubao 视频模型.
    TODO: 接入时填实际端点和请求格式.
    """

    def __init__(self, model: str = "seeddance-pro", *, cost_per_sec: float = 0.6) -> None:
        self.model = model
        self.cost_per_sec = cost_per_sec

    async def generate(
        self,
        *,
        prompt: str,
        first_frame_url: str,
        duration_sec: float,
    ) -> VideoResult:
        raise NotImplementedError("SeedDanceClient: wire SeedDance API here")
