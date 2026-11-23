import type { BatchJob, KBEntry, ModelConfig, QueryLevel, QueryResponse, ValidationReport } from './types'

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const data = await res.json()
      msg = data.detail || JSON.stringify(data)
    } catch {
      msg = await res.text()
    }
    throw new Error(msg)
  }
  return res.json()
}

export const api = {
  health: () => req<any>('/api/health'),
  schema: () => req<any>('/api/schema'),
  refreshSchema: () => req<any>('/api/schema/refresh', { method: 'POST' }),
  skills: () => req<any[]>('/api/skills'),

  query: (
    question: string,
    level: QueryLevel,
    cfg: ModelConfig,
    scope?: string,
    selectedEntityIds?: Record<string, string>,
  ) =>
    req<QueryResponse>('/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        level,
        scope: scope || null,
        options: {
          max_hops: cfg.max_hops,
          clarify_on_ambiguity: cfg.clarify_on_ambiguity,
          use_kb_rag: cfg.use_kb_rag,
          use_llm_rerank: cfg.use_llm_rerank,
          use_decomposition: cfg.use_decomposition,
          use_semantic_check: cfg.use_semantic_check,
          selected_entity_ids: selectedEntityIds || {},
          provider: cfg.provider,
          model: cfg.model,
          api_key: cfg.api_key || null,
          base_url: cfg.base_url || null,
        },
      }),
    }),

  validateCypher: (cypher: string, params: Record<string, any> = {}, runExplain = true) =>
    req<ValidationReport>('/api/query/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cypher, params, run_explain: runExplain }),
    }),

  listTraces: (limit = 25) => req<any[]>(`/api/traces?limit=${limit}`),
  getTrace: (traceId: string) => req<QueryResponse>(`/api/traces/${traceId}`),
  markdownUrl: (traceId: string) => `/api/traces/${traceId}/markdown`,

  listKB: (params?: { keyword?: string; category?: string; level?: string }) => {
    const q = new URLSearchParams()
    if (params?.keyword) q.set('keyword', params.keyword)
    if (params?.category && params.category !== 'all') q.set('category', params.category)
    if (params?.level && params.level !== 'all') q.set('level', params.level)
    return req<KBEntry[]>(`/api/kb?${q.toString()}`)
  },
  createKB: (payload: Partial<KBEntry>) =>
    req<KBEntry>('/api/kb', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  updateKB: (id: string, payload: Partial<KBEntry>) =>
    req<KBEntry>(`/api/kb/${encodeURIComponent(id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  deleteKB: (id: string) => req<any>(`/api/kb/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  verifyKB: (id: string) => req<any>(`/api/kb/${encodeURIComponent(id)}/verify`, { method: 'POST' }),

  submitBatch: (questions: Array<{ question: string; level: string }>, cfg: ModelConfig) =>
    req<BatchJob>('/api/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        questions,
        options: {
          max_hops: cfg.max_hops,
          clarify_on_ambiguity: false,
          use_kb_rag: cfg.use_kb_rag,
          use_llm_rerank: cfg.use_llm_rerank,
          use_decomposition: cfg.use_decomposition,
          use_semantic_check: cfg.use_semantic_check,
          provider: cfg.provider,
          model: cfg.model,
          api_key: cfg.api_key || null,
          base_url: cfg.base_url || null,
        },
      }),
    }),
  uploadBatch: (file: File, cfg: ModelConfig) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('provider', cfg.provider)
    fd.append('model', cfg.model)
    if (cfg.api_key) fd.append('api_key', cfg.api_key)
    if (cfg.base_url) fd.append('base_url', cfg.base_url)
    return req<BatchJob>('/api/batch/upload', { method: 'POST', body: fd })
  },
  listBatches: () => req<any[]>('/api/batch'),
  getBatch: (id: string) => req<BatchJob>(`/api/batch/${id}`),
  cancelBatch: (id: string) => req<any>(`/api/batch/${id}/cancel`, { method: 'POST' }),

  evalSummary: () => req<any>('/api/eval/summary'),
}
