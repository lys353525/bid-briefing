# -*- coding: utf-8 -*-
"""SQLite 연동 계층.

원시 데이터 → 지표 스냅샷 → 발행 글 → 품질 결과를 한 파일(pseo.db)에 보관한다.
표준 라이브러리 sqlite3만 사용한다.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from config import BASE_DIR, KST, now_kst

SCHEMA_PATH = BASE_DIR / "schema.sql"


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or now_kst()).isoformat(timespec="seconds")


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------- 실행 이력
def start_run(conn: sqlite3.Connection, run_id: str, mode: str, as_of: str,
              window_start: str, window_end: str) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT OR REPLACE INTO runs
               (run_id, started_at, mode, as_of, window_start, window_end, status)
               VALUES (?, ?, ?, ?, ?, ?, 'running')""",
            (run_id, _iso(), mode, as_of, window_start, window_end),
        )


def finish_run(conn: sqlite3.Connection, run_id: str, *, status: str, counts: dict,
               note: str = "") -> None:
    with transaction(conn):
        conn.execute(
            """UPDATE runs SET finished_at=?, status=?, collected=?, computed=?,
               published=?, held=?, skipped=?, note=? WHERE run_id=?""",
            (_iso(), status, counts.get("collected", 0), counts.get("computed", 0),
             counts.get("published", 0), counts.get("held", 0), counts.get("skipped", 0),
             note or None, run_id),
        )


def get_run(conn: sqlite3.Connection, run_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()


# ---------------------------------------------------------------- 원시 데이터
def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def upsert_raw_items(conn: sqlite3.Connection, items: Iterable[dict], run_id: str,
                     source: str) -> dict:
    """자연키 기준 upsert. 내용이 바뀌면 revision을 올린다(정정 공고 감지)."""
    inserted = updated = unchanged = 0
    stamp = _iso()
    with transaction(conn):
        for item in items:
            natural_key = item["natural_key"]
            key_hash = _hash(natural_key)
            payload = json.dumps(item["payload"], ensure_ascii=False, sort_keys=True)
            payload_hash = _hash(payload)
            row = conn.execute(
                "SELECT payload_hash, revision FROM raw_items WHERE key_hash=?", (key_hash,)
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO raw_items
                       (key_hash, natural_key, source, biz_type, payload, payload_hash,
                        revision, first_seen, last_seen, run_id)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                    (key_hash, natural_key, source, item.get("biz_type"), payload,
                     payload_hash, stamp, stamp, run_id),
                )
                inserted += 1
            elif row["payload_hash"] != payload_hash:
                conn.execute(
                    """UPDATE raw_items SET payload=?, payload_hash=?, revision=revision+1,
                       last_seen=?, run_id=? WHERE key_hash=?""",
                    (payload, payload_hash, stamp, run_id, key_hash),
                )
                updated += 1
            else:
                conn.execute(
                    "UPDATE raw_items SET last_seen=?, run_id=? WHERE key_hash=?",
                    (stamp, run_id, key_hash),
                )
                unchanged += 1
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}


def load_raw_items(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT payload, biz_type, revision FROM raw_items").fetchall()
    out = []
    for row in rows:
        item = json.loads(row["payload"])
        item.setdefault("biz_type", row["biz_type"])
        item["_revision"] = row["revision"]
        out.append(item)
    return out


def raw_item_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM raw_items").fetchone()["c"]


# ---------------------------------------------------------------- 지표 스냅샷
def save_metric_snapshot(conn: sqlite3.Connection, *, snapshot_id: str, run_id: str,
                         scope_type: str, scope_value: str, window_start: str,
                         window_end: str, facts: dict) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT OR REPLACE INTO metric_snapshots
               (snapshot_id, run_id, scope_type, scope_value, window_start, window_end,
                facts, computed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_id, run_id, scope_type, scope_value, window_start, window_end,
             json.dumps(facts, ensure_ascii=False), _iso()),
        )


def get_metric_snapshot(conn: sqlite3.Connection, scope_type: str, scope_value: str,
                        window_end: str) -> Optional[dict]:
    row = conn.execute(
        """SELECT facts FROM metric_snapshots
           WHERE scope_type=? AND scope_value=? AND window_end=?""",
        (scope_type, scope_value, window_end),
    ).fetchone()
    return json.loads(row["facts"]) if row else None


