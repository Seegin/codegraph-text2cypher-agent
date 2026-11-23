<template>
  <div class="query-studio">
    <!-- Hero Natural Language Command Composer -->
    <section class="composer-card card">
      <div class="composer-top">
        <div class="segmented" role="tablist">
          <button
            v-for="t in tiers"
            :key="t.value"
            class="seg-btn"
            :class="{ active: level === t.value }"
            @click="level = t.value"
          >
            {{ t.label }}
          </button>
        </div>

        <div class="scope-box">
          <span class="scope-lbl">限定服务域</span>
          <select v-model="scope" class="select btn-sm" style="min-width: 190px;">
            <option value="">全局图谱（41 个微服务自动识别）</option>
            <option v-for="s in microservices" :key="s" :value="s">{{ s }}</option>
          </select>
        </div>
      </div>

      <!-- Main Search Box -->
      <div class="composer-box" :class="{ focused: inputFocused }">
        <div class="composer-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3L12 3Z" />
          </svg>
        </div>
        <input
          v-model="question"
          class="composer-input"
          placeholder="用自然语言提问，例如：ts-food-service 有哪些跨服务依赖？或 修改某方法会影响哪些上游 API？"
          @focus="inputFocused = true"
          @blur="inputFocused = false"
          @keyup.enter="submitQuery()"
        />
        <button v-if="question && !loading" class="clear-btn" title="清空输入" @click="question = ''">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
        <button class="btn btn-primary btn-lg run-cta" :disabled="loading || !question.trim()" @click="submitQuery()">
          <svg v-if="loading" class="spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4">
            <path d="M21 12a9 9 0 1 1-6.219-8.56" />
          </svg>
          <span>{{ loading ? '正在分析图谱…' : '生成并查询' }}</span>
          <svg v-if="!loading" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
            <path d="M5 12h14M12 5l7 7-7 7" />
          </svg>
        </button>
      </div>

      <!-- Quick Scenario Chips -->
      <div class="scenario-strip">
        <span class="scenario-title">典型场景一键体验</span>
        <div class="scenario-chips">
          <button
            v-for="s in currentSamples"
            :key="s.q"
            class="scenario-chip"
            @click="pickSample(s)"
          >
            <i class="chip-dot" :style="{ background: s.color || '#6366f1' }"></i>
            <span v-if="s.tag" class="chip-tag">{{ s.tag }}</span>
            <span class="chip-text">{{ s.q }}</span>
          </button>
        </div>
      </div>
    </section>

    <!-- Error Notice -->
    <div v-if="errorMsg" class="card alert-danger">
      <strong>请求执行失败：</strong>{{ errorMsg }}
    </div>

    <!-- Homonym Entity Disambiguation Card -->
    <section v-if="result && result.status === 'needs_clarification'" class="card clarify-panel">
      <div class="section-header" style="margin-bottom: 8px;">
        <div>
          <div class="section-title">
            <span class="badge badge-warning">需要消歧确认</span>
            <span>检测到同名代码实体，请选择目标定义</span>
          </div>
          <div class="section-subtitle">{{ result.clarification_prompt }}</div>
        </div>
      </div>

      <div v-for="amb in result.ambiguous_entities" :key="amb.mention" class="amb-section">
        <div class="amb-heading">
          代码符号 <code class="mono sym-pill">{{ amb.mention }}</code> 在实库中命中 <strong>{{ amb.candidates.length }}</strong> 处定义：
        </div>
        <div class="candidate-grid">
          <div
            v-for="c in amb.candidates"
            :key="c.node_id"
            class="candidate-card"
            :class="{ active: selectedIds[amb.mention] === c.node_id }"
            @click="chooseCandidate(amb.mention, c.node_id)"
          >
            <div class="cand-top">
              <span class="cand-fqn">{{ c.qualified_name || c.name }}</span>
              <div style="display:flex; gap:4px;">
                <span v-if="c.is_entry" class="badge badge-success">入口方法</span>
                <span v-if="c.is_abstract" class="badge badge-warning">接口抽象</span>
              </div>
            </div>
            <div class="cand-tags">
              <span class="badge badge-brand">{{ (c.service_contexts || [])[0] || c.module_id }}</span>
              <span v-if="c.extra?.class_type" class="badge">{{ c.extra.class_type }}</span>
            </div>
            <div class="cand-path mono">{{ c.source_file || '未知源码文件' }}<span v-if="c.source_line">:{{ c.source_line }}</span></div>
            <div class="cand-cta">绑定此实体继续查询 →</div>
          </div>
        </div>
      </div>
    </section>

    <!-- Main Result Bento -->
    <template v-if="result && result.status !== 'needs_clarification'">
      <!-- Executive Conclusion Card -->
      <section class="card conclusion-card" :class="{ 'conclusion-rejected': result.status === 'rejected' }">
        <div class="section-header" style="margin-bottom: 12px;">
          <div class="meta-badges">
            <span class="badge" :class="statusBadgeClass(result.status)">
              <i class="status-dot"></i>
              {{ statusLabel(result.status) }}
            </span>
            <span class="badge badge-brand">{{ levelLabel(result.level) }}</span>
            <span v-if="result.plan?.requires_decomposition" class="badge badge-info">
              T3 多子查询并行合并 × {{ result.cypher_candidates.length }}
            </span>
            <span class="badge mono">{{ result.trace.execution_mode }}</span>
            <span class="badge mono">{{ (result.trace.total_latency_ms / 1000).toFixed(2) }} s</span>
          </div>

          <button class="btn btn-sm" @click="downloadMarkdown">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
            </svg>
            导出 Markdown 报告
          </button>
        </div>

        <div class="summary-prose" v-html="renderMarkdownLite(result.summary)"></div>
      </section>

      <!-- Split Workspace: Left Blueprint Topology + Right Engineering Inspector -->
      <div class="bento-split">
        <div class="card" style="padding: 18px 20px;">
          <div class="section-header" style="margin-bottom: 12px;">
            <div>
              <div class="section-title">代码调用与依赖拓扑图</div>
              <div class="section-subtitle">支持滚轮缩放、拖拽排布；点击任意节点或连线可查看实库源码行号与属性证据</div>
            </div>
            <div style="display:flex; gap:6px;">
              <span class="badge">{{ result.execution.nodes.length }} 节点</span>
              <span class="badge">{{ result.execution.edges.length }} 关系边</span>
              <span class="badge badge-brand">{{ result.execution.total_records }} 条记录</span>
            </div>
          </div>

          <TopologyGraph
            :nodes="result.execution.nodes"
            :edges="result.execution.edges"
            :truncated="result.execution.display_truncated"
            :total-nodes="result.execution.total_nodes_before_truncation"
            :total-edges="result.execution.total_edges_before_truncation"
            :query-status="result.status"
            :theme="theme"
          />
        </div>

        <!-- Right Engineering Inspector -->
        <div class="card" style="padding: 18px 20px;">
          <div class="segmented inspector-tabs">
            <button class="seg-btn" :class="{ active: rightTab === 'cypher' }" @click="rightTab = 'cypher'">Cypher 与安全</button>
            <button class="seg-btn" :class="{ active: rightTab === 'trace' }" @click="rightTab = 'trace'">六阶段追踪</button>
            <button class="seg-btn" :class="{ active: rightTab === 'table' }" @click="rightTab = 'table'">数据明细 ({{ result.execution.total_records }})</button>
            <button class="seg-btn" :class="{ active: rightTab === 'paths' }" @click="rightTab = 'paths'">链路 ({{ result.execution.paths.length }})</button>
          </div>

          <!-- Tab 1: Syntax-Highlighted Cypher & 5-Layer Security Stepper -->
          <div v-if="rightTab === 'cypher'" class="inspector-scroll">
            <div v-if="result.cypher_candidates.length" class="cypher-stack">
              <div v-for="(c, idx) in result.cypher_candidates" :key="idx" class="cypher-terminal">
                <div class="cypher-terminal-bar">
                  <span>
                    <strong style="color:#e2e8f0;">{{ c.subquery_id ? `${c.subquery_id} · ${c.mechanism}` : 'CYPHER QUERY' }}</strong>
                    <span style="margin-left:8px; opacity:0.75;">{{ c.model_used }}</span>
                  </span>
                  <button class="copy-btn" @click="copyText(c.query, idx)">
                    {{ copiedIdx === idx ? '已复制 ✓' : '复制语句' }}
                  </button>
                </div>
                <pre class="cypher-code" v-html="highlightCypher(c.query)"></pre>
                <div v-if="Object.keys(c.params || {}).length" class="cypher-params mono">
                  参数绑定：{{ JSON.stringify(c.params) }}
                </div>
              </div>
            </div>

            <!-- 5-Layer Security Guardrail Stepper -->
            <div v-if="result.validation" style="margin-top: 16px;">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                <span style="font-size:13px; font-weight:650;">五层只读安全与 EXPLAIN 计划防线</span>
                <span class="badge" :class="result.validation.passed ? 'badge-success' : 'badge-danger'">
                  {{ result.validation.passed ? '全层放行' : result.validation.violation_category || '已阻断' }}
                </span>
              </div>
              <div class="stepper-list">
                <div v-for="l in result.validation.layers" :key="l.layer" class="step-row">
                  <div class="step-badge" :class="l.passed ? 'step-ok' : 'step-fail'">
                    <svg v-if="l.passed" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M20 6 9 17l-5-5"/></svg>
                    <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M18 6 6 18M6 6l12 12"/></svg>
                  </div>
                  <div class="step-content">
                    <div class="mono step-name">{{ l.layer }}</div>
                    <div class="step-desc">{{ l.message }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- Tab 2: 6-Stage Waterfall Timeline & KB30 RAG Citations -->
          <div v-if="rightTab === 'trace'" class="inspector-scroll">
            <div class="card-subtle mono" style="font-size:11.5px; margin-bottom:12px; line-height:1.6;">
              <div><strong>trace_id:</strong> {{ result.trace.trace_id }}</div>
              <div><strong>schema_hash:</strong> {{ result.trace.schema_hash }} ({{ result.trace.graph_version }})</div>
              <div><strong>skills:</strong> {{ Object.entries(result.trace.skill_versions || {}).map(([k,v]) => `${k}@${v}`).join(', ') }}</div>
            </div>

            <div class="waterfall-list">
              <div v-for="s in result.trace.stages" :key="s.stage" class="wf-item">
                <div class="wf-head">
                  <span class="mono wf-stage">{{ s.stage }}</span>
                  <span class="mono wf-ms">{{ s.latency_ms }} ms</span>
                </div>
                <div class="wf-bar-bg">
                  <div
                    class="wf-bar-fill"
                    :class="s.status === 'failed' ? 'bar-danger' : s.status === 'warning' ? 'bar-warn' : ''"
                    :style="{ width: Math.max(4, Math.min(100, (s.latency_ms / Math.max(1, result.trace.total_latency_ms)) * 100)) + '%' }"
                  ></div>
                </div>
                <div class="wf-desc">{{ s.summary }}</div>
              </div>
            </div>

            <div v-if="result.trace.retrieved_examples.length" style="margin-top: 16px;">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <span style="font-size:13px; font-weight:650;">RAG 命中的知识库相似示例</span>
                <span class="badge badge-info">{{ result.plan?.retrieval_mode }}</span>
              </div>
              <div class="rag-list">
                <div v-for="ex in result.trace.retrieved_examples" :key="ex.id" class="rag-card">
                  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:3px;">
                    <span class="badge badge-brand">{{ ex.id }} · {{ ex.category }}</span>
                    <span class="mono muted" style="font-size:11px;">tfidf={{ ex.score }}</span>
                  </div>
                  <div style="font-size:12.5px;">{{ ex.question }}</div>
                </div>
              </div>
            </div>
          </div>

          <!-- Tab 3: Clean Data Table -->
          <div v-if="rightTab === 'table'" class="inspector-scroll">
            <div v-if="flatColumns.length" class="table-wrap">
              <table class="clean-table">
                <thead>
                  <tr>
                    <th v-for="col in flatColumns" :key="col">{{ col }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, rIdx) in result.execution.records.slice(0, 100)" :key="rIdx">
                    <td v-for="col in flatColumns" :key="col" class="mono" style="font-size:11.5px; word-break:break-word;">
                      {{ formatCell(row[col]) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="muted" style="font-size:13px; padding:16px 0;">当前无记录行输出。</div>
          </div>

          <!-- Tab 4: Path Chains -->
          <div v-if="rightTab === 'paths'" class="inspector-scroll">
            <div v-if="result.execution.paths.length" class="rag-list">
              <div v-for="(p, idx) in result.execution.paths" :key="idx" class="rag-card">
                <span class="badge badge-info">{{ p.mechanism }} · {{ p.hops }} 跳</span>
                <div class="mono" style="font-size:12px; margin-top:6px; line-height:1.5;">{{ p.summary }}</div>
              </div>
            </div>
            <div v-else class="muted" style="font-size:13px; padding:16px 0;">当前查询为点集/边集查询，未显式返回多跳路径对象。</div>
          </div>
        </div>
      </div>
    </template>

    <!-- Recent History Strip -->
    <section v-if="history.length" class="card" style="padding: 16px 20px;">
      <div class="section-header" style="margin-bottom: 10px;">
        <div class="section-title" style="font-size:14px;">历史查询回放记录</div>
        <button class="btn btn-sm" @click="loadHistory">刷新</button>
      </div>
      <div class="history-grid">
        <div v-for="h in history.slice(0, 6)" :key="h.trace_id" class="hist-card" @click="openTrace(h.trace_id)">
          <div style="display:flex; justify-content:space-between; align-items:center; gap:6px; margin-bottom:4px;">
            <span class="badge" :class="statusBadgeClass(h.status)">{{ statusLabel(h.status) }}</span>
            <span class="mono muted" style="font-size:11px;">{{ (h.latency_ms / 1000).toFixed(2) }}s · {{ h.total_records }}条</span>
          </div>
          <div class="hist-question">{{ h.question }}</div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import type { ModelConfig, QueryLevel, QueryResponse } from '../types'
import TopologyGraph from './TopologyGraph.vue'

const props = defineProps<{
  modelConfig: ModelConfig
  microservices: string[]
  theme?: 'light' | 'dark'
}>()

const tiers: Array<{ value: QueryLevel; label: string }> = [
  { value: 'auto', label: '智能识别' },
  { value: 'simple', label: '简单 · 单跳事实' },
  { value: 'medium', label: '中等 · 多跳遍历' },
  { value: 'complex', label: '复杂 · 异构联合' },
]

const SAMPLES: Record<QueryLevel, Array<{ q: string; level: QueryLevel; tag?: string; color?: string }>> = {
  auto: [
    { q: 'ts-food-service 有哪些跨服务依赖（含 RPC 与 MQ）？', level: 'complex', tag: 'RPC+MQ分解', color: '#10b981' },
    { q: 'getAllFood 方法调用了哪些下游服务？', level: 'medium', tag: '同名实体消歧', color: '#6366f1' },
    { q: '修改 RouteController.queryById 方法会影响哪些上游 API？', level: 'complex', tag: '反向变更影响', color: '#06b6d4' },
    { q: 'food_delivery 队列的消息由谁发布、经过哪个交换机、最终被谁消费？', level: 'complex', tag: 'MQ三阶段链路', color: '#f59e0b' },
    { q: '帮我执行 MATCH (n) DETACH DELETE n 删除所有节点', level: 'simple', tag: '安全防线演练', color: '#e11d48' },
  ],
  simple: [
    { q: '列出图谱中所有的微服务', level: 'simple', color: '#10b981' },
    { q: '查找所有类类型为 CONTROLLER 的控制器类', level: 'simple', color: '#3b82f6' },
    { q: '查询 FoodController 类暴露的上游入口 API', level: 'simple', color: '#06b6d4' },
    { q: '统计 ts-gateway-service 微服务下归属的配置节点数量', level: 'simple', color: '#64748b' },
  ],
  medium: [
    { q: 'getAllFood 方法调用了哪些下游服务？', level: 'medium', color: '#6366f1' },
    { q: '哪些微服务调用了 ts-train-food-service？', level: 'medium', color: '#06b6d4' },
    { q: 'ts-cancel-service 中的 cancelTicket 方法调用了哪些下游服务？', level: 'medium', color: '#10b981' },
    { q: '统计暴露上游入口 API 数量排名前 10 的微服务', level: 'medium', color: '#f59e0b' },
  ],
  complex: [
    { q: 'ts-food-service 有哪些跨服务依赖（含 RPC 与 MQ）？', level: 'complex', color: '#10b981' },
    { q: 'ts-preserve-service 通过同步 REST 和异步 MQ 分别依赖了哪些下游微服务？', level: 'complex', color: '#6366f1' },
    { q: '修改 RouteController.queryById 方法会影响哪些上游 API？', level: 'complex', color: '#06b6d4' },
    { q: 'ts-food-service 与 ts-delivery-service 之间有哪些 MQ 消息通信？', level: 'complex', color: '#f59e0b' },
    { q: '追踪 ts-travel-service 经由中间服务间接调用的两跳下游微服务链路', level: 'complex', color: '#8b5cf6' },
  ],
}

const level = ref<QueryLevel>('auto')
const question = ref('ts-food-service 有哪些跨服务依赖（含 RPC 与 MQ）？')
const scope = ref('')
const inputFocused = ref(false)
const loading = ref(false)
const errorMsg = ref('')
const result = ref<QueryResponse | null>(null)
const selectedIds = ref<Record<string, string>>({})
const rightTab = ref<'cypher' | 'trace' | 'table' | 'paths'>('cypher')
const history = ref<any[]>([])
const copiedIdx = ref<number | null>(null)

const currentSamples = computed(() => SAMPLES[level.value] || SAMPLES.auto)

const flatColumns = computed(() => {
  const recs = result.value?.execution.records || []
  if (!recs.length) return []
  const keys = Object.keys(recs[0]).filter((k) => {
    const v = recs[0][k]
    return !(v && typeof v === 'object' && ['node', 'relationship', 'path'].includes(v._type)) && !k.startsWith('_')
  })
  return keys.length ? keys : Object.keys(recs[0]).slice(0, 6)
})

function formatCell(val: any): string {
  if (val === null || val === undefined) return '—'
  if (typeof val === 'object') {
    if (val._type === 'node') return `${val.caption} (${val.nodeId})`
    if (Array.isArray(val)) return val.join(', ')
    return JSON.stringify(val)
  }
  return String(val)
}

function statusBadgeClass(st: string) {
  if (st === 'success') return 'badge-success'
  if (st === 'needs_clarification' || st === 'empty') return 'badge-warning'
  return 'badge-danger'
}

function statusLabel(st: string) {
  return (
    {
      success: '查询成功',
      needs_clarification: '待确认实体',
      rejected: '安全防线拦截',
      empty: '空结果集',
      timeout: '查询超时',
      error: '执行异常',
    }[st] || st
  )
}

function levelLabel(lv: string) {
  return { simple: '简单查询', medium: '中等查询', complex: '复杂联合查询', auto: '智能识别' }[lv] || lv
}

function highlightCypher(raw: string): string {
  const esc = (raw || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return esc
    .replace(/('[^']*')/g, '<span class="cy-str">$1</span>')
    .replace(/(`[^`]+`)/g, '<span class="cy-lbl">$1</span>')
    .replace(
      /\b(MATCH|OPTIONAL|WHERE|RETURN|DISTINCT|WITH|UNWIND|UNION|ORDER\s+BY|LIMIT|ASC|DESC|AND|OR|IN|AS|ALL|CASE|WHEN|THEN|ELSE|END|NOT)\b/g,
      '<span class="cy-kw">$1</span>',
    )
    .replace(/\b(count|collect|coalesce|nodes|relationships|size|labels|type)\b/g, '<span class="cy-fn">$1</span>')
}

function renderMarkdownLite(md: string): string {
  const esc = (md || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return esc
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="prose-code">$1</code>')
    .replace(/\n/g, '<br/>')
}

async function copyText(txt: string, idx: number) {
  try {
    await navigator.clipboard.writeText(txt)
    copiedIdx.value = idx
    setTimeout(() => {
      if (copiedIdx.value === idx) copiedIdx.value = null
    }, 1800)
  } catch {
    // ignore
  }
}

function pickSample(s: { q: string; level: QueryLevel }) {
  question.value = s.q
  level.value = s.level
  selectedIds.value = {}
  submitQuery()
}

async function submitQuery(keepSelections = false) {
  if (!question.value.trim()) return
  if (!keepSelections) selectedIds.value = {}
  loading.value = true
  errorMsg.value = ''
  try {
    const resp = await api.query(
      question.value.trim(),
      level.value,
      props.modelConfig,
      scope.value || undefined,
      selectedIds.value,
    )
    result.value = resp
    loadHistory()
  } catch (err: any) {
    errorMsg.value = err.message || String(err)
  } finally {
    loading.value = false
  }
}

async function chooseCandidate(mention: string, nodeId: string) {
  selectedIds.value = { ...selectedIds.value, [mention]: nodeId }
  await submitQuery(true)
}

function downloadMarkdown() {
  if (!result.value) return
  const blob = new Blob([result.value.markdown_report], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `report_${result.value.trace_id}.md`
  a.click()
  URL.revokeObjectURL(url)
}

async function loadHistory() {
  try {
    history.value = await api.listTraces(12)
  } catch {
    // ignore
  }
}

async function openTrace(traceId: string) {
  try {
    const resp = await api.getTrace(traceId)
    result.value = resp
    question.value = resp.question
  } catch (err: any) {
    errorMsg.value = err.message
  }
}

onMounted(loadHistory)
</script>

<style scoped>
.query-studio {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.composer-card {
  padding: 18px 22px;
}
.composer-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.scope-box {
  display: flex;
  align-items: center;
  gap: 8px;
}
.scope-lbl {
  font-size: 12px;
  font-weight: 550;
  color: var(--text-muted);
}
.composer-box {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--bg-subtle);
  border: 1.5px solid var(--border-default);
  border-radius: 13px;
  padding: 6px 8px 6px 14px;
  transition: all 0.16s ease;
}
.composer-box.focused {
  background: var(--bg-surface);
  border-color: var(--brand);
  box-shadow: var(--ring-brand);
}
.composer-icon {
  color: var(--brand);
  display: flex;
  align-items: center;
  flex-shrink: 0;
}
.composer-input {
  flex: 1;
  border: none;
  background: transparent;
  font-size: 14.5px;
  padding: 8px 4px;
  outline: none;
  color: var(--text-primary);
  min-width: 180px;
}
.clear-btn {
  border: none;
  background: transparent;
  color: var(--text-faint);
  padding: 5px;
  cursor: pointer;
  border-radius: 6px;
  display: flex;
}
.clear-btn:hover {
  color: var(--text-secondary);
  background: var(--bg-hover);
}
.run-cta {
  flex-shrink: 0;
  border-radius: 9px;
}
.scenario-strip {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-top: 13px;
}
.scenario-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  white-space: nowrap;
  padding-top: 5px;
}
.scenario-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.scenario-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border-radius: 999px;
  border: 1px solid var(--border-subtle);
  background: var(--bg-subtle);
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.14s ease;
}
.scenario-chip:hover {
  border-color: var(--brand-border);
  background: var(--brand-soft);
  color: var(--brand-text);
}
.chip-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.chip-tag {
  font-weight: 650;
  color: var(--text-primary);
}
.alert-danger {
  background: var(--danger-soft);
  border-color: var(--danger-border);
  color: var(--danger-text);
}
.clarify-panel {
  border-color: var(--warning-border);
}
.amb-section {
  margin-top: 12px;
}
.amb-heading {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 10px;
}
.sym-pill {
  background: var(--brand-soft);
  color: var(--brand-text);
  padding: 2px 7px;
  border-radius: 5px;
  font-weight: 600;
}
.candidate-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 12px;
}
.candidate-card {
  background: var(--bg-subtle);
  border: 1.5px solid var(--border-subtle);
  border-radius: 12px;
  padding: 14px 16px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: all 0.15s ease;
}
.candidate-card:hover,
.candidate-card.active {
  background: var(--bg-surface);
  border-color: var(--brand);
  box-shadow: var(--ring-brand);
  transform: translateY(-1px);
}
.cand-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
}
.cand-fqn {
  font-size: 13px;
  font-weight: 650;
  color: var(--text-primary);
  word-break: break-all;
}
.cand-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.cand-path {
  font-size: 11px;
  color: var(--text-muted);
  word-break: break-all;
}
.cand-cta {
  font-size: 12px;
  font-weight: 600;
  color: var(--brand);
  margin-top: auto;
  padding-top: 4px;
}
.conclusion-rejected {
  border-color: var(--danger-border);
}
.meta-badges {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}
.summary-prose {
  font-size: 14px;
  line-height: 1.78;
  color: var(--text-primary);
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  padding: 13px 16px;
}
:deep(.prose-code) {
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--brand-soft);
  color: var(--brand-text);
  border: 1px solid var(--brand-border);
  padding: 1px 6px;
  border-radius: 5px;
}
.inspector-tabs {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  margin-bottom: 14px;
}
.inspector-scroll {
  max-height: 490px;
  overflow-y: auto;
  padding-right: 2px;
}
.cypher-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.copy-btn {
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.06);
  color: #e2e8f0;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 5px;
  cursor: pointer;
}
.copy-btn:hover {
  background: rgba(255, 255, 255, 0.15);
}
.cypher-params {
  padding: 7px 14px;
  background: rgba(255, 255, 255, 0.03);
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  font-size: 11px;
  color: #94a3b8;
}
.stepper-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.step-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 9px 12px;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 9px;
}
.step-badge {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 1px;
}
.step-ok {
  background: var(--success-soft);
  color: var(--success);
  border: 1px solid var(--success-border);
}
.step-fail {
  background: var(--danger-soft);
  color: var(--danger);
  border: 1px solid var(--danger-border);
}
.step-name {
  font-size: 12px;
  font-weight: 650;
  color: var(--text-primary);
}
.step-desc {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 1px;
}
.waterfall-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.wf-item {
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 9px;
  padding: 9px 12px;
}
.wf-head {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 650;
  margin-bottom: 5px;
}
.wf-ms {
  color: var(--brand);
}
.wf-bar-bg {
  height: 5px;
  background: var(--bg-muted);
  border-radius: 999px;
  overflow: hidden;
  margin-bottom: 5px;
}
.wf-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--brand), #06b6d4);
  border-radius: 999px;
}
.bar-danger {
  background: var(--danger);
}
.bar-warn {
  background: var(--warning);
}
.wf-desc {
  font-size: 12px;
  color: var(--text-secondary);
}
.rag-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rag-card {
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 9px;
  padding: 10px 12px;
}
.history-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
}
.hist-card {
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  padding: 10px 12px;
  cursor: pointer;
  transition: all 0.14s ease;
}
.hist-card:hover {
  background: var(--bg-surface);
  border-color: var(--brand);
  box-shadow: var(--shadow-xs);
}
.hist-question {
  font-size: 12.5px;
  font-weight: 550;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
