<template>
  <div class="blueprint-wrap">
    <!-- Truncation Alert -->
    <div v-if="truncated" class="trunc-alert">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
        <path d="M12 9v4M12 17h.01" />
      </svg>
      <span>
        已按界面渲染预算截断展示 <strong>{{ nodes.length }}/{{ totalNodes }}</strong> 个节点、<strong>{{ edges.length }}/{{ totalEdges }}</strong> 条边；右侧记录统计与评测仍基于完整图谱结果。
      </span>
    </div>

    <div class="canvas-stage">
      <!-- Floating Top-Right Controls -->
      <div v-if="nodes.length > 0" class="floating-controls">
        <select v-model="layoutName" class="ctrl-select" @change="applyLayout" title="切换拓扑布局">
          <option value="dagre-lr">分层流向 (左 → 右)</option>
          <option value="dagre-tb">分层流向 (上 → 下)</option>
          <option value="cose">力导向分布</option>
          <option value="concentric">同心环分布</option>
        </select>
        <div class="ctrl-divider"></div>
        <button class="ctrl-icon-btn" title="放大" @click="zoomBy(1.22)">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
        </button>
        <button class="ctrl-icon-btn" title="缩小" @click="zoomBy(0.82)">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14"/></svg>
        </button>
        <button class="ctrl-icon-btn" title="自适应画布" @click="fitGraph">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
          </svg>
        </button>
      </div>

      <!-- Empty / Blocked State -->
      <div v-if="nodes.length === 0" class="canvas-empty">
        <template v-if="queryStatus === 'rejected'">
          <div class="empty-icon danger-icon">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              <path d="m15 9-6 6M9 9l6 6" />
            </svg>
          </div>
          <div class="empty-title">危险查询已被安全防线拦截</div>
          <div class="empty-desc">请求在静态白名单或输入门禁层即被阻断，未接触 Neo4j 图数据库。</div>
        </template>
        <template v-else>
          <div class="empty-icon">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <path d="M3 9h18M9 21V9" />
            </svg>
          </div>
          <div class="empty-title">当前查询返回标量统计或表格结果</div>
          <div class="empty-desc">无可渲染的节点与边对象，请在右侧切换至「数据明细」查看完整记录。</div>
        </template>
      </div>

      <!-- Cytoscape Blueprint Canvas -->
      <div ref="containerRef" class="cy-mount" v-show="nodes.length > 0"></div>

      <!-- Floating Bottom-Left Legend -->
      <div v-if="nodes.length > 0" class="floating-legend">
        <span class="lg-chip"><i class="lg-dot" style="background:#10b981"></i>微服务</span>
        <span class="lg-chip"><i class="lg-dot" style="background:#3b82f6"></i>类</span>
        <span class="lg-chip"><i class="lg-dot" style="background:#6366f1"></i>方法</span>
        <span class="lg-chip"><i class="lg-dot" style="background:#06b6d4"></i>上游API</span>
        <span class="lg-chip"><i class="lg-dot" style="background:#14b8a6"></i>下游API</span>
        <span class="lg-chip"><i class="lg-dot" style="background:#8b5cf6"></i>交换机</span>
        <span class="lg-chip"><i class="lg-dot" style="background:#f59e0b"></i>队列</span>
        <span class="lg-sep"></span>
        <span class="lg-chip"><i class="lg-line" style="background:#10b981"></i>RPC</span>
        <span class="lg-chip"><i class="lg-line lg-dash"></i>MQ消息流</span>
        <span class="lg-chip"><i class="lg-line" style="background:#6366f1"></i>方法调用</span>
      </div>

      <!-- Slide-over Node / Edge Provenance Drawer -->
      <div v-if="selectedItem" class="provenance-drawer">
        <div class="prov-header">
          <span class="badge badge-brand">{{ selectedItem.kind === 'node' ? selectedItem.node_type : selectedItem.rel_type }}</span>
          <button class="ctrl-icon-btn" title="关闭面板" @click="selectedItem = null">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="prov-title">{{ selectedItem.label }}</div>
        <div class="prov-id mono">{{ selectedItem.id }}</div>
        <div class="prov-list">
          <div v-for="(val, key) in selectedItem.properties" :key="key" class="prov-item">
            <div class="prov-k">{{ key }}</div>
            <div class="prov-v mono">{{ formatVal(val) }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import cytoscape from 'cytoscape'
import dagre from 'cytoscape-dagre'
import type { GraphEdge, GraphNode } from '../types'

cytoscape.use(dagre)

const props = defineProps<{
  nodes: GraphNode[]
  edges: GraphEdge[]
  truncated?: boolean
  totalNodes?: number
  totalEdges?: number
  queryStatus?: string
  theme?: 'light' | 'dark'
}>()

const containerRef = ref<HTMLElement | null>(null)
const layoutName = ref('dagre-lr')
const selectedItem = ref<any>(null)
let cy: cytoscape.Core | null = null

const PALETTE_LIGHT: Record<string, { bg: string; border: string; text: string; shape: string }> = {
  微服务: { bg: '#ecfdf5', border: '#10b981', text: '#065f46', shape: 'round-rectangle' },
  类: { bg: '#eff6ff', border: '#3b82f6', text: '#1e40af', shape: 'round-rectangle' },
  方法: { bg: '#eef2ff', border: '#6366f1', text: '#312e81', shape: 'ellipse' },
  上游API: { bg: '#ecfeff', border: '#06b6d4', text: '#155e75', shape: 'round-diamond' },
  下游API: { bg: '#f0fdfa', border: '#14b8a6', text: '#115e59', shape: 'round-triangle' },
  消息交换机: { bg: '#f5f3ff', border: '#8b5cf6', text: '#5b21b6', shape: 'hexagon' },
  消息队列: { bg: '#fffbeb', border: '#f59e0b', text: '#92400e', shape: 'barrel' },
  配置: { bg: '#f1f5f9', border: '#64748b', text: '#334155', shape: 'round-tag' },
}

const PALETTE_DARK: Record<string, { bg: string; border: string; text: string; shape: string }> = {
  微服务: { bg: '#064e3b', border: '#34d399', text: '#ecfdf5', shape: 'round-rectangle' },
  类: { bg: '#1e3a8a', border: '#60a5fa', text: '#eff6ff', shape: 'round-rectangle' },
  方法: { bg: '#312e81', border: '#818cf8', text: '#eef2ff', shape: 'ellipse' },
  上游API: { bg: '#164e63', border: '#22d3ee', text: '#ecfeff', shape: 'round-diamond' },
  下游API: { bg: '#134e4a', border: '#2dd4bf', text: '#f0fdfa', shape: 'round-triangle' },
  消息交换机: { bg: '#4c1d95', border: '#a78bfa', text: '#f5f3ff', shape: 'hexagon' },
  消息队列: { bg: '#78350f', border: '#fbbf24', text: '#fffbeb', shape: 'barrel' },
  配置: { bg: '#27272a', border: '#a1a1aa', text: '#f4f4f5', shape: 'round-tag' },
}

function formatVal(v: any): string {
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'object' && v !== null) return JSON.stringify(v)
  return String(v)
}

