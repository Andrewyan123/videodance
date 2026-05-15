"""SQLite store for Session / CharacterCandidate / ShotCandidate.

3 张表共用 data/candidates.db (跟 assets.db / critic_scores.db 平级).
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from .schema import CharacterCandidate, Session, ShotCandidate, _now_iso


DEFAULT_DATA_DIR = Path(os.environ.get("VIDEO1_DATA_DIR", "./data")).resolve()


def _data_dir() -> Path:
    p = DEFAULT_DATA_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _db_path() -> Path:
    return _data_dir() / "candidates.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_id           TEXT PRIMARY KEY,
    user_prompt         TEXT NOT NULL,
    target_duration_sec REAL NOT NULL,
    profile_id          TEXT NOT NULL,
    storyboard_json     TEXT NOT NULL,
    status              TEXT NOT NULL,
    final_video_url     TEXT,
    total_cost_usd      REAL NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS character_candidates (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id           TEXT NOT NULL,
    char_id             TEXT NOT NULL,
    variant_index       INTEGER NOT NULL,
    description_used    TEXT NOT NULL,
    seed                INTEGER,
    ref_image_url       TEXT NOT NULL,
    embedding_json      TEXT,
    consistency_score   REAL,
    selected            INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    UNIQUE(thread_id, char_id, variant_index)
);

CREATE INDEX IF NOT EXISTS idx_char_cand_thread
ON character_candidates(thread_id, char_id);

CREATE TABLE IF NOT EXISTS shot_candidates (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id           TEXT NOT NULL,
    shot_id             TEXT NOT NULL,
    variant_index       INTEGER NOT NULL,
    first_keyframe_url  TEXT,
    last_keyframe_url   TEXT,
    seed                INTEGER,
    backend_used        TEXT,
    video_url           TEXT NOT NULL,
    identity_score      REAL,
    selected            INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL NOT NULL DEFAULT 0,
    error               TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE(thread_id, shot_id, variant_index)
);

CREATE INDEX IF NOT EXISTS idx_shot_cand_thread
ON shot_candidates(thread_id, shot_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# === Sessions ===


def upsert_session(s: Session) -> None:
    s.updated_at = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (thread_id, user_prompt, target_duration_sec, profile_id,
                                  storyboard_json, status, final_video_url, total_cost_usd,
                                  created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                status=excluded.status,
                final_video_url=excluded.final_video_url,
                total_cost_usd=excluded.total_cost_usd,
                updated_at=excluded.updated_at
            """,
            (s.thread_id, s.user_prompt, s.target_duration_sec, s.profile_id,
             s.storyboard_json, s.status, s.final_video_url, s.total_cost_usd,
             s.created_at, s.updated_at),
        )


def get_session(thread_id: str) -> Optional[Session]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE thread_id = ?", (thread_id,),
        ).fetchone()
    if not row:
        return None
    return Session(**{k: row[k] for k in row.keys()})


# === Character candidates ===


def _row_to_char_cand(row: sqlite3.Row) -> CharacterCandidate:
    emb = json.loads(row["embedding_json"]) if row["embedding_json"] else None
    return CharacterCandidate(
        id=row["id"],
        thread_id=row["thread_id"],
        char_id=row["char_id"],
        variant_index=row["variant_index"],
        description_used=row["description_used"],
        seed=row["seed"],
        ref_image_url=row["ref_image_url"],
        embedding=emb,
        consistency_score=row["consistency_score"],
        selected=bool(row["selected"]),
        cost_usd=row["cost_usd"],
        created_at=row["created_at"],
    )


def insert_character_candidate(c: CharacterCandidate) -> int:
    emb_json = json.dumps(c.embedding) if c.embedding else None
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO character_candidates
              (thread_id, char_id, variant_index, description_used, seed,
               ref_image_url, embedding_json, consistency_score, selected, cost_usd, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (c.thread_id, c.char_id, c.variant_index, c.description_used, c.seed,
             c.ref_image_url, emb_json, c.consistency_score,
             1 if c.selected else 0, c.cost_usd, c.created_at),
        )
        return cur.lastrowid


