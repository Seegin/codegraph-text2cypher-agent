<template>
  <div class="kb-studio">
    <section class="card">
      <div class="section-header">
        <div>
          <div class="section-title">NL–Cypher 查询知识库 (KB30)</div>
          <div class="section-subtitle">人工精标并经实库非空执行验证的高质量范例库，支撑双阶段 TF-IDF + 大模型语义重排检索</div>
        </div>
        <button class="btn btn-primary" @click="openCreate">+ 新增知识库示例</button>
      </div>

      <!-- Category Pill Filter Bar -->
      <div class="cat-strip">
        <button class="cat-pill" :class="{ active: category === 'all' }" @click="selectCategory('all')">
          全部模式
        </button>
        <button
          v-for="c in categories"
          :key="c"
          class="cat-pill"
          :class="{ active: category === c }"
          @click="selectCategory(c)"
        >
          {{ c }}
        </button>
      </div>

      <!-- Search & Difficulty Bar -->
      <div class="filter-bar">
        <input
          v-model="keyword"
          class="input"
          style="flex:1; min-width:240px;"
          placeholder="搜索自然语言问题、Cypher 关键字或备注..."
          @input="loadKB"
        />
        <select v-model="level" class="select" @change="loadKB">
          <option value="all">所有复杂度</option>
          <option value="simple">简单 (simple)</option>
          <option value="medium">中等 (medium)</option>
          <option value="complex">复杂 (complex)</option>
        </select>
        <span class="badge badge-brand">当前展示 {{ entries.length }} 条</span>
      </div>
    </section>

    <!-- KB Cards List -->
    <div class="kb-grid">
      <article v-for="e in entries" :key="e.id" class="card kb-card">
        <div class="kb-card-top">
          <div style="display:flex; align-items:center; gap:7px; flex-wrap:wrap;">
            <span class="mono kb-id">{{ e.id }}</span>
            <span class="badge badge-brand">{{ e.category }}</span>
            <span class="badge">{{ e.level }} · {{ e.direction }}</span>
            <span class="badge" :class="e.verified ? 'badge-success' : 'badge-warning'">
              {{ e.verified ? `实库通过 (${e.verified_row_count} 行)` : '待实库核验' }}
            </span>
          </div>

          <div style="display:flex; align-items:center; gap:6px;">
            <span class="mono muted" style="font-size:11.5px;">v{{ e.version }}</span>
            <button class="btn btn-sm" :disabled="verifyingId === e.id" @click="verifyEntry(e.id)">
              {{ verifyingId === e.id ? '运行中…' : '实库试运行' }}
            </button>
            <button class="btn btn-sm" @click="openEdit(e)">编辑</button>
            <button class="btn btn-sm btn-danger" @click="removeEntry(e.id)">删除</button>
          </div>
        </div>

        <div class="kb-question">{{ e.question }}</div>

        <div class="cypher-terminal">
          <pre class="cypher-code" style="padding:10px 13px; font-size:12px;">{{ e.cypher }}</pre>
        </div>

        <div class="kb-meta">
          <span v-if="Object.keys(e.params || {}).length" class="mono">参数：{{ JSON.stringify(e.params) }}</span>
          <span v-if="e.notes" class="muted">{{ e.notes }}</span>
        </div>
      </article>
    </div>

    <div v-if="toast" class="toast-pop">{{ toast }}</div>

    <!-- Create / Edit Modal -->
    <div v-if="modalOpen" class="modal-backdrop" @click.self="modalOpen = false">
      <div class="modal-box">
        <div class="section-header" style="margin-bottom: 12px;">
          <div class="section-title">{{ editingId ? `编辑示例 ${editingId}` : '新增 NL–Cypher 知识库示例' }}</div>
          <button class="btn btn-sm" @click="modalOpen = false">关闭</button>
        </div>

        <div class="form-stack">
          <label>
            <span class="lbl">自然语言问题</span>
            <input v-model="form.question" class="input" style="width:100%;" />
          </label>
          <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px;">
            <label>
              <span class="lbl">查询模式分类</span>
              <select v-model="form.category" class="select" style="width:100%;">
                <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
              </select>
            </label>
            <label>
              <span class="lbl">复杂度</span>
              <select v-model="form.level" class="select" style="width:100%;">
                <option value="simple">simple</option>
                <option value="medium">medium</option>
                <option value="complex">complex</option>
              </select>
            </label>
            <label>
              <span class="lbl">遍历方向</span>
              <select v-model="form.direction" class="select" style="width:100%;">
                <option value="none">none</option>
                <option value="forward">forward</option>
                <option value="reverse">reverse</option>
                <option value="both">both</option>
              </select>
            </label>
          </div>
          <label>
            <span class="lbl">只读 Cypher 语句</span>
            <textarea v-model="form.cypher" class="textarea mono" rows="5" style="width:100%; font-size:12px;"></textarea>
          </label>
          <label>
            <span class="lbl">绑定参数 JSON</span>
            <input v-model="form.paramsText" class="input mono" style="width:100%; font-size:12px;" />
          </label>
          <label>
            <span class="lbl">审核与设计备注</span>
            <input v-model="form.notes" class="input" style="width:100%;" />
          </label>
        </div>

        <div v-if="formError" style="color:var(--danger); font-size:12.5px; margin-top:10px;">{{ formError }}</div>
        <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:16px;">
          <button class="btn" @click="modalOpen = false">取消</button>
          <button class="btn btn-primary" @click="saveForm">保存示例</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import type { KBEntry, QueryLevel } from '../types'

