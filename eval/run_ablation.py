from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "backend"))

from run_eval import run_evaluation, write_markdown_summary


def main() -> None:
    provider = "deepseek"
    model = "DeepSeek-V4.1-Flash"
    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Full System (Live Approved LLM: KB30 + LLM Rerank + Skills + Entity Binding + T3 Decomposition + Repair + LLM Summary)
    r_full = run_evaluation(
        provider=provider,
        model=model,
        use_kb_rag=True,
        use_llm_rerank=True,
        use_decomposition=True,
        use_semantic_check=True,
        use_llm_summary=True,
        tag="full_system_live_llm",
    )
    (out_dir / "latest_eval_report.json").write_text(json.dumps(r_full, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_summary(r_full, out_dir / "evaluation_report.md")

    # 2. Ablation A: TF-IDF Only Retrieval (no LLM rerank, measures Recall/Accuracy vs Latency tradeoff)
    r_tfidf = run_evaluation(
        provider=provider,
        model=model,
        use_kb_rag=True,
        use_llm_rerank=False,
        use_decomposition=True,
        use_semantic_check=True,
        use_llm_summary=False,
        tag="ablation_tfidf_no_rerank",
    )

    # 3. Ablation B: No T3 Complex Decomposition (single-shot LLM query without RPC+MQ / Impact split)
    r_nodec = run_evaluation(
        provider=provider,
        model=model,
        use_kb_rag=True,
        use_llm_rerank=False,
        use_decomposition=False,
        use_semantic_check=True,
        use_llm_summary=False,
        tag="ablation_no_decomposition",
    )

    # 4. Ablation C: Schema-Only Baseline (No KB30 RAG, No T3 Decomposition, No Semantic Check)
    r_base = run_evaluation(
        provider=provider,
        model=model,
        use_kb_rag=False,
        use_llm_rerank=False,
        use_decomposition=False,
        use_semantic_check=False,
        use_llm_summary=False,
        tag="baseline_schema_only",
    )

    runs = [r_base, r_nodec, r_tfidf, r_full]
    summary = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "model": f"{provider}/{model}",
        "schema_hash": r_full["schema_hash"],
        "graph_version": r_full["graph_version"],
        "runs": [
            {
                "tag": r["tag"],
                "passed": r["passed"],
                "total": r["total"],
                "accuracy_pct": r["accuracy_pct"],
                "grade": r["grade"],
                "simple": r["by_level"]["simple"]["passed"],
                "medium": r["by_level"]["medium"]["passed"],
                "complex": r["by_level"]["complex"]["passed"],
                "p50_ms": r["p50_latency_ms"],
                "p95_ms": r["p95_latency_ms"],
                "repairs": r["total_repairs"],
            }
            for r in runs
        ],
    }
    (out_dir / "ablation_study.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# 实时大模型对照与消融实验报告",
        "",
        f"- **控制变量**：统一基础模型 `{provider}/{model}`、温度 `0.0`、图谱版本 `{r_full['graph_version']}` (`schema_hash={r_full['schema_hash']}`)、禁用离线规划器兜底（100% 纯实时大模型生成）、全量分母固定为 `30`。",
        f"- **实验时间**：`{summary['generated_at']}`",
        "",
        "| 实验配置 | 总分 (/30) | 正确率 | 等级 | 简单 (/10) | 中等 (/12) | 困难 (/8) | P50 (ms) | P95 (ms) | 触发修复 |",
        "|---|---:|---:|:---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["runs"]:
        md.append(
            f"| `{row['tag']}` | **{row['passed']}/30** | {row['accuracy_pct']}% | **{row['grade']}** | {row['simple']}/10 | {row['medium']}/12 | {row['complex']}/8 | {row['p50_ms']} | {row['p95_ms']} | {row['repairs']} |"
        )
    (out_dir / "ablation_study.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n" + "\n".join(md))


if __name__ == "__main__":
    main()
