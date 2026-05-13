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
    """Stub: 字节 SeedDance / Doubao 视频模型 (走 volcengine 平台).

    认证: VOLC_ACCESSKEY + VOLC_SECRETKEY (字节通用 OpenAPI 签名).
    SeedDance 支持首尾帧, 5-10s 时长.

    TODO 接入时:
      1. 替换 _endpoint / _sign 为 volcengine 真实签名逻辑 (HMAC-SHA256)
      2. 解析返回 task_id, 轮询任务状态
      3. 解析 video_url / last_frame_url (SeedDance 应该返回末帧)
    """

    def __init__(
        self,
        model: str = "seeddance-2-pro",
        *,
        access_key: str | None = None,
        secret_key: str | None = None,
        duration_min: int = 5,
        duration_max: int = 10,
        cost_per_sec: float = 0.6,
        timeout_sec: float = 900.0,
    ) -> None:
        import os
        self.model = model
        self.access_key = access_key or os.environ.get("VOLC_ACCESSKEY")
        self.secret_key = secret_key or os.environ.get("VOLC_SECRETKEY")
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
        if not self.access_key or not self.secret_key:
            raise RuntimeError(
                "SeedDanceClient: VOLC_ACCESSKEY / VOLC_SECRETKEY not set in env"
            )
        raise NotImplementedError(
            "SeedDanceClient: wire volcengine HMAC signing + task polling here. "
            f"intended model={self.model}, duration={duration_sec}s, "
            f"first_frame={first_frame_url[:60]}..."
        )


class JiMengClient:
    """Stub: 字节即梦 (Dreamina) 视频模型 (走 volcengine 平台).

    认证: 同 SeedDance, 共享 VOLC_ACCESSKEY/SECRETKEY.
    即梦在风格化和 C 端友好上更有优势, 适合广告 / 动画 profile.

    TODO 接入时:
      1. 端点是 visual.volcengineapi.com (CV 类) 或 cv.volcengineapi.com
      2. action 应该是类似 CVProcess / Img2Vid 这种
      3. 同步 vs 异步轮询模式待确认
    """

    def __init__(
        self,
        model: str = "jimeng-3",
        *,
        access_key: str | None = None,
        secret_key: str | None = None,
        duration_min: int = 3,
        duration_max: int = 10,
        cost_per_sec: float = 0.5,
        timeout_sec: float = 900.0,
    ) -> None:
        import os
        self.model = model
        self.access_key = access_key or os.environ.get("VOLC_ACCESSKEY")
        self.secret_key = secret_key or os.environ.get("VOLC_SECRETKEY")
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
        if not self.access_key or not self.secret_key:
            raise RuntimeError(
                "JiMengClient: VOLC_ACCESSKEY / VOLC_SECRETKEY not set in env"
            )
        raise NotImplementedError(
            "JiMengClient: wire volcengine visual API here. "
            f"intended model={self.model}, duration={duration_sec}s, "
            f"first_frame={first_frame_url[:60]}..."
        )
