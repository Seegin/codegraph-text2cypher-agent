from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.models.schemas import (
    BatchJob,
    KBEntry,
    KBEntryCreate,
    KBEntryUpdate,
    QueryOptions,
    QueryRequest,
    QueryResponse,
    SchemaSnapshot,
    ValidationReport,
)
from app.pipeline.executor import cypher_executor
from app.pipeline.llm_client import llm_client
from app.pipeline.schema_fetcher import neo4j_manager, schema_fetcher
from app.pipeline.skill_loader import skill_loader
from app.pipeline.validator import cypher_validator
from app.services.batch_manager import batch_manager
from app.services.database import app_db
from app.services.orchestrator import orchestrator

router = APIRouter(prefix="/api", tags=["Text2Cypher Agent"])


class CypherValidateRequest(BaseModel):
    cypher: str
    params: Dict[str, Any] = Field(default_factory=dict)
    run_explain: bool = True


class BatchSubmitRequest(BaseModel):
    questions: List[Dict[str, str]]
    options: QueryOptions = Field(default_factory=QueryOptions)


@router.get("/health")
def health_check() -> Dict[str, Any]:
    neo_health = neo4j_manager.check_health()
    schema = schema_fetcher.get_schema(force_refresh=False)
    return {
        "status": "ok",
        "neo4j": neo_health,
        "schema_hash": schema.schema_hash,
        "graph_version": schema.graph_version,
        "schema_source": schema.source,
        "kb_count": len(app_db.list_kb()),
        "skills": skill_loader.get_versions(),
        "approved_providers": llm_client.list_approved_providers(),
        "budgets": {
            "default_max_hops": settings.default_max_hops,
            "hard_max_hops": settings.hard_max_hops,
            "db_timeout_sec": settings.neo4j_query_timeout_sec,
            "max_repair_rounds": settings.max_repair_rounds,
            "batch_max_size": settings.batch_max_size,
            "batch_concurrency": settings.batch_concurrency,
            "display_max_nodes": settings.display_max_nodes,
            "display_max_edges": settings.display_max_edges,
        },
    }


@router.get("/schema", response_model=SchemaSnapshot)
def get_schema() -> SchemaSnapshot:
    return schema_fetcher.get_schema(force_refresh=False)


@router.post("/schema/refresh", response_model=SchemaSnapshot)
def refresh_schema() -> SchemaSnapshot:
    return schema_fetcher.get_schema(force_refresh=True)


@router.get("/skills")
def list_skills() -> List[Dict[str, Any]]:
    return [
        {
            "name": s.name,
            "version": s.version,
            "description": s.description,
            "applies_to": s.applies_to,
            "schema_version": s.schema_version,
        }
        for s in skill_loader.list_skills()
    ]


@router.post("/query", response_model=QueryResponse)
def execute_query(req: QueryRequest) -> QueryResponse:
    return orchestrator.run_query(req, save_trace=True)


@router.post("/query/validate", response_model=ValidationReport)
def validate_cypher_only(req: CypherValidateRequest) -> ValidationReport:
    schema = schema_fetcher.get_schema(force_refresh=False)
    return cypher_validator.validate(req.cypher, req.params, schema, run_explain=req.run_explain)


@router.get("/traces")
def list_traces(limit: int = Query(default=30, ge=1, le=200)) -> List[Dict[str, Any]]:
    return app_db.list_traces(limit=limit)


@router.get("/traces/{trace_id}", response_model=QueryResponse)
def get_trace(trace_id: str) -> QueryResponse:
    resp = app_db.get_trace(trace_id)
    if not resp:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' 不存在")
    return resp


@router.get("/traces/{trace_id}/markdown", response_class=PlainTextResponse)
def download_trace_markdown(trace_id: str) -> PlainTextResponse:
    resp = app_db.get_trace(trace_id)
    if not resp:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' 不存在")
    return PlainTextResponse(
        content=resp.markdown_report,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="report_{trace_id}.md"'},
    )


# ===== KB30 Knowledge Base Management =====
@router.get("/kb", response_model=List[KBEntry])
def list_kb(
    keyword: Optional[str] = None,
    category: Optional[str] = None,
    level: Optional[str] = None,
) -> List[KBEntry]:
    return app_db.list_kb(keyword=keyword, category=category, level=level)


@router.post("/kb", response_model=KBEntry, status_code=201)
def create_kb(entry: KBEntryCreate) -> KBEntry:
    schema = schema_fetcher.get_schema(force_refresh=False)
    val = cypher_validator.validate(entry.cypher, entry.params, schema, run_explain=False)
    if not val.passed and val.violation_category in ("WRITE_OR_PROCEDURE_BLOCKED", "INPUT_OR_BOUNDS_VIOLATION"):
        raise HTTPException(
            status_code=400,
            detail="知识库示例必须满足只读安全约束: " + "; ".join(l.message for l in val.layers if not l.passed),
        )
    return app_db.create_kb(entry)


