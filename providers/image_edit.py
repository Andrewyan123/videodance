"""Image edit / i2i providers.

实装:
  - WanxI2IClient: DashScope wanx2.1-imageedit, function=description_edit
    实测: ArcFace cos sim 跟 anchor 0.81, 远超 t2i 的 0.12

Stubs (留扩展口):
  - QwenImageEditClient: qwen-image-edit-plus (同 DashScope 但走 multimodal-generation
    端点, 当前 key 不支持 async; 等改成 sync 调用再接)
  - GPTImageEditClient: OpenAI gpt-image-2 (走 eval 网关, 图像输入编辑)

设计:
  - edit(anchor_path, instruction) 是 ImageEditProvider Protocol 的唯一入口
  - anchor_path 是本地文件; provider 内部负责 base64 编码 (DashScope 接受
    data:image/png;base64,... 这种 data URI 格式) 或上传 (后续 vendor 可能需要)
  - 上层完全不知道传输方式
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

from . import _dashscope
from .base import ImageEditResult


_DASHSCOPE_IMG_EDIT_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/image2image/image-synthesis"
)


def _to_data_uri(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"anchor not found: {path}")
    # 简单按后缀推 mime; 我们目前只产 png
    ext = p.suffix.lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/png")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


class WanxI2IClient:
    """DashScope wanx2.1-imageedit (function=description_edit).

    实测 (Phase 1.3 probe): anchor 是 wanx-t2i 生成的人物正面, 用 description_edit
    + 不同 scene instruction 派生, ArcFace cos sim ≈ 0.81. 远超 t2i 抽卡的 ≈ 0.12.

    限制:
      - 单张 anchor 输入 (function 决定形式)
      - 输出尺寸由模型决定, 我们不传 size 参数 (传了会报错)
      - 多 function: description_edit / control_cartoon_feature / stylization_all 等;
        当前只暴露 description_edit, 其他 function 未来按需开
    """

    def __init__(
        self,
        model: str = "wanx2.1-imageedit",
        *,
        function: str = "description_edit",
        cost_per_image: float = 0.05,  # 比 t2i 略贵
        timeout_sec: float = 180.0,
    ) -> None:
        self.model = model
        self.function = function
        self.cost_per_image = cost_per_image
        self.timeout_sec = timeout_sec

    async def edit(
        self,
        anchor_path: str,
        instruction: str,
        *,
        size: str | None = None,  # noqa: ARG002 — wanx-imageedit 不接受 size, 保留接口形状
    ) -> ImageEditResult:
        data_uri = _to_data_uri(anchor_path)

        out = await _dashscope.submit_and_poll(
            _DASHSCOPE_IMG_EDIT_URL,
            body={
                "model": self.model,
                "input": {
                    "function": self.function,
                    "prompt": instruction[:800],
                    "base_image_url": data_uri,
                },
                "parameters": {"n": 1},
            },
            poll_interval=2.0,
            timeout_sec=self.timeout_sec,
        )
        return ImageEditResult(
            url=out["results"][0]["url"],
            cost_usd=self.cost_per_image,
        )


# --- Stubs ---


class QwenImageEditClient:
    """Stub: qwen-image-edit / qwen-image-edit-plus.

    端点是 multimodal-generation/generation, 但当前 key 不支持异步调用
    ('current user api does not support asynchronous calls'). 接入时需要:
      1. 改用同步 (HTTP POST without X-DashScope-Async: enable)
      2. 解析 sync 返回里的图像 URL / base64
      3. 处理 quota / rate limit
    """

    def __init__(self, model: str = "qwen-image-edit-plus") -> None:
        self.model = model

    async def edit(
        self,
        anchor_path: str,
        instruction: str,
        *,
        size: str | None = None,
    ) -> ImageEditResult:
        raise NotImplementedError(
            "QwenImageEditClient: switch to sync API, wire response parser"
        )


class GPTImageEditClient:
    """Stub: OpenAI gpt-image-2 走 eval 网关 (compatible-mode).

    接入时:
      1. 端点是 https://dashscope.aliyuncs.com/compatible-mode/v1/images/edits
      2. multipart/form-data 含 image=@file + prompt + model
      3. 解析 b64_json 或 url
    """

    def __init__(self, model: str = "gpt-image-2") -> None:
        self.model = model

    async def edit(
        self,
        anchor_path: str,
        instruction: str,
        *,
        size: str | None = None,
    ) -> ImageEditResult:
        raise NotImplementedError(
            "GPTImageEditClient: wire OpenAI Images edit endpoint (multipart)"
        )
