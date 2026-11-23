<template>
  <div class="studio-layout">
    <!-- Left Architectural Navigation Rail -->
    <aside class="sidebar">
      <div class="sidebar-top">
        <!-- Brand Identity -->
        <div class="brand-box">
          <div class="brand-mark">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="6" cy="6" r="3" />
              <circle cx="18" cy="6" r="3" />
              <circle cx="12" cy="18" r="3" />
              <path d="M8.6 7.5L10.8 15.3" />
              <path d="M15.4 7.5L13.2 15.3" />
              <path d="M9 6h6" />
            </svg>
          </div>
          <div>
            <div class="brand-name">CodeGraph</div>
            <div class="brand-tagline">Text2Cypher 智能分析台</div>
          </div>
        </div>

        <!-- Navigation Menu -->
        <div class="nav-label">工作空间</div>
        <nav class="rail-nav">
          <button class="rail-item" :class="{ active: activeTab === 'single' }" @click="activeTab = 'single'">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="11" cy="11" r="8" />
              <path d="m21 21-4.3-4.3" />
            </svg>
            <span>智能图谱查询</span>
          </button>

          <button class="rail-item" :class="{ active: activeTab === 'batch' }" @click="activeTab = 'batch'">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
              <rect x="3" y="4" width="18" height="6" rx="1.5" />
              <rect x="3" y="14" width="18" height="6" rx="1.5" />
            </svg>
            <span>批量任务中心</span>
          </button>

          <button class="rail-item" :class="{ active: activeTab === 'kb' }" @click="activeTab = 'kb'">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
              <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20" />
            </svg>
            <span>查询知识库</span>
            <span class="rail-count">{{ health?.kb_count ?? 30 }}</span>
          </button>

          <button class="rail-item" :class="{ active: activeTab === 'eval' }" @click="activeTab = 'eval'">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              <path d="m9 12 2 2 4-4" />
            </svg>
            <span>评测与安全审计</span>
            <span class="rail-badge">A 档</span>
          </button>
        </nav>
      </div>

      <!-- Bottom Telemetry & Controls -->
      <div class="sidebar-bottom">
        <!-- Theme Toggle -->
        <div class="theme-switch" role="group" aria-label="界面主题">
          <button class="theme-btn" :class="{ active: theme === 'light' }" @click="setTheme('light')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
            </svg>
            浅色蓝图
          </button>
          <button class="theme-btn" :class="{ active: theme === 'dark' }" @click="setTheme('dark')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
            </svg>
            深色暗夜
          </button>
        </div>

        <!-- System Status Card -->
        <div class="telemetry-card">
          <div class="tele-row">
            <span class="tele-label">图数据库</span>
            <span class="badge" :class="health?.neo4j?.connected ? 'badge-success' : 'badge-danger'">
              <i class="status-dot"></i>
              {{ health?.neo4j?.connected ? 'Neo4j 在线' : '未连接' }}
            </span>
          </div>
          <div class="tele-row">
            <span class="tele-label">图谱快照</span>
            <span class="mono tele-val">{{ health?.graph_version || 'master-to-dd92845' }}</span>
          </div>
          <div class="tele-row">
            <span class="tele-label">Schema 指纹</span>
            <span class="mono tele-val">{{ health?.schema_hash || '22b9370a' }}</span>
          </div>
          <div class="tele-divider"></div>
          <div class="tele-model">
            <div>
              <div class="tele-label">当前推理模型</div>
              <div class="tele-model-name">{{ activeModelBadge }}</div>
            </div>
            <button class="btn btn-sm" @click="settingsOpen = true">配置</button>
          </div>
        </div>
      </div>
    </aside>

    <!-- Main Workspace -->
    <main class="workspace-area">
      <SingleQueryView
        v-show="activeTab === 'single'"
        :model-config="modelConfig"
        :microservices="microservices"
        :theme="theme"
      />
      <BatchQueryView
        v-show="activeTab === 'batch'"
        :model-config="modelConfig"
        :theme="theme"
      />
      <KBManagerView v-if="activeTab === 'kb'" />
      <EvalAndSchemaView v-if="activeTab === 'eval'" />
    </main>

    <!-- Approved LLM Provider Settings Modal -->
    <div v-if="settingsOpen" class="modal-backdrop" @click.self="settingsOpen = false">
      <div class="modal-box">
        <div class="section-header" style="margin-bottom: 10px;">
          <div>
            <div class="section-title">模型与执行策略设置</div>
            <div class="section-subtitle">严格遵循课程约束：仅允许使用 DeepSeek、GLM 或 Kimi 基础模型</div>
          </div>
          <button class="btn btn-sm" @click="settingsOpen = false">完成</button>
        </div>

        <div class="form-stack">
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
            <label>
              <span class="field-lbl">批准模型厂商</span>
              <select v-model="modelConfig.provider" class="select" style="width:100%;" @change="onProviderChange">
                <option value="deepseek">DeepSeek</option>
                <option value="glm">GLM (智谱清言)</option>
                <option value="kimi">Kimi (Moonshot)</option>
              </select>
            </label>
            <label>
              <span class="field-lbl">锁定模型型号</span>
              <select v-model="modelConfig.model" class="select" style="width:100%;">
                <option v-for="m in providerModels[modelConfig.provider]" :key="m" :value="m">{{ m }}</option>
              </select>
            </label>
          </div>

          <label>
            <span class="field-lbl">临时覆盖 API Key（留空则自动使用服务端已就绪密钥）</span>
            <input v-model="modelConfig.api_key" type="password" class="input mono" style="width:100%;" placeholder="默认已连接服务端密钥，无需重复填写" />
          </label>

          <label>
            <span class="field-lbl">自定义网关地址（可选）</span>
            <input v-model="modelConfig.base_url" class="input mono" style="width:100%;" :placeholder="defaultBaseUrls[modelConfig.provider]" />
          </label>

          <div class="card-subtle" style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            <label class="chk-row">
              <input type="checkbox" v-model="modelConfig.clarify_on_ambiguity" />
              <span>同名实体交互澄清</span>
            </label>
            <label class="chk-row">
              <input type="checkbox" v-model="modelConfig.use_kb_rag" />
              <span>KB30 示例检索增强</span>
            </label>
            <label class="chk-row">
              <input type="checkbox" v-model="modelConfig.use_decomposition" />
              <span>T3 复杂子查询分解</span>
            </label>
            <label class="chk-row">
              <input type="checkbox" v-model="modelConfig.use_semantic_check" />
              <span>结果语义一致性诊断</span>
            </label>
          </div>

          <label>
            <div style="display:flex; justify-content:space-between; font-size:12.5px; margin-bottom:4px;">
              <span class="secondary">调用链最大遍历深度</span>
              <strong class="mono">{{ modelConfig.max_hops }} 跳（硬上限 10 跳）</strong>
            </div>
            <input type="range" min="1" max="10" v-model.number="modelConfig.max_hops" style="width:100%; accent-color: var(--brand);" />
          </label>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from './api'