@router.put("/kb/{entry_id}", response_model=KBEntry)
def update_kb(entry_id: str, patch: KBEntryUpdate) -> KBEntry:
    if patch.cypher:
        schema = schema_fetcher.get_schema(force_refresh=False)
        val = cypher_validator.validate(patch.cypher, patch.params or {}, schema, run_explain=False)
        if not val.passed and val.violation_category in ("WRITE_OR_PROCEDURE_BLOCKED", "INPUT_OR_BOUNDS_VIOLATION"):
            raise HTTPException(
                status_code=400,
                detail="修改后的 Cypher 违反只读安全约束: " + "; ".join(l.message for l in val.layers if not l.passed),
            )
    updated = app_db.update_kb(entry_id, patch)
    if not updated:
        raise HTTPException(status_code=404, detail=f"知识库条目 '{entry_id}' 不存在")
    return updated


@router.delete("/kb/{entry_id}")
def delete_kb(entry_id: str) -> Dict[str, Any]:
    ok = app_db.delete_kb(entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"知识库条目 '{entry_id}' 不存在")
    return {"deleted": True, "id": entry_id}


@router.post("/kb/{entry_id}/verify")
def verify_kb_entry(entry_id: str) -> Dict[str, Any]:
    entry = app_db.get_kb(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"知识库条目 '{entry_id}' 不存在")
    schema = schema_fetcher.get_schema(force_refresh=False)
    val = cypher_validator.validate(entry.cypher, entry.params, schema, run_explain=True)
    if not val.passed:
        app_db.mark_kb_verified(entry_id, False, 0, schema.schema_hash)
        return {"verified": False, "validation": val.model_dump(), "row_count": 0}
    exe = cypher_executor.execute(val.sanitized_query or entry.cypher, entry.params)
    ok = (not exe.error) and (exe.total_records > 0)
    updated = app_db.mark_kb_verified(entry_id, ok, exe.total_records, schema.schema_hash)
    return {
        "verified": ok,
        "row_count": exe.total_records,
        "elapsed_ms": exe.elapsed_ms,
        "nodes_count": len(exe.nodes),
        "edges_count": len(exe.edges),
        "error": exe.error,
        "entry": updated.model_dump() if updated else None,
        "validation": val.model_dump(),
    }


# ===== Batch Query Endpoints =====
@router.post("/batch", response_model=BatchJob)
async def submit_batch_json(req: BatchSubmitRequest) -> BatchJob:
    try:
        return await batch_manager.submit_batch(req.questions, req.options)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/batch/upload", response_model=BatchJob)
async def upload_batch_file(
    file: UploadFile = File(...),
    provider: Optional[str] = Form(default=None),
    model: Optional[str] = Form(default=None),
    api_key: Optional[str] = Form(default=None),
    base_url: Optional[str] = Form(default=None),
) -> BatchJob:
    content = await file.read()
    items = batch_manager.parse_uploaded_questions(file.filename or "batch.txt", content)
    opts = QueryOptions(
        provider=provider or None,
        model=model or None,
        api_key=api_key or None,
        base_url=base_url or None,
        clarify_on_ambiguity=False,
    )
    try:
        return await batch_manager.submit_batch(items, opts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/batch")
def list_batches() -> List[Dict[str, Any]]:
    return app_db.list_batches(limit=20)


@router.get("/batch/{batch_id}", response_model=BatchJob)
def get_batch(batch_id: str, include_responses: bool = False) -> BatchJob:
    job = app_db.get_batch(batch_id, include_responses=include_responses)
    if not job:
        raise HTTPException(status_code=404, detail=f"批量任务 '{batch_id}' 不存在")
    return job


@router.post("/batch/{batch_id}/cancel")
def cancel_batch(batch_id: str) -> Dict[str, Any]:
    ok = batch_manager.cancel_batch(batch_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"批量任务 '{batch_id}' 不存在")
    return {"cancelled": True, "batch_id": batch_id}


# ===== Evaluation & Safety Audit Summary =====
@router.get("/eval/summary")
def get_eval_summary() -> Dict[str, Any]:
    report_path = settings.resolve_path("eval/results/latest_eval_report.json")
    ablation_path = settings.resolve_path("eval/results/ablation_study.json")
    golden_path = settings.resolve_path(settings.golden_set_path)
    out: Dict[str, Any] = {"has_report": False, "golden_count": 0, "report": None, "ablation": None}
    if golden_path.exists():
        try:
            g = json.loads(golden_path.read_text(encoding="utf-8"))
            out["golden_count"] = len(g)
        except Exception:
            pass
    if report_path.exists():
        try:
            out["report"] = json.loads(report_path.read_text(encoding="utf-8"))
            out["has_report"] = True
        except Exception:
            pass
    if ablation_path.exists():
        try:
            out["ablation"] = json.loads(ablation_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return out
