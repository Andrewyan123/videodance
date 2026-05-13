"""流派 Profile 系统.

每个流派 (短剧 / 动画 / 电影 / 广告) 是一个 Profile dataclass,
集中决定 planner / generation / critic / post 的差异化配置.

使用:
    from profiles import get_profile
    p = get_profile("short_drama")
    print(p.video_backend_preferred)
"""
from .base import Profile
from .registry import get_profile, list_profiles
from .short_drama import SHORT_DRAMA
from .anime import ANIME
from .cinema import CINEMA
from .commercial import COMMERCIAL

__all__ = [
    "Profile",
    "get_profile", "list_profiles",
    "SHORT_DRAMA", "ANIME", "CINEMA", "COMMERCIAL",
]