import type { ModelConfig } from './types'
import SingleQueryView from './components/SingleQueryView.vue'
import BatchQueryView from './components/BatchQueryView.vue'
import KBManagerView from './components/KBManagerView.vue'
import EvalAndSchemaView from './components/EvalAndSchemaView.vue'

const activeTab = ref<'single' | 'batch' | 'kb' | 'eval'>('single')
const settingsOpen = ref(false)
const health = ref<any>(null)
const microservices = ref<string[]>([])
const theme = ref<'light' | 'dark'>('light')

function setTheme(t: 'light' | 'dark') {
  theme.value = t
  document.documentElement.setAttribute('data-theme', t)
}

const providerModels: Record<'deepseek' | 'glm' | 'kimi', string[]> = {
  deepseek: ['DeepSeek-V4.1-Flash', 'DeepSeek-V4.1-Flash-1017', 'deepseek-chat', 'deepseek-reasoner'],
  glm: ['GLM-5.3', 'GLM-5.3-Flash', 'GLM-5.3-FlashX', 'glm-4-plus', 'glm-4-flash'],
  kimi: ['Kimi-K3.1-Pro-HighSpeed', 'Kimi-K3.1-Pro', 'kimi-k2-0711-preview', 'moonshot-v1-8k'],
}

const defaultBaseUrls: Record<'deepseek' | 'glm' | 'kimi', string[]> | any = {
  deepseek: 'https://llmapi.isrc.ac.cn/v1',
  glm: 'https://llmapi.isrc.ac.cn/v1',
  kimi: 'https://llmapi.isrc.ac.cn/v1',
}