const categories = [
  '单节点查询',
  '属性过滤',
  '入口API发现',
  '下游服务发现',
  '反向影响分析',
  'MQ发布者与完整链路',
  'REST+MQ综合依赖',
]

const entries = ref<KBEntry[]>([])
const keyword = ref('')
const category = ref('all')
const level = ref('all')
const verifyingId = ref('')
const toast = ref('')

const modalOpen = ref(false)
const editingId = ref<string | null>(null)
const formError = ref('')
const form = reactive({
  question: '',
  category: '单节点查询',
  level: 'simple' as QueryLevel,
  direction: 'none',
  cypher: '',
  paramsText: '{}',
  notes: '',
})

function showToast(msg: string) {
  toast.value = msg
  window.setTimeout(() => {
    if (toast.value === msg) toast.value = ''
  }, 3200)
}

function selectCategory(c: string) {
  category.value = c
  loadKB()
}

async function loadKB() {
  entries.value = await api.listKB({
    keyword: keyword.value,
    category: category.value,
    level: level.value,
  })
}

function openCreate() {
  editingId.value = null
  formError.value = ''
  Object.assign(form, {
    question: '',
    category: '单节点查询',
    level: 'simple',
    direction: 'none',
    cypher: 'MATCH (s:`微服务`) RETURN s.nodeId, s.`服务名称`, s',
    paramsText: '{}',
    notes: '',
  })
  modalOpen.value = true
}

function openEdit(e: KBEntry) {
  editingId.value = e.id
  formError.value = ''
  Object.assign(form, {
    question: e.question,
    category: e.category,
    level: e.level,
    direction: e.direction,
    cypher: e.cypher,
    paramsText: JSON.stringify(e.params || {}),
    notes: e.notes,
  })
  modalOpen.value = true
}

async function saveForm() {
  formError.value = ''
  let parsedParams: Record<string, any> = {}
  try {
    parsedParams = JSON.parse(form.paramsText || '{}')
  } catch {
    formError.value = '参数 JSON 格式不合法'
    return
  }
  try {
    const payload = {
      question: form.question,
      category: form.category,
      level: form.level,
      direction: form.direction,
      cypher: form.cypher,
      params: parsedParams,
      notes: form.notes,
    }
    if (editingId.value) {
      await api.updateKB(editingId.value, payload)
      showToast(`已更新 ${editingId.value}`)
    } else {
      await api.createKB(payload)
      showToast('已新增知识库示例')
    }
    modalOpen.value = false
    await loadKB()
  } catch (err: any) {
    formError.value = err.message || String(err)
  }
}

async function verifyEntry(id: string) {
  verifyingId.value = id
  try {
    const res = await api.verifyKB(id)
    if (res.verified) {
      showToast(`${id} 实库验证通过：返回 ${res.row_count} 行 (${res.elapsed_ms} ms)`)
    } else {
      showToast(`${id} 验证未通过：${res.error || res.validation?.violation_category || '空结果'}`)
    }
    await loadKB()
  } finally {
    verifyingId.value = ''
  }
}

async function removeEntry(id: string) {
  if (!window.confirm(`确认删除知识库示例 ${id}？`)) return
  await api.deleteKB(id)
  showToast(`已删除 ${id}`)
  await loadKB()
}

onMounted(loadKB)
</script>

<style scoped>
.kb-studio {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.cat-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-bottom: 12px;
}
.cat-pill {
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid var(--border-subtle);
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font-size: 12.5px;
  font-weight: 550;
  cursor: pointer;
  transition: all 0.14s;
}
.cat-pill:hover {
  color: var(--text-primary);
  border-color: var(--border-default);
}
.cat-pill.active {
  background: var(--brand);
  border-color: var(--brand);
  color: #ffffff;
}
.filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.kb-grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.kb-card {
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.kb-card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.kb-id {
  font-size: 13px;
  font-weight: 700;
  color: var(--brand);
}
.kb-question {
  font-size: 14.5px;
  font-weight: 650;
  color: var(--text-primary);
}
.kb-meta {
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}
.form-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.lbl {
  display: block;
  font-size: 12px;
  font-weight: 550;
  color: var(--text-secondary);
  margin-bottom: 4px;
}
.toast-pop {
  position: fixed;
  right: 28px;
  bottom: 28px;
  background: var(--bg-code);
  color: #f8fafc;
  padding: 11px 18px;
  border-radius: 10px;
  font-size: 13px;
  box-shadow: var(--shadow-lg);
  z-index: 90;
}
</style>
