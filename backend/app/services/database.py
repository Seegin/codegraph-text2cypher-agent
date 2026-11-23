from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app.core.config import settings
from app.models.schemas import (
    BatchItemResult,
    BatchJob,
    KBEntry,
    KBEntryCreate,
    KBEntryUpdate,
    QueryLevel,
    QueryResponse,
)


class AppDatabase:
    """SQLite persistence for KB30 management, QueryTrace history, and BatchJob execution."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or settings.resolve_path(settings.sqlite_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()
        self.seed_kb_if_empty()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_tables(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kb_entries (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    cypher TEXT NOT NULL,
                    params_json TEXT NOT NULL DEFAULT '{}',
                    category TEXT NOT NULL,
                    level TEXT NOT NULL DEFAULT 'simple',
                    intent TEXT NOT NULL DEFAULT '',
                    direction TEXT NOT NULL DEFAULT 'none',
                    relation_pattern TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    approved INTEGER NOT NULL DEFAULT 1,
                    reviewer TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 1,
                    verified INTEGER NOT NULL DEFAULT 0,
                    verified_row_count INTEGER NOT NULL DEFAULT 0,
                    verified_at TEXT,
                    schema_hash TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS query_traces (
                    trace_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    status TEXT NOT NULL,
                    level TEXT NOT NULL,
                    primary_cypher TEXT NOT NULL DEFAULT '',
                    total_records INTEGER NOT NULL DEFAULT 0,
                    latency_ms REAL NOT NULL DEFAULT 0.0,
                    execution_mode TEXT NOT NULL DEFAULT '',
                    model_version TEXT NOT NULL DEFAULT '',
                    schema_hash TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    response_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS batch_jobs (
                    batch_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    succeeded INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS batch_items (
                    batch_id TEXT NOT NULL,
                    item_index INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    level TEXT NOT NULL,
                    status TEXT NOT NULL,
                    trace_id TEXT,
                    primary_cypher TEXT NOT NULL DEFAULT '',
                    record_count INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    latency_ms REAL NOT NULL DEFAULT 0.0,
                    error TEXT,
                    response_json TEXT,
                    PRIMARY KEY (batch_id, item_index)
                );
                """
            )

    def seed_kb_if_empty(self) -> int:
        with self._conn() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM kb_entries").fetchone()[0]
            if cnt > 0:
                return 0
            seed_path = settings.resolve_path(settings.kb_seed_path)
            if not seed_path.exists():
                return 0
            entries = json.loads(seed_path.read_text(encoding="utf-8"))
            now = datetime.utcnow().isoformat() + "Z"
            for item in entries:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO kb_entries (
                        id, question, cypher, params_json, category, level, intent, direction,
                        relation_pattern, notes, approved, reviewer, version, verified,
                        verified_row_count, verified_at, schema_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        item["question"],
                        item["cypher"],
                        json.dumps(item.get("params") or {}, ensure_ascii=False),
                        item["category"],
                        item.get("level", "simple"),
                        item.get("intent", ""),
                        item.get("direction", "none"),
                        item.get("relation_pattern", ""),
                        item.get("notes", ""),
                        1 if item.get("approved", True) else 0,
                        item.get("reviewer", ""),
                        int(item.get("version", 1)),
                        1 if item.get("verified", False) else 0,
                        int(item.get("verified_row_count", 0)),
                        item.get("verified_at"),
                        item.get("schema_hash", ""),
                        item.get("created_at") or now,
                        item.get("updated_at") or now,
                    ),
                )
            return len(entries)

    def _row_to_kb(self, row: sqlite3.Row) -> KBEntry:
        return KBEntry(
            id=row["id"],
            question=row["question"],
            cypher=row["cypher"],
            params=json.loads(row["params_json"] or "{}"),
            category=row["category"],
            level=QueryLevel(row["level"]),
            intent=row["intent"],
            direction=row["direction"],
            relation_pattern=row["relation_pattern"],
            notes=row["notes"],
            approved=bool(row["approved"]),
            reviewer=row["reviewer"],
            version=row["version"],
            verified=bool(row["verified"]),
            verified_row_count=row["verified_row_count"],
            verified_at=row["verified_at"],
            schema_hash=row["schema_hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_kb(
        self,
        keyword: Optional[str] = None,
        category: Optional[str] = None,
        level: Optional[str] = None,
    ) -> List[KBEntry]:
        sql = "SELECT * FROM kb_entries WHERE 1=1"
        args: List[Any] = []
        if category and category != "all":
            sql += " AND category = ?"
            args.append(category)
        if level and level != "all":
            sql += " AND level = ?"
            args.append(level)
        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            sql += " AND (question LIKE ? OR cypher LIKE ? OR notes LIKE ? OR category LIKE ? OR id LIKE ?)"
            args.extend([kw, kw, kw, kw, kw])
        sql += " ORDER BY id ASC"
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._row_to_kb(r) for r in rows]

    def get_kb(self, entry_id: str) -> Optional[KBEntry]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM kb_entries WHERE id = ?", (entry_id,)).fetchone()
        return self._row_to_kb(row) if row else None

    def create_kb(self, data: KBEntryCreate) -> KBEntry:
        now = datetime.utcnow().isoformat() + "Z"
        eid = data.id or f"KB-{uuid.uuid4().hex[:6].upper()}"
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO kb_entries (
                    id, question, cypher, params_json, category, level, intent, direction,
                    relation_pattern, notes, approved, reviewer, version, verified,
                    verified_row_count, verified_at, schema_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, NULL, '', ?, ?)
                """,
                (
                    eid,
                    data.question,
                    data.cypher,
                    json.dumps(data.params, ensure_ascii=False),
                    data.category,
                    data.level.value,
                    data.intent,
                    data.direction,
                    data.relation_pattern,
                    data.notes,
                    1 if data.approved else 0,
                    data.reviewer,
                    now,
                    now,
                ),
            )
        return self.get_kb(eid)  # type: ignore[return-value]

    def update_kb(self, entry_id: str, patch: KBEntryUpdate) -> Optional[KBEntry]:
        existing = self.get_kb(entry_id)
        if not existing:
            return None
        upd = existing.model_dump()
        for k, v in patch.model_dump(exclude_unset=True).items():
            if v is not None:
                upd[k] = v
        now = datetime.utcnow().isoformat() + "Z"
        cypher_changed = patch.cypher is not None and patch.cypher != existing.cypher
        new_ver = existing.version + 1
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE kb_entries SET
                    question=?, cypher=?, params_json=?, category=?, level=?, intent=?,
                    direction=?, relation_pattern=?, notes=?, approved=?, reviewer=?,
                    version=?, verified=?, updated_at=?
                WHERE id=?
                """,
                (
                    upd["question"],
                    upd["cypher"],
                    json.dumps(upd["params"], ensure_ascii=False),
                    upd["category"],
                    upd["level"].value if isinstance(upd["level"], QueryLevel) else upd["level"],
                    upd["intent"],
                    upd["direction"],
                    upd["relation_pattern"],
                    upd["notes"],
                    1 if upd["approved"] else 0,
                    upd["reviewer"],
                    new_ver,
                    0 if cypher_changed else (1 if existing.verified else 0),
                    now,
                    entry_id,
                ),
            )
        return self.get_kb(entry_id)

    def mark_kb_verified(self, entry_id: str, verified: bool, row_count: int, schema_hash: str) -> Optional[KBEntry]:
        now = datetime.utcnow().isoformat() + "Z"
        with self._conn() as conn:
            conn.execute(
                "UPDATE kb_entries SET verified=?, verified_row_count=?, verified_at=?, schema_hash=?, updated_at=? WHERE id=?",
                (1 if verified else 0, row_count, now, schema_hash, now, entry_id),
            )
        return self.get_kb(entry_id)

    def delete_kb(self, entry_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM kb_entries WHERE id = ?", (entry_id,))
            return cur.rowcount > 0

    # --- Query Traces ---
    def save_trace(self, resp: QueryResponse) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO query_traces (
                    trace_id, request_id, question, status, level, primary_cypher,
                    total_records, latency_ms, execution_mode, model_version,
                    schema_hash, created_at, response_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resp.trace_id,
                    resp.request_id,
                    resp.question,
                    resp.status.value,
                    resp.level.value,
                    resp.primary_cypher,
                    resp.execution.total_records,
                    resp.trace.total_latency_ms,
                    resp.trace.execution_mode,
                    resp.trace.model_version,
                    resp.trace.schema_hash,
                    resp.trace.timestamp,
                    resp.model_dump_json(),
                ),
            )

    def list_traces(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT trace_id, request_id, question, status, level, primary_cypher,
                       total_records, latency_ms, execution_mode, model_version, schema_hash, created_at
                FROM query_traces ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_trace(self, trace_id: str) -> Optional[QueryResponse]:
        with self._conn() as conn:
            row = conn.execute("SELECT response_json FROM query_traces WHERE trace_id = ?", (trace_id,)).fetchone()
        if not row:
            return None
        return QueryResponse.model_validate_json(row["response_json"])

    # --- Batch Jobs ---
    def create_batch(self, batch_id: str, items: List[Dict[str, Any]]) -> BatchJob:
        now = datetime.utcnow().isoformat() + "Z"
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO batch_jobs (batch_id, status, total, completed, succeeded, failed, created_at, updated_at) VALUES (?, 'pending', ?, 0, 0, 0, ?, ?)",
                (batch_id, len(items), now, now),
            )
            for idx, it in enumerate(items):
                conn.execute(
                    "INSERT INTO batch_items (batch_id, item_index, question, level, status) VALUES (?, ?, ?, ?, 'pending')",
                    (batch_id, idx, it["question"], it.get("level", "auto")),
                )
        return self.get_batch(batch_id)  # type: ignore[return-value]

    def update_batch_status(self, batch_id: str, status: str) -> None:
        now = datetime.utcnow().isoformat() + "Z"
        with self._conn() as conn:
            conn.execute("UPDATE batch_jobs SET status=?, updated_at=? WHERE batch_id=?", (status, now, batch_id))

    def update_batch_item(
        self,
        batch_id: str,
        index: int,
        status: str,
        resp: Optional[QueryResponse] = None,
        error: Optional[str] = None,
    ) -> None:
        now = datetime.utcnow().isoformat() + "Z"
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE batch_items SET
                    status=?, trace_id=?, primary_cypher=?, record_count=?, summary=?,
                    latency_ms=?, error=?, response_json=?
                WHERE batch_id=? AND item_index=?
                """,
                (
                    status,
                    resp.trace_id if resp else None,
                    resp.primary_cypher if resp else "",
                    resp.execution.total_records if resp else 0,
                    resp.summary if resp else "",
                    resp.trace.total_latency_ms if resp else 0.0,
                    error,
                    resp.model_dump_json() if resp else None,
                    batch_id,
                    index,
                ),
            )
            agg = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status NOT IN ('pending','running') THEN 1 ELSE 0 END) AS done_cnt,
                    SUM(CASE WHEN status IN ('success','empty') THEN 1 ELSE 0 END) AS ok_cnt,
                    SUM(CASE WHEN status IN ('error','rejected','timeout') THEN 1 ELSE 0 END) AS err_cnt
                FROM batch_items WHERE batch_id=?
                """,
                (batch_id,),
            ).fetchone()
            conn.execute(
                "UPDATE batch_jobs SET completed=?, succeeded=?, failed=?, updated_at=? WHERE batch_id=?",
                (agg["done_cnt"] or 0, agg["ok_cnt"] or 0, agg["err_cnt"] or 0, now, batch_id),
            )

    def get_batch(self, batch_id: str, include_responses: bool = False) -> Optional[BatchJob]:
        with self._conn() as conn:
            job = conn.execute("SELECT * FROM batch_jobs WHERE batch_id=?", (batch_id,)).fetchone()
            if not job:
                return None
            rows = conn.execute(
                "SELECT * FROM batch_items WHERE batch_id=? ORDER BY item_index ASC",
                (batch_id,),
            ).fetchall()
        items = []
        for r in rows:
            resp_obj = None
            if include_responses and r["response_json"]:
                resp_obj = QueryResponse.model_validate_json(r["response_json"])
            items.append(
                BatchItemResult(
                    index=r["item_index"],
                    question=r["question"],
                    level=QueryLevel(r["level"]),
                    status=r["status"],
                    trace_id=r["trace_id"],
                    primary_cypher=r["primary_cypher"],
                    record_count=r["record_count"],
                    summary=r["summary"],
                    latency_ms=r["latency_ms"],
                    error=r["error"],
                    response=resp_obj,
                )
            )
        return BatchJob(
            batch_id=job["batch_id"],
            status=job["status"],
            total=job["total"],
            completed=job["completed"],
            succeeded=job["succeeded"],
            failed=job["failed"],
            created_at=job["created_at"],
            updated_at=job["updated_at"],
            items=items,
        )

    def list_batches(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT batch_id, status, total, completed, succeeded, failed, created_at, updated_at FROM batch_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


app_db = AppDatabase()