const modelConfig = reactive<ModelConfig>({
  provider: 'deepseek',
  model: 'DeepSeek-V4.1-Flash',
  api_key: '',
  base_url: '',
  max_hops: 6,
  clarify_on_ambiguity: true,
  use_kb_rag: true,
  use_llm_rerank: true,
  use_decomposition: true,
  use_semantic_check: true,
})

const backendProviderConfigured = computed(() => {
  const list = health.value?.approved_providers || []
  const found = list.find((p: any) => p.provider === modelConfig.provider)
  return Boolean(found?.configured)
})

const activeModelBadge = computed(() => {
  if (modelConfig.api_key || backendProviderConfigured.value) {
    return `${modelConfig.model}`
  }
  return 'Hybrid Planner (离线)'
})

function onProviderChange() {
  modelConfig.model = providerModels[modelConfig.provider][0]
  modelConfig.base_url = ''
}

onMounted(async () => {
  setTheme('light')
  try {
    const [h, sc] = await Promise.all([api.health(), api.schema()])
    health.value = h
    microservices.value = sc.microservices || []
  } catch {
    // offline
  }
})
</script>

<style scoped>
.sidebar-top {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.brand-box {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 4px 6px;
}
.brand-mark {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: linear-gradient(135deg, #4f46e5 0%, #06b6d4 100%);
  color: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 12px rgba(79, 70, 229, 0.28);
  flex-shrink: 0;
}
.brand-name {
  font-size: 16px;
  font-weight: 750;
  letter-spacing: -0.02em;
  color: var(--text-primary);
  line-height: 1.2;
}
.brand-tagline {
  font-size: 11.5px;
  color: var(--text-muted);
  margin-top: 1px;
}
.nav-label {
  font-size: 11px;
  font-weight: 650;
  color: var(--text-faint);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 0 10px;
  margin-bottom: -10px;
}
.rail-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.rail-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 12px;
  border-radius: 9px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13.5px;
  font-weight: 550;
  cursor: pointer;
  text-align: left;
  transition: all 0.14s ease;
}
.rail-item:hover {
  background: var(--bg-subtle);
  color: var(--text-primary);
}
.rail-item.active {
  background: var(--brand-soft);
  border-color: var(--brand-border);
  color: var(--brand-text);
  font-weight: 650;
}
.rail-count {
  margin-left: auto;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-muted);
  background: var(--bg-muted);
  padding: 1px 7px;
  border-radius: 999px;
}
.rail-badge {
  margin-left: auto;
  font-size: 11px;
  font-weight: 700;
  color: var(--success-text);
  background: var(--success-soft);
  border: 1px solid var(--success-border);
  padding: 1px 7px;
  border-radius: 999px;
}
.sidebar-bottom {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.theme-switch {
  display: grid;
  grid-template-columns: 1fr 1fr;
  background: var(--bg-muted);
  padding: 3px;
  border-radius: 9px;
  border: 1px solid var(--border-subtle);
}
.theme-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 6px 8px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 550;
  cursor: pointer;
  transition: all 0.15s ease;
}
.theme-btn.active {
  background: var(--bg-segmented-active);
  color: var(--text-primary);
  font-weight: 650;
  box-shadow: var(--shadow-xs);
}
.telemetry-card {
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 12px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.tele-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
}
.tele-label {
  font-size: 11.5px;
  color: var(--text-muted);
}
.tele-val {
  font-size: 11.5px;
  color: var(--text-secondary);
}
.tele-divider {
  height: 1px;
  background: var(--border-subtle);
  margin: 3px 0;
}
.tele-model {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.tele-model-name {
  font-size: 12.5px;
  font-weight: 650;
  color: var(--text-primary);
  margin-top: 1px;
}
.form-stack {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 12px;
}
.field-lbl {
  display: block;
  font-size: 12px;
  font-weight: 550;
  color: var(--text-secondary);
  margin-bottom: 5px;
}
.chk-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  cursor: pointer;
}
.chk-row input {
  accent-color: var(--brand);
}
</style>