# ---------------------------------------------------------------- 발행 글
def get_page(conn: sqlite3.Connection, page_key: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM pages WHERE slug=? OR page_key=?", (page_key, page_key)).fetchone()


def upsert_page(conn: sqlite3.Connection, page: dict) -> None:
    stamp = _iso()
    existing = get_page(conn, page.get("slug")) or get_page(conn, page["page_key"])
    first_published = existing["first_published_at"] if existing and existing["first_published_at"] else (
        stamp if page.get("status") == "published" else None
    )
    with transaction(conn):
        conn.execute(
            """INSERT INTO pages
               (page_key, slug, title, description, category, scope_type, scope_value,
                window_start, window_end, content_hash, body_chars, quality_score,
                status, file_path, first_published_at, updated_at, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(slug) DO UPDATE SET
                 page_key=excluded.page_key, title=excluded.title, description=excluded.description,
                 category=excluded.category, scope_type=excluded.scope_type, scope_value=excluded.scope_value,
                 window_start=excluded.window_start, window_end=excluded.window_end,
                 content_hash=excluded.content_hash, body_chars=excluded.body_chars,
                 quality_score=excluded.quality_score, status=excluded.status,
                 file_path=excluded.file_path, updated_at=excluded.updated_at,
                 run_id=excluded.run_id""",
            (page["page_key"], page["slug"], page["title"], page["description"],
             page["category"], page["scope_type"], page["scope_value"], page["window_start"],
             page["window_end"], page.get("content_hash"), page.get("body_chars"),
             page.get("quality_score"), page["status"], page.get("file_path"),
             first_published, stamp, page.get("run_id")),
        )


def published_shingles(conn: sqlite3.Connection, exclude_page_key: str = "") -> dict[str, set[str]]:
    rows = conn.execute(
        "SELECT page_key, shingle FROM page_shingles WHERE page_key != ?", (exclude_page_key,)
    ).fetchall()
    out: dict[str, set[str]] = {}
    for row in rows:
        out.setdefault(row["page_key"], set()).add(row["shingle"])
    return out


def replace_shingles(conn: sqlite3.Connection, page_key: str, shingles: Iterable[str]) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM page_shingles WHERE page_key=?", (page_key,))
        conn.executemany(
            "INSERT OR IGNORE INTO page_shingles (page_key, shingle) VALUES (?, ?)",
            [(page_key, s) for s in shingles],
        )


def list_pages(conn: sqlite3.Connection, status: Optional[str] = None) -> list[sqlite3.Row]:
    if status:
        return conn.execute("SELECT * FROM pages WHERE status=? ORDER BY window_end DESC, page_key",
                            (status,)).fetchall()
    return conn.execute("SELECT * FROM pages ORDER BY window_end DESC, page_key").fetchall()


# ---------------------------------------------------------------- 품질 검증
def record_quality(conn: sqlite3.Connection, *, page_key: str, run_id: str, passed: bool,
                   score: float, details: list[dict]) -> None:
    with transaction(conn):
        conn.execute(
            """INSERT INTO quality_checks (page_key, run_id, passed, score, details, checked_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (page_key, run_id, 1 if passed else 0, score,
             json.dumps(details, ensure_ascii=False), _iso()),
        )


# ---------------------------------------------------------------- 사건 기록
def log_incident(conn: sqlite3.Connection, *, run_id: str, level: str, code: str,
                 message: str) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO incidents (run_id, level, code, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, level, code, message, _iso()),
        )


def db_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    def one(sql: str, *args) -> Any:
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else None

    return {
        "raw_items": one("SELECT COUNT(*) FROM raw_items"),
        "revisions": one("SELECT COUNT(*) FROM raw_items WHERE revision > 1"),
        "metric_snapshots": one("SELECT COUNT(*) FROM metric_snapshots"),
        "pages_published": one("SELECT COUNT(*) FROM pages WHERE status='published'"),
        "pages_held": one("SELECT COUNT(*) FROM pages WHERE status='held'"),
        "runs": one("SELECT COUNT(*) FROM runs"),
        "last_run": one("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1"),
    }
