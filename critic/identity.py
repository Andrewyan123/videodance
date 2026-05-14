"""跨 shot 角色身份一致性评分.

流程: video → ffmpeg 抽中间帧 → InsightFace embedding → 跟 card.embedding 余弦.

设计:
- 用 mid-frame 而不是 first/last frame
  (动作两端态可能脸侧/转身; 中间帧最稳).
- score 是 cos sim, 不是 binary pass/fail —— 把阈值交给上层 critic/policy.
- 没检测到人脸 → score=None + 标记 reason='no_face_in_frame'
  (上游应判断是否要重试).
"""
from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

from assets import faceid


def _probe_duration_sec(video_path: Path) -> Optional[float]:
    """用 ffprobe 拿视频时长, 失败返 None."""
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            text=True, timeout=10,
        )
        return float(out.strip())
    except (subprocess.SubprocessError, ValueError):
        return None


def _extract_frame(video_path: Path, t_sec: float, out_path: Path) -> bool:
    """ffmpeg 抽指定时间戳的单帧到 out_path. 返回是否成功."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t_sec), "-i", str(video_path),
             "-frames:v", "1", "-q:v", "2", str(out_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=True,
        )
        return out_path.exists() and out_path.stat().st_size > 0
    except (subprocess.SubprocessError, subprocess.CalledProcessError):
        return False


def score_shot_identity(
    video_path: str | Path,
    card_embedding: list[float] | np.ndarray,
    *,
    frame_time_sec: Optional[float] = None,
) -> dict:
    """对一段视频与某 character 的 embedding 算 identity 一致性.

    Args:
        video_path: 本地 mp4 (注: 远端 URL 需调用方先下载)
        card_embedding: CharacterCard.embedding (512-d, arcface_buffalo_l)
        frame_time_sec: 抽帧时间, None 表示中间帧

    Returns:
        {
          "score": float | None,        # cosine in [-1, 1], None=no_face
          "frame_time_sec": float,
          "face_detected": bool,
          "reason": str | None,         # 失败原因
        }
    """
    video_path = Path(video_path)
    if not video_path.exists():
        return {"score": None, "frame_time_sec": None,
                "face_detected": False, "reason": "video_not_found"}

    duration = _probe_duration_sec(video_path)
    if frame_time_sec is None:
        frame_time_sec = (duration / 2.0) if duration else 1.0

    with tempfile.TemporaryDirectory() as td:
        frame = Path(td) / "frame.png"
        ok = _extract_frame(video_path, frame_time_sec, frame)
        if not ok:
            return {"score": None, "frame_time_sec": frame_time_sec,
                    "face_detected": False, "reason": "ffmpeg_extract_failed"}

        emb = faceid.extract_embedding(frame)
        if emb is None:
            return {"score": None, "frame_time_sec": frame_time_sec,
                    "face_detected": False, "reason": "no_face_in_frame"}

    score = faceid.cosine_similarity(np.asarray(card_embedding), emb)
    return {"score": score, "frame_time_sec": frame_time_sec,
            "face_detected": True, "reason": None}


def score_shots_batch(
    shot_videos: list[str | Path],
    card_embedding: list[float] | np.ndarray,
) -> dict:
    """对多 shot 视频统一打分, 返回 summary.

    Returns:
      {
        "per_shot": [{"video": str, ...score_shot_identity result}, ...],
        "scores":   [float, ...],          # 仅 detected 的, 跟 per_shot 对不齐
        "mean":     float | None,
        "min":      float | None,
        "max":      float | None,
        "n_detected": int,
        "n_total":    int,
      }
    """
    per_shot = []
    scores = []
    for v in shot_videos:
        r = score_shot_identity(v, card_embedding)
        r2 = {"video": str(v), **r}
        per_shot.append(r2)
        if r["score"] is not None:
            scores.append(r["score"])

    return {
        "per_shot": per_shot,
        "scores": scores,
        "mean": (sum(scores) / len(scores)) if scores else None,
        "min": min(scores) if scores else None,
        "max": max(scores) if scores else None,
        "n_detected": len(scores),
        "n_total": len(shot_videos),
    }
