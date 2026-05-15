"""Video generation providers (image-to-video).

backend 对照:
  DashScopeI2V (wanx2.1-i2v-turbo): 单帧 i2v, /video-generation/ 端点, img_url 字段
  WanxI2VPlusClient (wanx2.1-kf2v-plus): 首尾帧 i2v, /image2video/ 端点,
    first_frame_url + last_frame_url 字段, duration 固定 5s
  SeedDanceClient (doubao-seedance-2-0-260128): volcengine ark, content 数组
  JiMengClient: 同 SeedDance 共享 ARK_API_KEY, 别名层
"""
from __future__ import annotations

import logging

from . import _dashscope
from .base import VideoResult

log = logging.getLogger("video_pipeline.video")


class DashScopeI2V:
    """Aliyun DashScope wanx i2v (单帧).
    wanx2.1-i2v-turbo 当前 duration ∈ [3, 5]; turbo 不接 last_frame.
    传 last_frame_url 给 turbo 时静默丢 + warn (跨 provider 接口兼容).
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
        last_frame_url: str | None = None,  # 兼容 Protocol, turbo 不消化
        seed: int | None = None,             # Phase 4.1: retry policy 用
    ) -> VideoResult:
        if last_frame_url:
            log.warning("DashScopeI2V(%s): last_frame_url provided but turbo doesn't "
                        "support it, dropping. Use WanxI2VPlusClient for first+last.",
                        self.model)
        duration_int = max(
            self.duration_min,
            min(self.duration_max, int(round(duration_sec))),
        )
        params: dict = {"duration": duration_int}
        if seed is not None:
            params["seed"] = seed
        out = await _dashscope.submit_and_poll(
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
            body={
                "model": self.model,
                "input": {"prompt": prompt[:800], "img_url": first_frame_url},
                "parameters": params,
            },
            poll_interval=5.0,
            timeout_sec=self.timeout_sec,
        )
        return VideoResult(
            video_url=out["video_url"],
            last_frame_url=None,
            cost_usd=self.cost_per_sec * duration_int,
        )


class WanxI2VPlusClient:
    """Aliyun DashScope wanx2.1-kf2v-plus / wan2.2-kf2v-flash —— 首尾帧 i2v.

    跟 turbo 的差异:
      - 端点: /image2video/video-synthesis (turbo 是 /video-generation/...)
      - 字段: input.first_frame_url + input.last_frame_url (turbo 用 input.img_url)
      - 时长: 固定 5s (parameters 不接 duration)
      - 速度: 约 4.5 min/段 (turbo 约 2 min)
      - 用途: 首尾帧锚定, identity 保持比 turbo 强

    last_frame_url 缺失时, 自动降级用 first_frame_url 作为 last (= 静止帧, 等同 turbo);
    更明智的做法是显式选 DashScopeI2V (turbo) 跑单帧. router 层负责.
    """

    ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/image2video/video-synthesis"

    def __init__(
        self,
        model: str = "wanx2.1-kf2v-plus",
        *,
        resolution: str = "720P",
        cost_per_call: float = 3.0,  # 粗略估 (实测后调)
        timeout_sec: float = 900.0,
    ) -> None:
        self.model = model
        self.resolution = resolution
        self.cost_per_call = cost_per_call
        self.timeout_sec = timeout_sec

    async def generate(
        self,
        *,
        prompt: str,
        first_frame_url: str,
        duration_sec: float,  # 模型固定 5s, 接收但忽略 (兼容 Protocol)
        last_frame_url: str | None = None,
        seed: int | None = None,  # Phase 4.1: retry policy 用
    ) -> VideoResult:
        # last_frame 缺失时, 用 first 当 last (退化成"静止画面"是个不好的视频, 提醒)
        if not last_frame_url:
            log.warning("WanxI2VPlusClient: no last_frame_url, using first as last "
                        "(will produce near-static video). Use DashScopeI2V for "
                        "single-frame extrapolation instead.")
            last_frame_url = first_frame_url

        params: dict = {"resolution": self.resolution}
        if seed is not None:
            params["seed"] = seed
        out = await _dashscope.submit_and_poll(
            self.ENDPOINT,
            body={
                "model": self.model,
                "input": {
                    "first_frame_url": first_frame_url,
                    "last_frame_url": last_frame_url,
                    "prompt": prompt[:800],
                },
                "parameters": params,
            },
            poll_interval=10.0,  # plus 慢, 10s 间隔够
            timeout_sec=self.timeout_sec,
        )
        return VideoResult(
            video_url=out["video_url"],
            last_frame_url=None,  # plus 输出 mp4, 不直接返回末帧图
            cost_usd=self.cost_per_call,
        )


class _VolcengineArkBase:
    """共享: volcengine ark contents/generations/tasks API.
    SeedDance + JiMeng 都走这个端点, 仅 model 不同.

    认证: Bearer ARK_API_KEY (不是 HMAC, 跟 OpenAI 同范式).
    Body:
      {
        "model": "<model_id>",
        "content": [
          {"type": "text", "text": "..."},
          {"type": "image_url", "image_url": {"url": "<first_url>"}},   # optional
          {"type": "image_url", "image_url": {"url": "<last_url>"}}     # 同时给 → 首尾帧模式
        ],
        "ratio": "adaptive",
        "duration": 5,         // 4-15
        "resolution": "1080p", // 480p/720p/1080p/2K
        "watermark": false
      }
    """

    ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        duration_default: int = 5,
        resolution: str = "1080p",
        cost_per_sec: float = 0.6,
        timeout_sec: float = 900.0,
    ) -> None:
        import os
        self.model = model
        self.api_key = api_key or os.environ.get("ARK_API_KEY") or os.environ.get("VOLC_ARK_API_KEY")
        self.duration_default = duration_default
        self.resolution = resolution
        self.cost_per_sec = cost_per_sec
        self.timeout_sec = timeout_sec

    def _build_body(self, prompt: str, first: str, last: str | None, duration: int) -> dict:
        content: list[dict] = [{"type": "text", "text": prompt[:800]}]
        content.append({"type": "image_url", "image_url": {"url": first}})
        if last:
            content.append({"type": "image_url", "image_url": {"url": last}})
        return {
            "model": self.model,
            "content": content,
            "ratio": "adaptive",
            "duration": duration,
            "resolution": self.resolution,
            "watermark": False,
        }

    async def _submit_and_poll(self, body: dict) -> dict:
        """volcengine ark 同样是 task-create + poll 模式, 但端点不同.
        TODO: 拿到 ARK_API_KEY 后真测, 当前是 stub 行为 (raise NotImplementedError).
        """
        if not self.api_key:
            raise RuntimeError(
                f"{type(self).__name__}: ARK_API_KEY not set in env "
                "(volcengine ark bearer key)"
            )
        # TODO: 实装 HTTP submit + poll (用 aiohttp, 同 _dashscope.submit_and_poll 范式)
        raise NotImplementedError(
            f"{type(self).__name__}: volcengine ark HTTP not wired yet. "
            f"endpoint={self.ENDPOINT}, model={self.model}. "
            f"AK present={bool(self.api_key)}. 拿到 AK 后填 submit + poll."
        )


class SeedDanceClient(_VolcengineArkBase):
    """SeedDance 2.0 (volcengine ark doubao-seedance).

    支持首尾帧 (content 数组 = [text, image1, image2]).
    """

    def __init__(
        self,
        model: str = "doubao-seedance-2-0-260128",
        **kwargs,
    ) -> None:
        super().__init__(model=model, **kwargs)

    async def generate(
        self,
        *,
        prompt: str,
        first_frame_url: str,
        duration_sec: float,
        last_frame_url: str | None = None,
        seed: int | None = None,  # ark API 是否接 seed 待确认 (拿到 AK 后调)
    ) -> VideoResult:
        duration = max(4, min(15, int(round(duration_sec))))
        body = self._build_body(prompt, first_frame_url, last_frame_url, duration)
        if seed is not None:
            body["seed"] = seed
        out = await self._submit_and_poll(body)
        return VideoResult(
            video_url=out["video_url"],
            last_frame_url=None,
            cost_usd=self.cost_per_sec * duration,
        )


class JiMengClient(_VolcengineArkBase):
    """即梦 / Dreamina (跟 SeedDance 同 ark API, 不同 model id).

    Volcengine 上的 model id 还在 rolling out; 当前最佳猜测是 doubao-seedance 的别名.
    实测时可用 model=os.environ['JIMENG_MODEL'] override.
    """

    def __init__(
        self,
        model: str = "doubao-seedance-2-0-fast-260128",  # fast 变体, JiMeng 偏 C 端速度
        **kwargs,
    ) -> None:
        super().__init__(model=model, **kwargs)

    async def generate(
        self,
        *,
        prompt: str,
        first_frame_url: str,
        duration_sec: float,
        last_frame_url: str | None = None,
        seed: int | None = None,  # ark API 是否接 seed 待确认 (拿到 AK 后调)
    ) -> VideoResult:
        duration = max(4, min(15, int(round(duration_sec))))
        body = self._build_body(prompt, first_frame_url, last_frame_url, duration)
        if seed is not None:
            body["seed"] = seed
        out = await self._submit_and_poll(body)
        return VideoResult(
            video_url=out["video_url"],
            last_frame_url=None,
            cost_usd=self.cost_per_sec * duration,
        )
