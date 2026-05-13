"""Profile registry: name → Profile() 解析.

加新 profile = 写一个 my_profile.py + 在这里注册.
"""
from __future__ import annotations

from .anime import ANIME
from .base import Profile
from .cinema import CINEMA
from .commercial import COMMERCIAL
from .short_drama import SHORT_DRAMA


_REGISTRY: dict[str, Profile] = {
    p.profile_id: p
    for p in [SHORT_DRAMA, ANIME, CINEMA, COMMERCIAL]
}


def get_profile(profile_id: str) -> Profile:
    """根据 profile_id 取 Profile. 未知 id 抛 KeyError + 提示可用列表."""
    if profile_id not in _REGISTRY:
        avail = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"unknown profile_id={profile_id!r}; available: {avail}")
    return _REGISTRY[profile_id]


def list_profiles() -> list[Profile]:
    return list(_REGISTRY.values())
