"""Provider abstractions and factory.

Env vars are loaded from project-root .env on import with override=True —
.env 显式写的值优先于 shell 继承的全局变量 (例如阿里内网默认的 OPENAI_BASE_URL).
临时覆盖请在命令行 inline 设, e.g. `LLM_PROVIDER=anthropic_compat uv run ...`,
inline 设的会优先于 .env (因为 dotenv 不会覆盖已设的 process env, 只覆盖 import 前已存在的).
注意: load_dotenv(override=True) 实际上会顶掉 shell export 的同名值; inline 命令式赋值
是 process spawn 时设置, .env 同名时也会被 override 顶掉. 真要 inline 优先得在代码里
特殊处理, 这个项目用不到, 保持简单.

Selection via env vars:
    LLM_PROVIDER       (anthropic_compat | openai_compat),         default anthropic_compat
    PLANNER_MODEL      LLM model name,                              default aws.claude-opus-4-6
    IMAGE_PROVIDER     (dashscope_t2i | openai_image),              default dashscope_t2i
    T2I_MODEL          image model name,                            default wanx2.1-t2i-turbo
    IMAGE_EDIT_PROVIDER (wanx_i2i | qwen_image_edit | gpt_image_edit), default wanx_i2i
    IMAGE_EDIT_MODEL   image edit model name,                       default wanx2.1-imageedit
    VIDEO_PROVIDER     (dashscope_i2v | seeddance | jimeng),        default dashscope_i2v
    I2V_MODEL          video model name,                            default wanx2.1-i2v-turbo
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根 .env. 必须在 build_*_provider() 被调用前执行 — 工厂会读 env.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

from .base import (
    ImageEditProvider,
    ImageEditResult,
    ImageProvider,
    ImageResult,
    LLMProvider,
    LLMResponse,
    VideoProvider,
    VideoResult,
)
from .image import DashScopeT2I, OpenAIImageClient
from .image_edit import GPTImageEditClient, QwenImageEditClient, WanxI2IClient
from .llm import AnthropicCompatClient, OpenAICompatClient
from .video import DashScopeI2V, JiMengClient, SeedDanceClient


def build_llm_provider() -> LLMProvider:
    name = os.environ.get("LLM_PROVIDER", "anthropic_compat")
    model = os.environ.get("PLANNER_MODEL", "aws.claude-opus-4-6")
    if name == "anthropic_compat":
        return AnthropicCompatClient(model=model)
    if name == "openai_compat":
        return OpenAICompatClient(model=model)
    raise ValueError(f"unknown LLM_PROVIDER: {name}")


def build_image_provider() -> ImageProvider:
    name = os.environ.get("IMAGE_PROVIDER", "dashscope_t2i")
    model = os.environ.get("T2I_MODEL", "wanx2.1-t2i-turbo")
    if name == "dashscope_t2i":
        return DashScopeT2I(model=model)
    if name == "openai_image":
        return OpenAIImageClient(model=model)
    raise ValueError(f"unknown IMAGE_PROVIDER: {name}")


def build_image_edit_provider() -> ImageEditProvider:
    name = os.environ.get("IMAGE_EDIT_PROVIDER", "wanx_i2i")
    explicit_model = os.environ.get("IMAGE_EDIT_MODEL")
    if name == "wanx_i2i":
        return WanxI2IClient(model=explicit_model or "wanx2.1-imageedit")
    if name == "qwen_image_edit":
        return QwenImageEditClient(model=explicit_model or "qwen-image-edit-plus")
    if name == "gpt_image_edit":
        return GPTImageEditClient(model=explicit_model or "gpt-image-2")
    raise ValueError(f"unknown IMAGE_EDIT_PROVIDER: {name}")


def build_video_provider() -> VideoProvider:
    name = os.environ.get("VIDEO_PROVIDER", "dashscope_i2v")
    # I2V_MODEL 显式设了就用; 否则让 backend class 自带默认 (避免把 wanx 模型名传给 seeddance)
    explicit_model = os.environ.get("I2V_MODEL")
    if name == "dashscope_i2v":
        return DashScopeI2V(model=explicit_model or "wanx2.1-i2v-turbo")
    if name == "seeddance":
        return SeedDanceClient(model=explicit_model) if explicit_model else SeedDanceClient()
    if name == "jimeng":
        return JiMengClient(model=explicit_model) if explicit_model else JiMengClient()
    raise ValueError(f"unknown VIDEO_PROVIDER: {name}")


__all__ = [
    "LLMProvider", "LLMResponse",
    "ImageProvider", "ImageResult",
    "ImageEditProvider", "ImageEditResult",
    "VideoProvider", "VideoResult",
    "AnthropicCompatClient", "OpenAICompatClient",
    "DashScopeT2I", "OpenAIImageClient",
    "WanxI2IClient", "QwenImageEditClient", "GPTImageEditClient",
    "DashScopeI2V", "SeedDanceClient", "JiMengClient",
    "build_llm_provider", "build_image_provider", "build_image_edit_provider", "build_video_provider",
]
