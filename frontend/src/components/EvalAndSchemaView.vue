<template>
  <div class="eval-studio">
    <!-- Executive Golden 30 Benchmark Overview -->
    <section class="card">
      <div class="section-header">
        <div>
          <div class="section-title">独立黄金 30 题实测与等级评定</div>
          <div class="section-subtitle">测试集与 KB30 完全独立、零题目重合 · 评分标准：A ≥ 27 · B 24–26 · C 20–23 · D &lt; 20</div>
        </div>
        <button class="btn btn-sm" @click="loadAll">刷新评测快照</button>
      </div>

      <template v-if="evalData?.has_report && evalData.report">
        <!-- 4 Executive KPI Cards -->
        <div class="kpi-bento">
          <div class="kpi-tile accent-brand">
            <div class="kpi-k">黄金集实测得分</div>
            <div class="kpi-v">{{ evalData.report.passed }} <span class="kpi-denom">/ {{ evalData.report.total }}</span></div>
            <div class="kpi-meta">综合正确率 <strong>{{ evalData.report.accuracy_pct }}%</strong></div>
          </div>

          <div class="kpi-tile accent-emerald">
            <div class="kpi-k">课程评定等级</div>
            <div class="kpi-v" style="color: var(--success);">{{ evalData.report.grade }} 档</div>
            <div class="kpi-meta mono">{{ evalData.report.mode }}</div>
          </div>

          <div class="kpi-tile accent-cyan">
            <div class="kpi-k">三档难度拆解</div>
            <div class="kpi-v" style="font-size: 19px;">
              S {{ evalData.report.by_level?.simple?.passed }}/10 ·
              M {{ evalData.report.by_level?.medium?.passed }}/12 ·
              H {{ evalData.report.by_level?.complex?.passed }}/8
            </div>
            <div class="kpi-meta">一轮直过 · 触发修复 {{ evalData.report.total_repairs ?? 0 }} 次</div>
          </div>

          <div class="kpi-tile accent-amber">
            <div class="kpi-k">端到端时延 P95</div>
            <div class="kpi-v">{{ (evalData.report.p95_latency_ms / 1000).toFixed(2) }} <span class="kpi-denom">s</span></div>
            <div class="kpi-meta">P50 {{ (evalData.report.p50_latency_ms / 1000).toFixed(2) }} s（预算上限 ≤ 20 s）</div>
          </div>
        </div>

        <!-- Visual 4-Way Live Ablation Comparison -->
        <div v-if="evalData?.ablation?.runs?.length" class="ablation-panel">
          <div class="ablation-head">
            <div>
              <strong>同模型对照与消融实验</strong>
              <span class="muted" style="margin-left:8px; font-size:12px;">
                控制变量：统一基础模型 {{ evalData.ablation.model }} · 温度 0.0 · 冻结图谱 {{ evalData.ablation.schema_hash }} · 100% 纯实时大模型生成
              </span>
            </div>
          </div>

          <div class="ablation-bars">
            <div v-for="ab in evalData.ablation.runs" :key="ab.tag" class="ab-row">
              <div class="ab-name">
                <span class="mono" style="font-weight:650;">{{ formatAblationName(ab.tag) }}</span>
                <span class="badge" :class="ab.grade === 'A' ? 'badge-success' : 'badge-warning'">{{ ab.grade }} 档</span>
              </div>
              <div class="ab-track">
                <div
                  class="ab-fill"
                  :class="ab.passed === 30 ? 'fill-emerald' : ab.grade === 'A' ? 'fill-brand' : 'fill-amber'"
                  :style="{ width: ab.accuracy_pct + '%' }"
                ></div>
              </div>
              <div class="ab-stats mono">
                <strong>{{ ab.passed }}/30</strong> ({{ ab.accuracy_pct }}%) · 困难 {{ ab.complex }}/8 · P95 {{ (ab.p95_ms / 1000).toFixed(1) }}s
              </div>
            </div>
          </div>
        </div>

        <!-- Per-Question Ledger -->
        <div class="table-wrap" style="max-height: 360px; margin-top: 16px;">
          <table class="clean-table">
            <thead>
              <tr>
                <th>题号</th>
                <th>档位</th>
                <th>测试问题</th>
                <th>判定</th>
                <th>返回行数</th>
                <th>耗时</th>
                <th>核验口径</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in evalData.report.results" :key="r.id">
                <td class="mono"><strong>{{ r.id }}</strong></td>
                <td><span class="badge">{{ r.level }}</span></td>
                <td style="font-weight:550;">{{ r.question }}</td>
                <td>
                  <span class="badge" :class="r.passed ? 'badge-success' : 'badge-danger'">
                    {{ r.passed ? '通过' : '未通过' }}
                  </span>
                </td>
                <td class="mono">{{ r.record_count }}</td>
                <td class="mono" style="white-space:nowrap;">{{ (r.latency_ms / 1000).toFixed(2) }} s</td>
                <td class="muted mono" style="font-size:11.5px;">{{ r.reason }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </section>

    <!-- Interactive Five-Layer Firewall Sandbox -->
    <section class="card">
      <div class="section-header" style="margin-bottom: 12px;">
        <div>
          <div class="section-title">五层只读安全防线实时演练沙箱</div>
          <div class="section-subtitle">在线检验词法只读白名单、禁用过程拦截、实库 Schema 预检与 Neo4j EXPLAIN 执行计划审查</div>
        </div>
        <div style="display:flex; gap:6px; flex-wrap:wrap;">
          <button class="btn btn-sm" @click="testAttack('MATCH (n) DETACH DELETE n RETURN count(n)')">演练: DETACH DELETE</button>
          <button class="btn btn-sm" @click="testAttack('CREATE (x:`微服务` {`服务名称`: \'hack\'}) RETURN x')">演练: CREATE 写入</button>
          <button class="btn btn-sm" @click="testAttack('CALL dbms.security.listUsers() YIELD username RETURN username')">演练: 未授权过程</button>
          <button class="btn btn-sm" @click="testAttack('MATCH (a:`方法`)-[:CALLS*]->(b:`API端点`) RETURN a, b')">演练: 虚构标签 + 无界*</button>
          <button class="btn btn-soft btn-sm" @click="testAttack('MATCH (s:`微服务`) RETURN s.nodeId, s.`服务名称` LIMIT 5')">演练: 合法只读查询</button>
        </div>
      </div>

      <div style="display:flex; gap:10px; align-items:stretch;">
        <textarea v-model="testCypher" class="textarea mono" rows="2" style="flex:1; font-size:12.5px;"></textarea>
        <button class="btn btn-primary" @click="runValidate">立即预检</button>
      </div>

      <div v-if="valResult" class="sandbox-out">
        <div style="display:flex; align-items:center; gap:8px; margin-bottom:10px;">
          <strong>预检结论：</strong>
          <span class="badge" :class="valResult.passed ? 'badge-success' : 'badge-danger'">
            {{ valResult.passed ? '五层防线全部通过（纯只读计划）' : `已阻断 · ${valResult.violation_category}` }}
          </span>
        </div>
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:8px;">
          <div v-for="l in valResult.layers" :key="l.layer" class="card-subtle" style="padding:10px 12px;">
            <div style="display:flex; align-items:center; gap:6px; font-size:12px; font-weight:650;">
              <span :style="{ color: l.passed ? 'var(--success)' : 'var(--danger)' }">{{ l.passed ? '● 通过' : '● 阻断' }}</span>
              <span class="mono">{{ l.layer }}</span>
            </div>
            <div class="secondary" style="font-size:12px; margin-top:3px;">{{ l.message }}</div>
          </div>
        </div>
      </div>
    </section>

    <!-- Schema Contract & Versioned Skills Bento -->
    <div class="bento-split">
      <section class="card">
        <div class="section-header" style="margin-bottom: 10px;">
          <div>
            <div class="section-title">实库冻结 Schema 契约</div>
            <div class="section-subtitle">版本 {{ schema?.graph_version }} · 哈希 {{ schema?.schema_hash }} · Neo4j {{ schema?.neo4j_version }}</div>
          </div>
          <button class="btn btn-sm" @click="refreshSchema">重新同步</button>
        </div>

        <div v-if="schema">
          <div style="margin-bottom:12px;">
            <div class="sub-head">8 种节点标签</div>
            <div class="badge-cloud">
              <span v-for="(info, lbl) in schema.node_labels" :key="lbl" class="badge badge-brand">
                {{ lbl }} · {{ info.count }}
              </span>
            </div>
          </div>

          <div style="margin-bottom:12px;">
            <div class="sub-head">8 种关系类型</div>
            <div class="badge-cloud">
              <span v-for="(info, rt) in schema.rel_types" :key="rt" class="badge badge-success">
                {{ rt }} · {{ info.count }}
              </span>
            </div>
          </div>

          <div>
            <div class="sub-head">核心合法边结构模式</div>
            <div class="pattern-grid">
              <div v-for="(p, i) in (schema.rel_patterns || []).slice(0, 12)" :key="i" class="pattern-row mono">
                <span>(:{{ p.src }}) -[:{{ p.rel }}]-> (:{{ p.dst }})</span>
                <span class="muted">{{ p.cnt }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="section-header" style="margin-bottom: 10px;">
          <div>
            <div class="section-title">已挂载 Agent Skills</div>
            <div class="section-subtitle">遵循 SKILL.md 规范的可版本化领域知识与防幻觉规则</div>
          </div>
        </div>

        <div style="display:flex; flex-direction:column; gap:10px;">
          <div v-for="sk in skills" :key="sk.name" class="card-subtle">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <strong class="mono" style="font-size:13px;">{{ sk.name }}</strong>
              <span class="badge badge-success">v{{ sk.version }}</span>
            </div>
            <div class="secondary" style="font-size:12.5px; margin-top:4px;">{{ sk.description }}</div>
            <div class="muted mono" style="font-size:11px; margin-top:5px;">适用范围: {{ sk.applies_to.join(', ') }}</div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { ValidationReport } from '../types'

const evalData = ref<any>(null)
const schema = ref<any>(null)
const skills = ref<any[]>([])
const testCypher = ref('MATCH (n) DETACH DELETE n RETURN count(n)')
const valResult = ref<ValidationReport | null>(null)

function formatAblationName(tag: string) {
  const map: Record<string, string> = {
    baseline_schema_only: '1. 仅 Schema 基线 (无KB/无分解)',
    ablation_no_decomposition: '2. 移除 T3 子查询分解',
    ablation_tfidf_no_rerank: '3. 仅 TF-IDF 检索 + T3 分解',
    full_system_live_llm: '4. 完整系统 (LLM重排 + T3分解 + 总结)',
  }
  return map[tag] || tag
}

async function loadAll() {
  const [ev, sc, sk] = await Promise.all([api.evalSummary(), api.schema(), api.skills()])
  evalData.value = ev
  schema.value = sc
  skills.value = sk
}

async function refreshSchema() {
  schema.value = await api.refreshSchema()
}

function testAttack(cy: string) {
  testCypher.value = cy
  runValidate()
}

async function runValidate() {
  valResult.value = await api.validateCypher(testCypher.value, {}, true)
}

onMounted(loadAll)
</script>

<style scoped>
.eval-studio {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.kpi-bento {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}
@media (max-width: 1024px) {
  .kpi-bento {
    grid-template-columns: 1fr 1fr;
  }
}
.kpi-tile {
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-top-width: 3px;
  border-radius: 12px;
  padding: 15px 18px;
}
.accent-brand { border-top-color: var(--brand); }
.accent-emerald { border-top-color: var(--success); }
.accent-cyan { border-top-color: var(--info); }
.accent-amber { border-top-color: var(--warning); }
.kpi-k {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
}
.kpi-v {
  font-size: 28px;
  font-weight: 780;
  letter-spacing: -0.02em;
  margin: 5px 0 4px;
  color: var(--text-primary);
}
.kpi-denom {
  font-size: 16px;
  font-weight: 550;
  color: var(--text-muted);
}
.kpi-meta {
  font-size: 12px;
  color: var(--text-secondary);
}
.ablation-panel {
  margin-top: 16px;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 12px;
  padding: 15px 18px;
}
.ablation-head {
  font-size: 13.5px;
  margin-bottom: 12px;
}
.ablation-bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ab-row {
  display: grid;
  grid-template-columns: 270px 1fr 270px;
  align-items: center;
  gap: 14px;
  font-size: 12.5px;
}
@media (max-width: 1024px) {
  .ab-row {
    grid-template-columns: 1fr;
    gap: 4px;
  }
}
.ab-name {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}
.ab-track {
  height: 9px;
  background: var(--bg-muted);
  border-radius: 999px;
  overflow: hidden;
}
.ab-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.4s ease;
}
.fill-emerald { background: linear-gradient(90deg, #10b981, #059669); }
.fill-brand { background: linear-gradient(90deg, #6366f1, #4f46e5); }
.fill-amber { background: linear-gradient(90deg, #f59e0b, #d97706); }
.ab-stats {
  text-align: right;
  font-size: 12px;
  color: var(--text-secondary);
}
.sandbox-out {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--border-subtle);
}
.sub-head {
  font-size: 12px;
  font-weight: 650;
  color: var(--text-muted);
  margin-bottom: 6px;
}
.badge-cloud {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.pattern-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.pattern-row {
  display: flex;
  justify-content: space-between;
  font-size: 11.5px;
  padding: 5px 9px;
  background: var(--bg-subtle);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
}
</style>
