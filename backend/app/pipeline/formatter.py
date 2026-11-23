from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.schemas import (
    ExecutionResult,
    QueryPlan,
    QueryResponse,
    QueryStatus,
    QueryTrace,
    ValidationReport,
)
from app.pipeline.llm_client import llm_client


def _extract_readable_items(records: List[Dict[str, Any]], max_items: int = 12) -> List[str]:
    items: List[str] = []
    seen = set()
    for row in records:
        parts = []
        for k, v in row.items():
            if isinstance(v, dict) and v.get("_type") == "node":
                lbl = v.get("caption") or v.get("nodeId")
                parts.append(f"{k}={lbl}")
            elif isinstance(v, dict) and v.get("_type") == "path":
                continue
            elif isinstance(v, dict) and v.get("_type") == "relationship":
                continue
            elif v is not None and v != "":
                parts.append(f"{k}={v}")
        line = " | ".join(parts)
        if line and line not in seen:
            seen.add(line)
            items.append(line)
            if len(items) >= max_items:
                break
    return items


class ResultFormatter:
    """
    Stage 6: Result Formatter & Markdown Report Generator.
    - Generates evidence-grounded natural language summary (uses approved LLM when configured,
      falls back to strictly factual deterministic summary — zero fabricated claims).
    - Generates structured, reproducible Markdown report with trace_id, schema_hash, Cypher,
      safety validation log, evidence table, and path traces.
    """

    def format_summary(
        self,
        question: str,
        plan: QueryPlan,
        execution: ExecutionResult,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        allow_llm: bool = True,
    ) -> str:
        if execution.error:
            return f"查询执行失败：{execution.error}。系统已中止并返回完整错误追踪，未生成无证据推断。"

        if execution.total_records == 0:
            return (
                f"针对问题“{question}”，在当前代码图谱快照中执行只读查询后返回 **0 条记录**。"
                "图谱证据中未发现满足条件的实体或调用/消息路径（详见语义诊断与执行追踪）。"
            )

        truncated_note = ""
        if execution.display_truncated:
            truncated_note = (
                f"（注：拓扑图展示已按预算截断至 {len(execution.nodes)}/{execution.total_nodes_before_truncation} 节点、"
                f"{len(execution.edges)}/{execution.total_edges_before_truncation} 边，评测与统计仍基于完整查询结果。）"
            )

        # Try approved LLM evidence-grounded summary if API key is available
        if allow_llm and api_key != "__OFFLINE__" and llm_client.has_live_credentials(provider, model, api_key, base_url):
            llm_sum = llm_client.summarize_evidence(
                question=question,
                records=execution.records,
                paths=[p.model_dump() for p in execution.paths],
                truncated_note=truncated_note,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
            if llm_sum:
                return llm_sum

        # Deterministic strictly evidence-grounded summary
        return self._deterministic_summary(question, plan, execution, truncated_note)

    def _deterministic_summary(
        self,
        question: str,
        plan: QueryPlan,
        execution: ExecutionResult,
        truncated_note: str,
    ) -> str:
        lines: List[str] = []
        # Check for single-number count result
        if (
            execution.total_records == 1
            and len(execution.records[0]) <= 3
            and any(isinstance(v, (int, float)) for v in execution.records[0].values())
        ):
            kv = "，".join(f"`{k}` = **{v}**" for k, v in execution.records[0].items() if not isinstance(v, dict))
            lines.append(f"根据实库图谱查询证据，统计结果为：{kv}。")
            return "\n".join(lines)

        # Summarize hybrid RPC + MQ dependencies
        if plan.expected_shape == "hybrid_dependencies":
            rpc_rows = [r for r in execution.records if r.get("mechanism") == "RPC"]
            mq_rows = [r for r in execution.records if r.get("mechanism") == "MQ"]
            rpc_svcs = sorted({str(r.get("target_service") or r.get("peer_service")) for r in rpc_rows if r.get("target_service") or r.get("peer_service")})
            mq_svcs = sorted({str(r.get("target_service") or r.get("peer_service")) for r in mq_rows if r.get("target_service") or r.get("peer_service")})
            lines.append(
                f"共检索到 **{execution.total_records}** 条跨服务依赖证据（已按同步 RPC 与异步 MQ 分解并去重合并）："
            )
            if rpc_svcs:
                lines.append(f"- **同步 RPC/HTTP 依赖（{len(rpc_rows)} 条）**：涉及微服务 {', '.join(f'`{s}`' for s in rpc_svcs)}。")
            else:
                lines.append("- **同步 RPC/HTTP 依赖**：无匹配记录。")
            if mq_svcs:
                lines.append(f"- **异步 MQ 消息依赖（{len(mq_rows)} 条）**：涉及微服务 {', '.join(f'`{s}`' for s in mq_svcs)}。")
            else:
                lines.append("- **异步 MQ 消息依赖**：无匹配记录。")
        else:
            lines.append(
                f"基于实库图谱共检索到 **{execution.total_records}** 条记录，构建可视化拓扑包含 "
                f"**{len(execution.nodes)}** 个节点与 **{len(execution.edges)}** 条边。{truncated_note}"
            )

        # Highlight services / APIs / queues directly from evidence
        svcs = sorted({n.label for n in execution.nodes if n.node_type == "微服务"})
        uapis = sorted({n.label for n in execution.nodes if n.node_type == "上游API"})
        queues = sorted({n.label for n in execution.nodes if n.node_type == "消息队列"})
        if svcs and len(svcs) <= 15:
            lines.append(f"- **涉及微服务 ({len(svcs)})**：" + "、".join(f"`{s}`" for s in svcs))
        if uapis and len(uapis) <= 10:
            lines.append(f"- **涉及入口/上游 API ({len(uapis)})**：" + "、".join(f"`{u}`" for u in uapis))
        if queues:
            lines.append(f"- **涉及消息队列 ({len(queues)})**：" + "、".join(f"`{q}`" for q in queues))

        if execution.paths:
            lines.append(f"- **关键调用/消息路径证据（共 {len(execution.paths)} 条，展示前 {min(5, len(execution.paths))} 条）**：")
            for p in execution.paths[:5]:
                lines.append(f"  - `[{p.mechanism} · {p.hops}跳]` {p.summary}")
        else:
            readable = _extract_readable_items(execution.records, max_items=8)
            if readable:
                lines.append("- **代表性结果记录**：")
                for item in readable:
                    lines.append(f"  - {item}")

        return "\n".join(lines)

    def build_markdown_report(self, resp: QueryResponse) -> str:
        t: QueryTrace = resp.trace
        lines = [
            "# 代码图谱 Text2Cypher 查询报告",
            "",
            "## 1. 查询基本信息",
            f"- **用户问题**：{resp.question}",
            f"- **查询状态**：`{resp.status.value}`",
            f"- **复杂度档位**：`{resp.level.value}`",
            f"- **追踪 ID (`trace_id`)**：`{t.trace_id}`",
            f"- **请求 ID (`request_id`)**：`{resp.request_id}`",
            f"- **执行时间**：`{t.timestamp}`（总耗时 `{t.total_latency_ms:.1f} ms`）",
            "",
            "## 2. 可复现版本元数据",
            f"- **图谱版本**：`{t.graph_version}`（Schema 哈希：`{t.schema_hash}`，来源：`{t.schema_source}`）",
            f"- **基础模型**：`{t.model_provider} / {t.model_version}`（执行模式：`{t.execution_mode}`）",
            f"- **知识库版本**：`{t.kb_version}`",
            f"- **加载 Skills**：`{t.skill_versions}`",
            "",
            "## 3. 自然语言总结（严格忠于图谱证据）",
            resp.summary or "（无）",
            "",
            "## 4. 生成的 Cypher 与参数",
        ]

        if resp.cypher_candidates:
            for idx, cand in enumerate(resp.cypher_candidates, 1):
                tag = f"（子查询 {cand.subquery_id} · {cand.mechanism}）" if cand.subquery_id else ""
                lines.extend([
                    f"### 候选语句 {idx} {tag}",
                    "```cypher",
                    cand.query,
                    "```",
                    f"- **参数**：`{cand.params}`",
                    f"- **生成方式**：`{cand.generation_mode}`（模型：`{cand.model_used}`）",
                    "",
                ])
        elif resp.primary_cypher:
            lines.extend([
                "```cypher",
                resp.primary_cypher,
                "```",
                f"- **参数**：`{resp.primary_params}`",
                "",
            ])

        lines.append("## 5. 五层只读安全与校验记录")
        if resp.validation:
            lines.append(f"- **综合判定**：{'✅ 通过' if resp.validation.passed else '❌ 拒绝 / 失败'}")
            for layer in resp.validation.layers:
                icon = "✅" if layer.passed else "❌"
                lines.append(f"  - {icon} `{layer.layer}`：{layer.message}")
        else:
            lines.append("- （未进入校验阶段，例如处于歧义实体澄清等待）")
        lines.append("")

        lines.append("## 6. 图谱执行证据")
        lines.append(
            f"- **返回记录数**：`{resp.execution.total_records}` | **拓扑节点**：`{len(resp.execution.nodes)}` | "
            f"**拓扑边**：`{len(resp.execution.edges)}` | **执行耗时**：`{resp.execution.elapsed_ms:.1f} ms` | "
            f"**完整性**：`{'完整' if resp.execution.complete else '已达上限/不完整'}`"
        )
        if resp.execution.display_truncated:
            lines.append(
                f"- **展示截断提示**：原始拓扑共 {resp.execution.total_nodes_before_truncation} 节点 / "
                f"{resp.execution.total_edges_before_truncation} 边，界面已按 100 节点 / 500 边预算截断。"
            )
        if resp.execution.paths:
            lines.append("")
            lines.append("### 路径证据")
            for idx, p in enumerate(resp.execution.paths[:20], 1):
                lines.append(f"{idx}. `[{p.mechanism} · {p.hops}跳]` {p.summary}")
        else:
            readable = _extract_readable_items(resp.execution.records, max_items=25)
            if readable:
                lines.append("")
                lines.append("### 记录明细（前 25 条）")
                for idx, item in enumerate(readable, 1):
                    lines.append(f"{idx}. {item}")

        lines.extend([
            "",
            "## 7. 六阶段流水线追踪",
            "| 阶段 | 状态 | 耗时 (ms) | 说明 |",
            "|---|---|---:|---|",
        ])
        for st in t.stages:
            lines.append(f"| `{st.stage}` | `{st.status}` | {st.latency_ms:.1f} | {st.summary} |")

        if t.repairs:
            lines.extend(["", "### 受限修复记录"])
            for rp in t.repairs:
                lines.append(
                    f"- 第 {rp.round} 轮：触发原因 `{rp.trigger_reason}` → 校验{'通过' if rp.validation_passed else '失败'}"
                )

        return "\n".join(lines) + "\n"


result_formatter = ResultFormatter()
