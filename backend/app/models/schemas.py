from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class QueryLevel(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    AUTO = "auto"


class QueryStatus(str, Enum):
    SUCCESS = "success"
    NEEDS_CLARIFICATION = "needs_clarification"
    REJECTED = "rejected"
    EMPTY = "empty"
    TIMEOUT = "timeout"
    ERROR = "error"


class SafetyLayerResult(BaseModel):
    layer: str
    passed: bool
    message: str
    violations: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    passed: bool
    layers: List[SafetyLayerResult] = Field(default_factory=list)
    violation_category: Optional[str] = None
    explain_summary: Optional[Dict[str, Any]] = None
    sanitized_query: Optional[str] = None


class EntityCandidate(BaseModel):
    node_id: str
    label: str
    name: str
    qualified_name: Optional[str] = None
    class_name: Optional[str] = None
    service_contexts: List[str] = Field(default_factory=list)
    module_id: Optional[str] = None
    source_file: Optional[str] = None
    source_line: Optional[int] = None
    is_entry: Optional[bool] = None
    is_abstract: Optional[bool] = None
    graph_version: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class ResolvedEntity(BaseModel):
    mention: str
    entity_type: str
    status: Literal["bound", "ambiguous", "not_found"]
    bound: Optional[EntityCandidate] = None
    candidates: List[EntityCandidate] = Field(default_factory=list)


class SubquerySpec(BaseModel):
    subquery_id: str
    mechanism: Literal["STRUCTURAL", "RPC", "MQ", "IMPACT"]
    description: str
    direction: Literal["forward", "reverse", "both", "none"] = "forward"
    max_hops: int = 6
    cypher: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)


class QueryPlan(BaseModel):
    intent: str
    category: str
    level: QueryLevel
    direction: Literal["forward", "reverse", "both", "none"] = "none"
    max_hops: int = 6
    mechanisms: List[str] = Field(default_factory=list)
    expected_shape: Literal["entities", "count", "paths", "hybrid_dependencies", "mq_chain", "impact"] = "entities"
    entities: List[ResolvedEntity] = Field(default_factory=list)
    requires_decomposition: bool = False
    subqueries: List[SubquerySpec] = Field(default_factory=list)
    join_keys: List[str] = Field(default_factory=lambda: ["nodeId", "mechanism"])
    selected_skills: List[str] = Field(default_factory=list)
    retrieval_mode: str = "tfidf"


class CypherCandidate(BaseModel):
    query: str
    params: Dict[str, Any] = Field(default_factory=dict)
    schema_hash: str
    skill_version: str
    model_used: str
    generation_mode: Literal["llm", "hybrid_planner", "repaired"] = "llm"
    reasoning: Optional[str] = None
    subquery_id: Optional[str] = None
    mechanism: Optional[str] = None


class GraphNode(BaseModel):
    id: str
    label: str
    node_type: str
    service: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    rel_type: str
    label: str
    mechanism: str = "STRUCTURAL"
    properties: Dict[str, Any] = Field(default_factory=dict)


class PathEvidence(BaseModel):
    mechanism: str
    hops: int
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    summary: str


class ExecutionResult(BaseModel):
    records: List[Dict[str, Any]] = Field(default_factory=list)
    total_records: int = 0
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)
    paths: List[PathEvidence] = Field(default_factory=list)
    display_truncated: bool = False
    total_nodes_before_truncation: int = 0
    total_edges_before_truncation: int = 0
    elapsed_ms: float = 0.0
    complete: bool = True
    error: Optional[str] = None


class StageTrace(BaseModel):
    stage: str
    status: Literal["ok", "warning", "failed", "skipped"]
    latency_ms: float
    summary: str
    details: Dict[str, Any] = Field(default_factory=dict)


class RepairAttempt(BaseModel):
    round: int
    trigger_reason: str
    original_cypher: str
    repaired_cypher: str
    validation_passed: bool
    error: Optional[str] = None


class SemanticCheckResult(BaseModel):
    passed: bool
    issues: List[str] = Field(default_factory=list)
    diagnosis: Optional[str] = None
    suggested_fix: Optional[str] = None


