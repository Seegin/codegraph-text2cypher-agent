export type QueryLevel = 'simple' | 'medium' | 'complex' | 'auto'
export type QueryStatus =
  | 'success'
  | 'needs_clarification'
  | 'rejected'
  | 'empty'
  | 'timeout'
  | 'error'

export interface EntityCandidate {
  node_id: string
  label: string
  name: string
  qualified_name?: string
  class_name?: string
  service_contexts: string[]
  module_id?: string
  source_file?: string
  source_line?: number
  is_entry?: boolean
  is_abstract?: boolean
  graph_version?: string
  extra?: Record<string, any>
}

export interface ResolvedEntity {
  mention: string
  entity_type: string
  status: 'bound' | 'ambiguous' | 'not_found'
  bound?: EntityCandidate
  candidates: EntityCandidate[]
}

export interface SafetyLayerResult {
  layer: string
  passed: boolean
  message: string
  violations: string[]
  details?: Record<string, any>
}

export interface ValidationReport {
  passed: boolean
  layers: SafetyLayerResult[]
  violation_category?: string
  explain_summary?: Record<string, any>
  sanitized_query?: string
}

export interface GraphNode {
  id: string
  label: string
  node_type: string
  service?: string
  properties: Record<string, any>
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  rel_type: string
  label: string
  mechanism: string
  properties: Record<string, any>
}

export interface PathEvidence {
  mechanism: string
  hops: number
  nodes: Array<{ id: string; label: string; node_type: string; service?: string }>
  edges: Array<{ id: string; source: string; target: string; rel_type: string; label: string; mechanism: string }>
  summary: string
}

export interface ExecutionResult {
  records: Record<string, any>[]
  total_records: number
  nodes: GraphNode[]
  edges: GraphEdge[]
  paths: PathEvidence[]
  display_truncated: boolean
  total_nodes_before_truncation: number
  total_edges_before_truncation: number
  elapsed_ms: number
  complete: boolean
  error?: string
}

export interface StageTrace {
  stage: string
  status: 'ok' | 'warning' | 'failed' | 'skipped'
  latency_ms: number
  summary: string
  details?: Record<string, any>
}

export interface CypherCandidate {
  query: string
  params: Record<string, any>
  schema_hash: string
  skill_version: string
  model_used: string
  generation_mode: string
  reasoning?: string
  subquery_id?: string
  mechanism?: string
}

export interface QueryTrace {
  trace_id: string
  request_id: string
  timestamp: string
  schema_hash: string
  graph_version: string
  schema_source: string
  skill_versions: Record<string, string>
  kb_version: string
  model_provider: string
  model_version: string
  execution_mode: string
  code_commit: string
  total_latency_ms: number
  retrieved_examples: Array<{ id: string; category: string; question: string; score?: number; rerank_mode?: string }>
  stages: StageTrace[]
  repairs: Array<{ round: number; trigger_reason: string; validation_passed: boolean; error?: string }>
  semantic_check?: { passed: boolean; issues: string[]; diagnosis?: string; suggested_fix?: string }
}

export interface QueryResponse {
  request_id: string
  trace_id: string
  question: string
  status: QueryStatus
  level: QueryLevel
  plan?: {
    intent: string
    category: string
    level: QueryLevel
    direction: string
    max_hops: number
    mechanisms: string[]
    expected_shape: string
    requires_decomposition: boolean
    selected_skills: string[]
    retrieval_mode: string
    entities: ResolvedEntity[]
    subqueries: Array<{ subquery_id: string; mechanism: string; description: string }>
  }
  cypher_candidates: CypherCandidate[]
  primary_cypher: string
  primary_params: Record<string, any>
  validation?: ValidationReport
  execution: ExecutionResult
  summary: string
  clarification_prompt?: string
  ambiguous_entities: ResolvedEntity[]
  trace: QueryTrace
  markdown_report: string
}

export interface KBEntry {
  id: string
  question: string
  cypher: string
  params: Record<string, any>
  category: string
  level: QueryLevel
  intent: string
  direction: string
  relation_pattern: string
  notes: string
  approved: boolean
  reviewer: string
  version: number
  verified: boolean
  verified_row_count: number
  verified_at?: string
  schema_hash: string
}

export interface BatchItem {
  index: number
  question: string
  level: QueryLevel
  status: string
  trace_id?: string
  primary_cypher: string
  record_count: number
  summary: string
  latency_ms: number
  error?: string
}

export interface BatchJob {
  batch_id: string
  status: 'pending' | 'running' | 'completed' | 'cancelled' | 'failed'
  total: number
  completed: number
  succeeded: number
  failed: number
  created_at: string
  updated_at: string
  items: BatchItem[]
}

export interface ModelConfig {
  provider: 'deepseek' | 'glm' | 'kimi'
  model: string
  api_key: string
  base_url: string
  max_hops: number
  clarify_on_ambiguity: boolean
  use_kb_rag: boolean
  use_llm_rerank: boolean
  use_decomposition: boolean
  use_semantic_check: boolean
}
