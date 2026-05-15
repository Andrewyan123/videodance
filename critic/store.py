"""Critic 评分持久化.

每个 shot 在每次 retry 都打一组分(目前只 identity 一维),全部入库。
后续 Phase 4.2 加 scene/narrative/VSA 三维度时,只多写几行 (dimension 列已为通用).
入库的数据正好可以作为 RL reward 的 trajectory.

存储:
  data/critic_scores.db  SQLite
  data/critic_scores/    (留作未来存 detail JSON / 抽帧 缩略图)
"""
from __future__ import annotations

import json
import os
import sqlite3
import datetime
from pathlib import Path
from typing import Optional


DEFAULT_DATA_DIR = Path(os.environ.get("VIDEO1_DATA_DIR", "./data")).resolve()


def _data_dir() -> Path:
    p = DEFAULT_DATA_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _db_path() -> Path:
    return _data_dir() / "critic_scores.db"


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS critic_scores (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id    TEXT NOT NULL,
    shot_id      TEXT NOT NULL,
    dimension    TEXT NOT NULL,
    score        REAL,
    threshold    REAL,
    passed       INTEGER NOT NULL,
    backend      TEXT,
    retry_count  INTEGER NOT NULL DEFAULT 0,
    video_url    TEXT,
    detail       TEXT,  -- JSON 详细信息 (e.g. frame_time_sec, reason)
    timestamp    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_critic_thread_shot
ON critic_scores(thread_id, shot_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    return conn


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def record_score(
    *,
    thread_id: str,
    shot_id: str,
    dimension: str,
    score: Optional[float],
    threshold: Optional[float] = None,
    passed: bool,
    backend: Optional[str] = None,
    retry_count: int = 0,
    video_url: str = "",
    detail: Optional[dict] = None,
) -> int:
    """落一条评分. 返回新行 id."""
    detail_json = json.dumps(detail, ensure_ascii=False) if detail is not None else None
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO critic_scores
              (thread_id, shot_id, dimension, score, threshold, passed,
               backend, retry_count, video_url, detail, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                thread_id, shot_id, dimension, score, threshold,
                1 if passed else 0,
                backend, retry_count, video_url, detail_json, _now_iso(),
            ),
        )
        return cur.lastrowid


def get_scores(
    *,
    thread_id: Optional[str] = None,
    shot_id: Optional[str] = None,
    dimension: Optional[str] = None,
) -> list[dict]:
    """筛 / 拉评分历史. 不传 filter 返回全部 (按 timestamp 排)."""
    conditions: list[str] = []
    params: list = []
    if thread_id is not None:
        conditions.append("thread_id = ?")
        params.append(thread_id)
    if shot_id is not None:
        conditions.append("shot_id = ?")
        params.append(shot_id)
    if dimension is not None:
        conditions.append("dimension = ?")
        params.append(dimension)

    sql = "SELECT * FROM critic_scores"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY id ASC"

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def reset_for_test() -> None:
    """清空 store. 测试用."""
    p = _db_path()
    if p.exists():
        p.unlink()
