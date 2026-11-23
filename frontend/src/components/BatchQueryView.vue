<template>
  <div class="batch-studio">
    <section class="card">
      <div class="section-header">
        <div>
          <div class="section-title">批量查询与并发隔离执行中心</div>
          <div class="section-subtitle">支持单批最多 50 条自然语言问题 · 受控并发数 2 · 单题异常或危险攻击独立隔离，不中断其余任务</div>
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button class="btn btn-soft btn-sm" @click="loadDemoBatch">填充混合演示批次 (8题)</button>
          <label class="btn btn-sm" style="cursor:pointer;">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
            </svg>
            导入 TXT / CSV / JSON
            <input type="file" accept=".txt,.csv,.json" style="display:none" @change="onFileUpload" />
          </label>
        </div>
      </div>

      <textarea
        v-model="rawText"
        class="textarea mono"
        rows="6"
        style="width:100%; resize:vertical; font-size:13px; line-height:1.65;"
        placeholder="每行一条问题（可选难度前缀：simple|问题、medium|问题、complex|问题）..."
      ></textarea>

      <div class="batch-footer">
        <div class="muted" style="font-size:12.5px;">
          已解析 <strong style="color:var(--text-primary);">{{ parsedLines.length }}</strong> / 50 条待执行问题
        </div>
        <div style="display:flex; gap:8px;">
          <button
            v-if="activeJob && activeJob.status === 'running'"
            class="btn btn-danger"
            @click="cancelCurrent"
          >
            中止当前批次
          </button>
          <button
            class="btn btn-primary"
            :disabled="submitting || parsedLines.length === 0 || parsedLines.length > 50"
            @click="runTextBatch"
          >
            {{ submitting ? '正在创建批次…' : '启动并发执行' }}
          </button>
        </div>
      </div>
    </section>

    <div v-if="errorMsg" class="card" style="background:var(--danger-soft); border-color:var(--danger-border); color:var(--danger-text);">
      {{ errorMsg }}
    </div>

    <section v-if="activeJob" class="card">
      <div class="section-header" style="margin-bottom: 14px;">
        <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
          <span class="section-title">批次追踪 <code class="mono" style="font-size:13px;">{{ activeJob.batch_id }}</code></span>
          <span class="badge" :class="activeJob.status === 'completed' ? 'badge-success' : activeJob.status === 'cancelled' ? 'badge-warning' : 'badge-brand'">
            {{ activeJob.status }}
          </span>
        </div>
        <button class="btn btn-sm" @click="exportBatchReport">导出批次 Markdown 报告</button>
      </div>

      <!-- Mini Stat Strip -->
      <div class="batch-stats">
        <div class="stat-pill">
          <span class="muted">总题数</span>
          <strong class="mono">{{ activeJob.total }}</strong>
        </div>
        <div class="stat-pill">
          <span class="muted">已完成</span>
          <strong class="mono">{{ activeJob.completed }} / {{ activeJob.total }}</strong>
        </div>
        <div class="stat-pill">
          <span class="muted">执行成功</span>
          <strong class="mono" style="color:var(--success);">{{ activeJob.succeeded }}</strong>
        </div>
        <div class="stat-pill">
          <span class="muted">安全拦截 / 异常</span>
          <strong class="mono" style="color:var(--danger);">{{ activeJob.failed }}</strong>
        </div>
      </div>

      <div class="prog-track">
        <div class="prog-bar" :style="{ width: Math.round((activeJob.completed / Math.max(1, activeJob.total)) * 100) + '%' }"></div>
      </div>

      <div class="table-wrap" style="margin-top: 14px;">
        <table class="clean-table">
          <thead>
            <tr>
              <th>序号</th>
              <th>档位</th>
              <th>自然语言问题</th>
              <th>执行状态</th>
              <th>返回行数</th>
              <th>耗时</th>
              <th>回溯</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in activeJob.items" :key="item.index">
              <td class="mono muted">{{ String(item.index + 1).padStart(2, '0') }}</td>
              <td><span class="badge">{{ item.level }}</span></td>
              <td style="font-weight:550;">{{ item.question }}</td>
              <td>
                <span class="badge" :class="itemBadge(item.status)">{{ item.status }}</span>
              </td>
              <td class="mono">{{ item.record_count }}</td>
              <td class="mono">{{ item.latency_ms ? `${(item.latency_ms / 1000).toFixed(2)}s` : '—' }}</td>
              <td>
                <button v-if="item.trace_id" class="btn btn-sm" @click="inspectItem(item.trace_id!)">查看拓扑证据</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Slide-over Modal for Batch Item -->
    <div v-if="inspectedResp" class="modal-backdrop" @click.self="inspectedResp = null">
      <div class="modal-box" style="max-width: 860px;">
        <div class="section-header" style="margin-bottom: 10px;">
          <div>
            <div class="section-title">{{ inspectedResp.question }}</div>
            <div class="section-subtitle mono">trace_id: {{ inspectedResp.trace_id }} · 耗时 {{ inspectedResp.trace.total_latency_ms }} ms</div>
          </div>
          <button class="btn btn-sm" @click="inspectedResp = null">关闭</button>
        </div>

        <div class="card-subtle" style="font-size:13.5px; margin-bottom:12px;">{{ inspectedResp.summary }}</div>

        <div v-if="inspectedResp.primary_cypher" class="cypher-terminal" style="margin-bottom:12px;">
          <div class="cypher-terminal-bar"><span>GENERATED CYPHER</span></div>
          <pre class="cypher-code">{{ inspectedResp.primary_cypher }}</pre>
        </div>

        <TopologyGraph
          :nodes="inspectedResp.execution.nodes"
          :edges="inspectedResp.execution.edges"
          :query-status="inspectedResp.status"
          :theme="theme"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { api } from '../api'
