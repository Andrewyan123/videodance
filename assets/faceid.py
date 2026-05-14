"""ArcFace embedding 提取 + cosine 相似度.

Phase 1.2 接入. 用 InsightFace 的 buffalo_l 模型包 (5 个 onnx, 模型下到
~/.insightface/models/buffalo_l/, 首次 ~3-4s 初始化).

API 设计:
- extract_embedding(path) -> Optional[np.ndarray[512]]
  - None 表示没检测到人脸 (建档时 builder 应触发重生成或人工干预)
  - 多脸取置信度最高的 (det_score)
- cosine_similarity(a, b) -> float in [-1, 1]
- pairwise_cosine(embs) -> list[(i, j, cos)] 全部两两

实现要点:
- FaceAnalysis 单例, lazy load, CPU only (macOS 没 CUDA 也跑得动)
- embedding 是 512-d float32, ArcFace 风格, 余弦比对推荐 >0.4 视为同人
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np


# --- 单例: 避免每次调用都重新加载模型 (init ~3s) ---

_face_app = None


def _get_app():
    global _face_app
    if _face_app is None:
        from insightface.app import FaceAnalysis
        _face_app = FaceAnalysis(
            name="buffalo_l",
            providers=["CPUExecutionProvider"],
        )
        _face_app.prepare(ctx_id=0, det_size=(640, 640))
    return _face_app


# --- 核心 API ---


def extract_embedding(image_path: str | Path) -> Optional[np.ndarray]:
    """提取最高置信度人脸的 512-d ArcFace embedding.

    返回 None 表示没检测到任何人脸.
    多脸时取 det_score 最高的 (通常是最大 / 最清晰的那张).
    """
    import cv2

    img = cv2.imread(str(image_path))
    if img is None:
        raise FileNotFoundError(f"cannot read image: {image_path}")

    app = _get_app()
    faces = app.get(img)
    if not faces:
        return None

    best = max(faces, key=lambda f: f.det_score)
    return best.embedding.astype(np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度, 结果 in [-1, 1]. 越接近 1 越像同人."""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def pairwise_cosine(embeddings: list[Optional[np.ndarray]]) -> list[tuple[int, int, float]]:
    """所有两两 cosine. 返回 [(i, j, cos)], i<j. None embedding 跳过那一对."""
    out: list[tuple[int, int, float]] = []
    n = len(embeddings)
    for i in range(n):
        if embeddings[i] is None:
            continue
        for j in range(i + 1, n):
            if embeddings[j] is None:
                continue
            out.append((i, j, cosine_similarity(embeddings[i], embeddings[j])))
    return out


def consistency_summary(
    embeddings: list[Optional[np.ndarray]],
) -> dict:
    """跨视图一致性总结: min/mean/max 两两 cosine.

    用于 builder 入库前的 gate 判断, 也用于 critic 评分.
    """
    pairs = pairwise_cosine(embeddings)
    detected = sum(1 for e in embeddings if e is not None)
    if not pairs:
        return {
            "n_total": len(embeddings),
            "n_detected": detected,
            "n_pairs": 0,
            "min": None, "mean": None, "max": None,
        }
    vals = [c for _, _, c in pairs]
    return {
        "n_total": len(embeddings),
        "n_detected": detected,
        "n_pairs": len(pairs),
        "min": min(vals),
        "mean": sum(vals) / len(vals),
        "max": max(vals),
    }