def list_character_candidates(thread_id: str, char_id: Optional[str] = None) -> list[CharacterCandidate]:
    sql = "SELECT * FROM character_candidates WHERE thread_id = ?"
    params: list = [thread_id]
    if char_id is not None:
        sql += " AND char_id = ?"
        params.append(char_id)
    sql += " ORDER BY char_id, variant_index"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_char_cand(r) for r in rows]


def next_character_variant_index(thread_id: str, char_id: str) -> int:
    """propose 时算下一个 variant_index. 0 = 第一次 propose."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(variant_index) AS m FROM character_candidates "
            "WHERE thread_id = ? AND char_id = ?",
            (thread_id, char_id),
        ).fetchone()
    m = row["m"]
    return 0 if m is None else m + 1


def select_character_candidate(thread_id: str, char_id: str, variant_index: int) -> bool:
    """把 (thread_id, char_id, variant_index) 标 selected, 其他 variant 同 char 清掉."""
    with _connect() as conn:
        # 先清同 char 所有
        conn.execute(
            "UPDATE character_candidates SET selected = 0 "
            "WHERE thread_id = ? AND char_id = ?",
            (thread_id, char_id),
        )
        # 设 selected
        cur = conn.execute(
            "UPDATE character_candidates SET selected = 1 "
            "WHERE thread_id = ? AND char_id = ? AND variant_index = ?",
            (thread_id, char_id, variant_index),
        )
        return cur.rowcount > 0


# === Shot candidates ===


def _row_to_shot_cand(row: sqlite3.Row) -> ShotCandidate:
    return ShotCandidate(
        id=row["id"],
        thread_id=row["thread_id"],
        shot_id=row["shot_id"],
        variant_index=row["variant_index"],
        first_keyframe_url=row["first_keyframe_url"],
        last_keyframe_url=row["last_keyframe_url"],
        seed=row["seed"],
        backend_used=row["backend_used"],
        video_url=row["video_url"],
        identity_score=row["identity_score"],
        selected=bool(row["selected"]),
        cost_usd=row["cost_usd"],
        error=row["error"],
        created_at=row["created_at"],
    )


def insert_shot_candidate(c: ShotCandidate) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO shot_candidates
              (thread_id, shot_id, variant_index, first_keyframe_url, last_keyframe_url,
               seed, backend_used, video_url, identity_score, selected, cost_usd, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (c.thread_id, c.shot_id, c.variant_index, c.first_keyframe_url, c.last_keyframe_url,
             c.seed, c.backend_used, c.video_url, c.identity_score,
             1 if c.selected else 0, c.cost_usd, c.error, c.created_at),
        )
        return cur.lastrowid


def list_shot_candidates(thread_id: str, shot_id: Optional[str] = None) -> list[ShotCandidate]:
    sql = "SELECT * FROM shot_candidates WHERE thread_id = ?"
    params: list = [thread_id]
    if shot_id is not None:
        sql += " AND shot_id = ?"
        params.append(shot_id)
    sql += " ORDER BY shot_id, variant_index"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_shot_cand(r) for r in rows]


def next_shot_variant_index(thread_id: str, shot_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(variant_index) AS m FROM shot_candidates "
            "WHERE thread_id = ? AND shot_id = ?",
            (thread_id, shot_id),
        ).fetchone()
    m = row["m"]
    return 0 if m is None else m + 1


def select_shot_candidate(thread_id: str, shot_id: str, variant_index: int) -> bool:
    with _connect() as conn:
        conn.execute(
            "UPDATE shot_candidates SET selected = 0 "
            "WHERE thread_id = ? AND shot_id = ?",
            (thread_id, shot_id),
        )
        cur = conn.execute(
            "UPDATE shot_candidates SET selected = 1 "
            "WHERE thread_id = ? AND shot_id = ? AND variant_index = ?",
            (thread_id, shot_id, variant_index),
        )
        return cur.rowcount > 0


# === 工具 ===


def reset_for_test() -> None:
    p = _db_path()
    if p.exists():
        p.unlink()