function buildElements() {
  const isDark = props.theme === 'dark'
  const palette = isDark ? PALETTE_DARK : PALETTE_LIGHT
  const els: any[] = []
  for (const n of props.nodes) {
    const sty = palette[n.node_type] || {
      bg: isDark ? '#27272a' : '#f1f5f9',
      border: '#94a3b8',
      text: isDark ? '#f4f4f5' : '#0f172a',
      shape: 'ellipse',
    }
    const shortLabel = n.label.length > 26 ? n.label.slice(0, 24) + '…' : n.label
    els.push({
      data: {
        id: n.id,
        label: shortLabel,
        node_type: n.node_type,
        bg: sty.bg,
        border: sty.border,
        shape: sty.shape,
        raw: n,
      },
    })
  }
  for (const e of props.edges) {
    els.push({
      data: {
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.label.length > 22 ? e.label.slice(0, 20) + '…' : e.label,
        rel_type: e.rel_type,
        mechanism: e.mechanism,
        raw: e,
      },
    })
  }
  return els
}

function getLayoutConfig() {
  if (props.edges.length === 0) {
    return { name: 'grid', animate: false, padding: 36 }
  }
  if (layoutName.value === 'dagre-lr') {
    return { name: 'dagre', rankDir: 'LR', nodeSep: 58, rankSep: 132, padding: 38, animate: false }
  }
  if (layoutName.value === 'dagre-tb') {
    return { name: 'dagre', rankDir: 'TB', nodeSep: 58, rankSep: 92, padding: 38, animate: false }
  }
  if (layoutName.value === 'concentric') {
    return { name: 'concentric', padding: 38, animate: false }
  }
  return { name: 'cose', padding: 40, animate: false, idealEdgeLength: 120 }
}

