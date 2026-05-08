"""Provider abstractions and factory.

Env vars are loaded from project-root .env on import. Existing shell env vars
take precedence (override=False), so CI / shell can still overwrite.

Selection via env vars:
    LLM_PROVIDER     (anthropic_compat | openai_compat),  default anthropic_compat
    PLANNER_MODEL    LLM model name,                      default aws.claude-opus-4-6
    IMAGE_PROVIDER   (dashscope_t2i | openai_image),      default dashscope_t2i
    T2I_MODEL        image model name,                    default wanx2.1-t2i-turbo
    VIDEO_PROVIDER   (dashscope_i2v | seeddance),         default dashscope_i2v
    I2V_MODEL        video model name,                    default wanx2.1-i2v-turbo
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根 .env. 必须在 build_*_provider() 被调用前执行 — 工厂会读 env.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from .base import (
    ImageProvider,
    ImageResult,
    LLMProvider,
    LLMResponse,
    VideoProvider,
    VideoResult,
)
from .image import DashScopeT2I, OpenAIImageClient
from .llm import AnthropicCompatClient, OpenAICompatClient
from .video import DashScopeI2V, SeedDanceClient


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


def build_video_provider() -> VideoProvider:
    name = os.environ.get("VIDEO_PROVIDER", "dashscope_i2v")
    model = os.environ.get("I2V_MODEL", "wanx2.1-i2v-turbo")
    if name == "dashscope_i2v":
        return DashScopeI2V(model=model)
    if name == "seeddance":
        return SeedDanceClient(model=model)
    raise ValueError(f"unknown VIDEO_PROVIDER: {name}")


__all__ = [
    "LLMProvider", "LLMResponse",
    "ImageProvider", "ImageResult",
    "VideoProvider", "VideoResult",
    "AnthropicCompatClient", "OpenAICompatClient",
    "DashScopeT2I", "OpenAIImageClient",
    "DashScopeI2V", "SeedDanceClient",
    "build_llm_provider", "build_image_provider", "build_video_provider",
]
