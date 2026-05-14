"""资产库存储: SQLite 元数据 + 文件系统资产.

约定:
  data/
  ├── assets.db                     # SQLite, 元数据 (整张 card JSON)
  └── assets/
      └── <char_id>/
          ├── front.png             # 按 required_views 顺序命名
          ├── side.png
          └── 3-quarter.png

设计:
- SQLite 只存 card JSON (整体序列化),不展开字段 → schema 演进无 ALTER TABLE 负担
- 文件系统是真相,SQLite 是索引;DB 丢失可从文件系统重建 (后续 phase 加 rebuild_index)
- 同步 API (sqlite3 模块, store 用法都是控制流非热路径)

Phase 1 范围: 单机本地, 不考虑并发写
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Iterator, Optional

from .schema import CharacterCard


# --- 路径 ---

DEFAULT_DATA_DIR = Path(os.environ.get("VIDEO1_DATA_DIR", "./data")).resolve()


def _data_dir() -> Path:
    p = DEFAULT_DATA_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _db_path() -> Path:
    return _data_dir() / "assets.db"


def asset_dir(char_id: str) -> Path:
    """给定 char_id 返回资产目录 (不存在会创建)."""
    p = _data_dir() / "assets" / char_id
    p.mkdir(parents=True, exist_ok=True)
    return p


# --- DB ---

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS characters (
    char_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    profile_id  TEXT,
    payload     TEXT NOT NULL,  -- 整个 CharacterCard JSON
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_characters_profile
ON characters(profile_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    return conn


# --- CRUD ---


class StoreError(Exception):
    pass


def upsert_character(card: CharacterCard) -> None:
    """新增或覆写一个 character. 不动文件系统资产 — 资产文件由 builder 管理."""
    asset_dir(card.char_id)  # 确保目录存在
    payload = card.model_dump_json()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO characters (char_id, name, profile_id, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(char_id) DO UPDATE SET
                name=excluded.name,
                profile_id=excluded.profile_id,
                payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            (
                card.char_id, card.name, card.profile_id, payload,
                card.created_at, card.updated_at,
            ),
        )


def get_character(char_id: str) -> Optional[CharacterCard]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload FROM characters WHERE char_id = ?", (char_id,),
        ).fetchone()
    if not row:
        return None
    return CharacterCard.model_validate_json(row["payload"])


def list_characters(profile_id: Optional[str] = None) -> Iterator[CharacterCard]:
    sql = "SELECT payload FROM characters"
    params: tuple = ()
    if profile_id is not None:
        sql += " WHERE profile_id = ?"
        params = (profile_id,)
    sql += " ORDER BY created_at"
    with _connect() as conn:
        for row in conn.execute(sql, params):
            yield CharacterCard.model_validate_json(row["payload"])


def delete_character(char_id: str, *, delete_assets: bool = True) -> bool:
    """删除 character. delete_assets=True 时同时清理文件系统目录."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM characters WHERE char_id = ?", (char_id,))
        deleted = cur.rowcount > 0
    if deleted and delete_assets:
        path = _data_dir() / "assets" / char_id
        if path.exists():
            shutil.rmtree(path)
    return deleted


# --- 工具 ---


def reset_for_test() -> None:
    """清空 store. **测试用**, 生产代码不要调."""
    p = _data_dir()
    if (p / "assets.db").exists():
        (p / "assets.db").unlink()
    if (p / "assets").exists():
        shutil.rmtree(p / "assets")