function renderGraph() {
  if (!containerRef.value) return
  if (cy) {
    cy.destroy()
    cy = null
  }
  selectedItem.value = null
  if (props.nodes.length === 0) return

  const isDark = props.theme === 'dark'
  const labelColor = isDark ? '#f4f4f5' : '#1e293b'
  const labelBg = isDark ? '#141418' : '#ffffff'
  const edgeLabelColor = isDark ? '#a1a1aa' : '#475569'

  cy = cytoscape({
    container: containerRef.value,
    elements: buildElements(),
    style: [
      {
        selector: 'node',
        style: {
          'background-color': 'data(bg)',
          'border-color': 'data(border)',
          'border-width': 2.2,
          shape: 'data(shape)' as any,
          label: 'data(label)',
          color: labelColor,
          'font-size': 11,
          'font-weight': 600,
          'font-family': 'Inter, PingFang SC, sans-serif',
          'text-valign': 'bottom',
          'text-halign': 'center',
          'text-margin-y': 6,
          'text-background-color': labelBg,
          'text-background-opacity': 0.9,
          'text-background-padding': '3px' as any,
          'text-background-shape': 'roundrectangle',
          width: 36,
          height: 36,
        },
      },
      {
        selector: 'node[node_type = "微服务"]',
        style: {
          width: 44,
          height: 36,
          'border-width': 2.6,
        },
      },
      {
        selector: 'node:selected',
        style: {
          'border-width': 3.5,
          'border-color': '#4f46e5',
          'overlay-opacity': 0.12,
          'overlay-color': '#4f46e5',
        },
      },
      {
        selector: 'edge',
        style: {
          width: 1.9,
          'line-color': '#94a3b8',
          'target-arrow-color': '#94a3b8',
          'target-arrow-shape': 'triangle',
          'arrow-scale': 0.9,
          'curve-style': 'bezier',
          label: 'data(label)',
          'font-size': 10,
          color: edgeLabelColor,
          'text-rotation': 'autorotate',
          'text-background-color': labelBg,
          'text-background-opacity': 0.88,
          'text-background-padding': '2px' as any,
        },
      },
      {
        selector: 'edge[mechanism = "RPC"]',
        style: {
          'line-color': '#10b981',
          'target-arrow-color': '#10b981',
          width: 2.5,
        },
      },
      {
        selector: 'edge[mechanism = "MQ"]',
        style: {
          'line-color': '#f59e0b',
          'target-arrow-color': '#f59e0b',
          'line-style': 'dashed',
          width: 2.6,
        },
      },
      {
        selector: 'edge[mechanism = "CALL"]',
        style: {
          'line-color': '#6366f1',
          'target-arrow-color': '#6366f1',
          width: 2,
        },
      },
      {
        selector: 'edge[mechanism = "STRUCTURAL"]',
        style: {
          'line-color': '#94a3b8',
          'target-arrow-color': '#94a3b8',
          'line-style': 'dotted',
          width: 1.6,
        },
      },
    ],
    layout: getLayoutConfig() as any,
  })

  cy.on('tap', 'node', (evt) => {
    const raw: GraphNode = evt.target.data('raw')
    selectedItem.value = { kind: 'node', ...raw }
  })
  cy.on('tap', 'edge', (evt) => {
    const raw: GraphEdge = evt.target.data('raw')
    selectedItem.value = { kind: 'edge', ...raw }
  })
  cy.on('tap', (evt) => {
    if (evt.target === cy) selectedItem.value = null
  })
}