class QueryTrace(BaseModel):
    trace_id: str
    request_id: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    schema_hash: str = ""
    graph_version: str = ""
    schema_source: str = "live"
    skill_versions: Dict[str, str] = Field(default_factory=dict)
    kb_version: str = "kb30-v1.0"
    model_provider: str = "deepseek"
    model_version: str = "deepseek-chat"
    execution_mode: str = "hybrid_planner"
    code_commit: str = "workspace"
    total_latency_ms: float = 0.0
    retrieved_examples: List[Dict[str, Any]] = Field(default_factory=list)
    stages: List[StageTrace] = Field(default_factory=list)
    repairs: List[RepairAttempt] = Field(default_factory=list)
    semantic_check: Optional[SemanticCheckResult] = None


class QueryOptions(BaseModel):
    max_hops: int = Field(default=6, ge=1, le=10)
    clarify_on_ambiguity: bool = True
    use_kb_rag: bool = True
    use_llm_rerank: bool = True
    use_decomposition: bool = True
    use_semantic_check: bool = True
    use_llm_summary: bool = True
    force_offline: bool = False
    allow_planner_fallback: bool = True
    selected_entity_ids: Dict[str, str] = Field(default_factory=dict)
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    level: QueryLevel = QueryLevel.AUTO
    scope: Optional[str] = None  # optional microservice context
    options: QueryOptions = Field(default_factory=QueryOptions)
    request_id: Optional[str] = None


class QueryResponse(BaseModel):
    request_id: str
    trace_id: str
    question: str
    status: QueryStatus
    level: QueryLevel
    plan: Optional[QueryPlan] = None
    cypher_candidates: List[CypherCandidate] = Field(default_factory=list)
    primary_cypher: str = ""
    primary_params: Dict[str, Any] = Field(default_factory=dict)
    validation: Optional[ValidationReport] = None
    execution: ExecutionResult = Field(default_factory=ExecutionResult)
    summary: str = ""
    clarification_prompt: Optional[str] = None
    ambiguous_entities: List[ResolvedEntity] = Field(default_factory=list)
    trace: QueryTrace
    markdown_report: str = ""


class KBEntryBase(BaseModel):
    question: str = Field(..., min_length=2)
    cypher: str = Field(..., min_length=5)
    params: Dict[str, Any] = Field(default_factory=dict)
    category: str
    level: QueryLevel = QueryLevel.SIMPLE
    intent: str = ""
    direction: str = "none"
    relation_pattern: str = ""
    notes: str = ""
    approved: bool = True
    reviewer: str = "double-reviewed"


class KBEntryCreate(KBEntryBase):
    id: Optional[str] = None


class KBEntryUpdate(BaseModel):
    question: Optional[str] = None
    cypher: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    category: Optional[str] = None
    level: Optional[QueryLevel] = None
    intent: Optional[str] = None
    direction: Optional[str] = None
    relation_pattern: Optional[str] = None
    notes: Optional[str] = None
    approved: Optional[bool] = None
    reviewer: Optional[str] = None


class KBEntry(KBEntryBase):
    id: str
    version: int = 1
    verified: bool = False
    verified_row_count: int = 0
    verified_at: Optional[str] = None
    schema_hash: str = ""
    created_at: str = ""
    updated_at: str = ""


class BatchItemResult(BaseModel):
    index: int
    question: str
    level: QueryLevel
    status: str  # pending / running / success / needs_clarification / rejected / empty / error / cancelled
    trace_id: Optional[str] = None
    primary_cypher: str = ""
    record_count: int = 0
    summary: str = ""
    latency_ms: float = 0.0
    error: Optional[str] = None
    response: Optional[QueryResponse] = None


class BatchJob(BaseModel):
    batch_id: str
    status: Literal["pending", "running", "completed", "cancelled", "failed"]
    total: int
    completed: int
    succeeded: int
    failed: int
    created_at: str
    updated_at: str
    items: List[BatchItemResult] = Field(default_factory=list)


class SchemaSnapshot(BaseModel):
    graph_version: str
    graph_model: str
    neo4j_version: str
    schema_hash: str
    fetched_at: str
    source: Literal["live", "offline_snapshot"]
    node_labels: Dict[str, Dict[str, Any]]
    rel_types: Dict[str, Dict[str, Any]]
    rel_patterns: List[Dict[str, Any]]
    microservices: List[str]
    class_types: List[str]
    mq_flow_types: List[str]
    mq_queues: List[Dict[str, Any]]
    mq_exchanges: List[Dict[str, Any]]