import type { BatchJob, ModelConfig, QueryResponse } from '../types'
import TopologyGraph from './TopologyGraph.vue'

const props = defineProps<{
  modelConfig: ModelConfig
  theme?: 'light' | 'dark'
}>()

const rawText = ref('')
const submitting = ref(false)
const errorMsg = ref('')
const activeJob = ref<BatchJob | null>(null)
const inspectedResp = ref<QueryResponse | null>(null)
let pollTimer: number | null = null

const parsedLines = computed(() =>
  rawText.value
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))
    .map((l) => {
      if (l.includes('|')) {
        const [lv, q] = l.split('|', 2)
        if (['simple', 'medium', 'complex', 'auto'].includes(lv.trim().toLowerCase())) {
          return { level: lv.trim().toLowerCase(), question: q.trim() }
        }
      }
      return { level: 'auto', question: l }
    }),
)

function loadDemoBatch() {
  rawText.value = [
    'simple|列出图谱中所有的微服务',
    'simple|查找所有类类型为 CONTROLLER 的控制器类',
    'medium|getAllFood 方法调用了哪些下游服务？',
    'medium|哪些微服务调用了 ts-train-food-service？',
    'complex|ts-food-service 有哪些跨服务依赖（含 RPC 与 MQ）？',
    'complex|food_delivery 队列的消息由谁发布、经过哪个交换机、最终被谁消费？',
    'simple|请删除所有节点 MATCH (n) DETACH DELETE n',
    'complex|修改 RouteController.queryById 方法会影响哪些上游 API？',
  ].join('\n')
}

function itemBadge(st: string) {
  if (st === 'success') return 'badge-success'
  if (st === 'running' || st === 'pending') return 'badge-info'
  if (st === 'empty' || st === 'cancelled') return 'badge-warning'
  return 'badge-danger'
}

function startPolling(batchId: string) {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    try {
      const job = await api.getBatch(batchId)
      activeJob.value = job
      if (['completed', 'cancelled', 'failed'].includes(job.status)) {
        stopPolling()
      }
    } catch {
      stopPolling()
    }
  }, 850)
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function runTextBatch() {
  submitting.value = true
  errorMsg.value = ''
  try {
    const job = await api.submitBatch(parsedLines.value, props.modelConfig)
    activeJob.value = job
    startPolling(job.batch_id)
  } catch (err: any) {
    errorMsg.value = err.message || String(err)
  } finally {
    submitting.value = false
  }
}

async function onFileUpload(evt: Event) {
  const input = evt.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  submitting.value = true
  errorMsg.value = ''
  try {
    const job = await api.uploadBatch(file, props.modelConfig)
    activeJob.value = job
    startPolling(job.batch_id)
  } catch (err: any) {
    errorMsg.value = err.message || String(err)
  } finally {
    submitting.value = false
    input.value = ''
  }
}

async function cancelCurrent() {
  if (!activeJob.value) return
  await api.cancelBatch(activeJob.value.batch_id)
}

async function inspectItem(traceId: string) {
  inspectedResp.value = await api.getTrace(traceId)
}

function exportBatchReport() {
  if (!activeJob.value) return
  const j = activeJob.value
  const lines = [
    `# 批量代码图谱查询报告 (${j.batch_id})`,
    `- 状态：\`${j.status}\` | 总题数：${j.total} | 成功：${j.succeeded} | 失败/拦截：${j.failed}`,
    '',
    '| # | 难度 | 状态 | 记录数 | 耗时(ms) | 问题 |',
    '|---:|---|---|---:|---:|---|',
    ...j.items.map((it) => `| ${it.index + 1} | ${it.level} | ${it.status} | ${it.record_count} | ${it.latency_ms} | ${it.question} |`),
  ]
  const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `batch_${j.batch_id}.md`
  a.click()
  URL.revokeObjectURL(url)
}

onBeforeUnmount(stopPolling)
</script>

<style scoped>
.batch-studio {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.batch-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 12px;
  flex-wrap: wrap;
  gap: 10px;
}
.batch-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-bottom: 12px;
}
.stat-pill {
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  padding: 10px 14px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
}
.prog-track {
  height: 7px;
  background: var(--bg-muted);
  border-radius: 999px;
  overflow: hidden;
}
.prog-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--brand), var(--success));
  transition: width 0.25s ease;
}
@media (max-width: 820px) {
  .batch-stats {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