function applyLayout() {
  if (cy) cy.layout(getLayoutConfig() as any).run()
}

function fitGraph() {
  if (cy) cy.fit(undefined, 36)
}

function zoomBy(factor: number) {
  if (!cy) return
  cy.zoom({
    level: cy.zoom() * factor,
    renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
  })
}

onMounted(renderGraph)
watch(() => [props.nodes, props.edges, props.theme], renderGraph, { deep: true })
onBeforeUnmount(() => {
  if (cy) cy.destroy()
})
</script>

<style scoped>
.blueprint-wrap {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.trunc-alert {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 13px;
  background: var(--warning-soft);
  border: 1px solid var(--warning-border);
  color: var(--warning-text);
  border-radius: 9px;
  font-size: 12.5px;
}
.canvas-stage {
  position: relative;
  height: 490px;
  border-radius: 12px;
  border: 1px solid var(--border-subtle);
  background-color: var(--bg-canvas);
  background-image: radial-gradient(var(--canvas-dot) 1.15px, transparent 1.15px);
  background-size: 20px 20px;
  overflow: hidden;
}
.cy-mount {
  width: 100%;
  height: 100%;
}
.floating-controls {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 9px;
  box-shadow: var(--shadow-sm);
}
.ctrl-select {
  border: none;
  background: transparent;
  font-size: 12px;
  font-weight: 550;
  color: var(--text-secondary);
  padding: 3px 6px;
  outline: none;
  cursor: pointer;
}
.ctrl-divider {
  width: 1px;
  height: 16px;
  background: var(--border-subtle);
  margin: 0 2px;
}
.ctrl-icon-btn {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.14s;
}
.ctrl-icon-btn:hover {
  background: var(--bg-subtle);
  color: var(--text-primary);
}
.floating-legend {
  position: absolute;
  left: 12px;
  bottom: 12px;
  z-index: 10;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
  padding: 6px 11px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 999px;
  box-shadow: var(--shadow-sm);
  font-size: 11px;
  color: var(--text-secondary);
  max-width: calc(100% - 24px);
}
.lg-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.lg-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.lg-line {
  width: 14px;
  height: 2.5px;
  border-radius: 2px;
  display: inline-block;
}
.lg-dash {
  border-top: 2.5px dashed #f59e0b;
  height: 0;
}
.lg-sep {
  width: 1px;
  height: 11px;
  background: var(--border-default);
}
.canvas-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 28px;
  gap: 8px;
}
.empty-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  color: var(--text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--shadow-xs);
  margin-bottom: 4px;
}
.danger-icon {
  background: var(--danger-soft);
  border-color: var(--danger-border);
  color: var(--danger);
}
.empty-title {
  font-size: 14px;
  font-weight: 650;
  color: var(--text-primary);
}
.empty-desc {
  font-size: 12.5px;
  color: var(--text-muted);
  max-width: 380px;
}
.provenance-drawer {
  position: absolute;
  top: 52px;
  right: 12px;
  bottom: 12px;
  width: 288px;
  z-index: 15;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 12px;
  padding: 14px;
  overflow-y: auto;
  box-shadow: var(--shadow-md);
}
.prov-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.prov-title {
  font-size: 14px;
  font-weight: 650;
  color: var(--text-primary);
  word-break: break-word;
}
.prov-id {
  font-size: 11px;
  color: var(--text-muted);
  word-break: break-all;
  margin: 3px 0 10px;
}
.prov-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.prov-item {
  padding-top: 7px;
  border-top: 1px solid var(--border-subtle);
}
.prov-k {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
}
.prov-v {
  font-size: 11.5px;
  color: var(--text-primary);
  word-break: break-all;
  margin-top: 1px;
}
</style>
