from __future__ import annotations

import csv
import io
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Set

from app.core.config import settings
from app.models.schemas import BatchJob, QueryLevel, QueryOptions, QueryRequest
from app.services.database import app_db
from app.services.orchestrator import orchestrator


class BatchQueryManager:
    """
    Controlled Concurrent Batch Execution Engine (Appendix C):
    - Max 50 questions per batch
    - Concurrency strictly capped at 2 via ThreadPoolExecutor(max_workers=2)
    - Strict per-question failure isolation (1 failure never aborts remaining questions)
    - Instant cooperative cancellation support
    """

    def __init__(self) -> None:
        self._cancel_flags: Set[str] = set()
        self._lock = threading.Lock()

    def parse_uploaded_questions(self, filename: str, content: bytes) -> List[Dict[str, str]]:
        text = content.decode("utf-8-sig", errors="replace").strip()
        if not text:
            return []
        low_name = filename.lower()
        items: List[Dict[str, str]] = []

        if low_name.endswith(".json"):
            data = json.loads(text)
            if isinstance(data, list):
                for row in data:
                    if isinstance(row, str) and row.strip():
                        items.append({"question": row.strip(), "level": "auto"})
                    elif isinstance(row, dict) and row.get("question"):
                        items.append(
                            {
                                "question": str(row["question"]).strip(),
                                "level": str(row.get("level", "auto")).lower(),
                            }
                        )
        elif low_name.endswith(".csv"):
            reader = csv.DictReader(io.StringIO(text))
            if reader.fieldnames and any(f.lower() in ("question", "问题", "query") for f in reader.fieldnames):
                q_col = next(f for f in reader.fieldnames if f.lower() in ("question", "问题", "query"))
                lvl_col = next((f for f in reader.fieldnames if f.lower() in ("level", "难度", "difficulty")), None)
                for row in reader:
                    q = (row.get(q_col) or "").strip()
                    if q:
                        lvl = (row.get(lvl_col) or "auto").strip().lower() if lvl_col else "auto"
                        if lvl not in ("simple", "medium", "complex", "auto"):
                            lvl = {"简单": "simple", "中等": "medium", "复杂": "complex", "困难": "complex"}.get(lvl, "auto")
                        items.append({"question": q, "level": lvl})
            else:
                for line in text.splitlines():
                    q = line.strip().strip(",")
                    if q:
                        items.append({"question": q, "level": "auto"})
        else:
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "|" in line:
                    parts = line.split("|", 1)
                    lvl = parts[0].strip().lower()
                    if lvl in ("simple", "medium", "complex", "auto"):
                        items.append({"question": parts[1].strip(), "level": lvl})
                        continue
                items.append({"question": line, "level": "auto"})

        return items

    async def submit_batch(
        self,
        items: List[Dict[str, str]],
        options: Optional[QueryOptions] = None,
    ) -> BatchJob:
        if not items:
            raise ValueError("批量任务为空，请至少提供 1 条自然语言问题。")
        if len(items) > settings.batch_max_size:
            raise ValueError(f"单批最多允许 {settings.batch_max_size} 条问题，当前提交了 {len(items)} 条。")

        batch_id = f"batch-{uuid.uuid4().hex[:10]}"
        job = app_db.create_batch(batch_id, items)
        batch_opts = (options or QueryOptions()).model_copy(
            update={"clarify_on_ambiguity": False}
        )
        t = threading.Thread(
            target=self._run_batch_sync,
            args=(batch_id, items, batch_opts),
            daemon=True,
        )
        t.start()
        return job

    def cancel_batch(self, batch_id: str) -> bool:
        with self._lock:
            self._cancel_flags.add(batch_id)
        job = app_db.get_batch(batch_id)
        if not job:
            return False
        if job.status in ("pending", "running"):
            app_db.update_batch_status(batch_id, "cancelled")
        return True

    def _is_cancelled(self, batch_id: str) -> bool:
        with self._lock:
            return batch_id in self._cancel_flags

    def _run_batch_sync(
        self,
        batch_id: str,
        items: List[Dict[str, str]],
        base_options: QueryOptions,
    ) -> None:
        app_db.update_batch_status(batch_id, "running")

        def _worker(idx: int, item: Dict[str, str]) -> None:
            if self._is_cancelled(batch_id):
                app_db.update_batch_item(batch_id, idx, "cancelled", error="用户已取消批量任务")
                return
            app_db.update_batch_item(batch_id, idx, "running")
            try:
                lvl_str = item.get("level", "auto")
                lvl = QueryLevel(lvl_str) if lvl_str in ("simple", "medium", "complex", "auto") else QueryLevel.AUTO
                req = QueryRequest(
                    question=item["question"],
                    level=lvl,
                    options=base_options,
                    request_id=f"{batch_id}-{idx + 1}",
                )
                resp = orchestrator.run_query(req, save_trace=True)
                app_db.update_batch_item(
                    batch_id,
                    idx,
                    resp.status.value,
                    resp=resp,
                    error=resp.execution.error,
                )
            except Exception as exc:
                app_db.update_batch_item(batch_id, idx, "error", error=str(exc))

        with ThreadPoolExecutor(max_workers=settings.batch_concurrency) as pool:
            futures = [pool.submit(_worker, i, it) for i, it in enumerate(items)]
            for f in futures:
                f.result()

        if self._is_cancelled(batch_id):
            app_db.update_batch_status(batch_id, "cancelled")
            with self._lock:
                self._cancel_flags.discard(batch_id)
        else:
            app_db.update_batch_status(batch_id, "completed")


batch_manager = BatchQueryManager()
