from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings
from app.models.schemas import QueryLevel, QueryOptions, QueryRequest, QueryStatus
from app.pipeline.llm_client import llm_client
from app.pipeline.schema_fetcher import schema_fetcher
from app.services.orchestrator import orchestrator


def grade_from_score(passed: int, total: int = 30) -> str:
    if total != 30:
        pct = passed / max(1, total)
        if pct >= 0.90:
            return "A"
        if pct >= 0.80:
            return "B"
        if pct >= 0.667:
            return "C"
        return "D"
    if passed >= 27:
        return "A"
    if passed >= 24:
        return "B"
    if passed >= 20:
        return "C"
    return "D"


def _extract_candidate_ids_from_row(row: Dict[str, Any], expected_set: Set[str]) -> Set[str]:
    """Extract canonical stable IDs from semantic-equivalent LLM RETURN columns (never relaxes set equality)."""
    found: Set[str] = set()
    expects_svc_prefix = all(x.startswith("svc:") for x in expected_set)
    for k, v in row.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and v.get("_type") == "node":
            nid = str(v.get("nodeId") or "")
            if nid:
                found.add(nid)
        elif isinstance(v, str):
            found.add(v)
            if expects_svc_prefix and v.startswith("ts-"):
                found.add(f"svc:{v}")
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    found.add(item)
                    if expects_svc_prefix and item.startswith("ts-"):
                        found.add(f"svc:{item}")
    return found


def compare_result(spec: Dict[str, Any], records: List[Dict[str, Any]]) -> Tuple[bool, str]:
    etype = spec["eval_type"]
    expected = spec["expected"]

    if etype == "scalar_int":
        key = spec["eval_key"]
        for r in records:
            if key in r and isinstance(r[key], (int, float)) and not isinstance(r[key], bool):
                got = int(r[key])
                return (got == int(expected), f"expected={expected}, got={got}")
        for r in records:
            ints = [int(v) for k, v in r.items() if not k.startswith("_") and isinstance(v, (int, float)) and not isinstance(v, bool)]
            if len(ints) == 1 and ints[0] == int(expected):
                return (True, f"expected={expected}, got={ints[0]} (alias matched)")
        return (False, f"scalar mismatch: expected={expected}, rows={records[:2]}")

    if etype in ("id_set", "str_set"):
        key = spec["eval_key"]
        exp_set = set(expected)
        got_set = {str(r[key]) for r in records if key in r and r[key] is not None and not isinstance(r[key], (dict, list))}
        if got_set == exp_set:
            return (True, f"exact set match ({len(exp_set)} items)")

        # Try semantic-equivalent single column extraction (strictly checking per column so unrelated nodes don't pollute)
        if records:
            candidate_cols = [k for k in records[0].keys() if not k.startswith("_")]
            expects_svc = all(x.startswith("svc:") for x in exp_set)
            # Check each column individually first (strictest)
            for col in candidate_cols:
                col_vals: Set[str] = set()
                for r in records:
                    v = r.get(col)
                    if isinstance(v, dict) and v.get("_type") == "node" and v.get("nodeId"):
                        col_vals.add(str(v["nodeId"]))
                    elif isinstance(v, str) and v:
                        col_vals.add(f"svc:{v}" if (expects_svc and v.startswith("ts-")) else v)
                    elif isinstance(v, list):
                        for it in v:
                            if isinstance(it, str) and it:
                                col_vals.add(f"svc:{it}" if (expects_svc and it.startswith("ts-")) else it)
                if col_vals == exp_set:
                    return (True, f"exact set match via col '{col}' ({len(exp_set)} items)")

            # For multi-subquery union results (e.g., H05/H06 where column names may vary slightly across subqueries)
            if len({r.get("_subquery") for r in records if r.get("_subquery")}) > 1:
                union_vals: Set[str] = set()
                for r in records:
                    row_ids = _extract_candidate_ids_from_row(r, exp_set)
                    # Keep IDs matching expected namespace prefix (uapi:/def:/svc:)
                    prefixes = {x.split(":")[0] + ":" for x in exp_set if ":" in x}
                    for rid in row_ids:
                        if not prefixes or any(rid.startswith(pf) for pf in prefixes):
                            union_vals.add(rid)
                # Only count as exact match if union matches expected without missing expected items
                if union_vals == exp_set:
                    return (True, f"exact decomposed set match ({len(exp_set)} items)")

            # Build best diagnostic diff
            best_col = max(
                candidate_cols,
                key=lambda c: len(
                    {
                        (str(r[c]["nodeId"]) if isinstance(r.get(c), dict) and r[c].get("nodeId") else str(r.get(c)))
                        for r in records
                        if r.get(c) is not None
                    }
                    & exp_set
                ),
            )
            got_best: Set[str] = set()
            for r in records:
                v = r.get(best_col)
                if isinstance(v, dict) and v.get("nodeId"):
                    got_best.add(str(v["nodeId"]))
                elif isinstance(v, str):
                    got_best.add(f"svc:{v}" if (expects_svc and v.startswith("ts-")) else v)
            missing = sorted(exp_set - got_best)[:5]
            extra = sorted(got_best - exp_set)[:5]
            return (False, f"set mismatch (best col '{best_col}'): exp={len(exp_set)}, got={len(got_best)}, missing={missing}, extra={extra}")

        return (False, f"empty records (expected {len(exp_set)} items)")

    if etype == "kv_map":
        ek, ev = spec["eval_key"], spec["eval_val"]
        got_map: Dict[str, int] = {}
        for r in records:
            if ek in r and ev in r and r[ek] is not None and isinstance(r[ev], (int, float)):
                got_map[str(r[ek])] = int(r[ev])
        if got_map == expected:
            return (True, f"map match ({len(expected)} entries)")
        # Alias-agnostic single-string + single-int column match
        alt_map: Dict[str, int] = {}
        for r in records:
            str_vals = [str(v) for k, v in r.items() if not k.startswith("_") and isinstance(v, str) and v.startswith("ts-")]
            int_vals = [int(v) for k, v in r.items() if not k.startswith("_") and isinstance(v, (int, float)) and not isinstance(v, bool)]
            if len(str_vals) >= 1 and len(int_vals) == 1:
                alt_map[str_vals[0]] = int_vals[0]
        if alt_map == expected:
            return (True, f"map match via semantic columns ({len(expected)} entries)")
        return (False, f"map mismatch: exp={expected}, got={got_map or alt_map}")

    if etype == "pair_set":
        k1, k2 = spec["eval_keys"]
        exp_pairs = set(expected)

        def _norm_mech(val: str) -> str:
            u = val.upper()
            if "MQ" in u:
                return "MQ"
            if "RPC" in u or "HTTP" in u or "REST" in u:
                return "RPC"
            return u

        got_pairs: Set[str] = set()
        for r in records:
            m_val = r.get(k1) or r.get("mechanism") or r.get("type") or r.get("通信机制")
            p_val = r.get(k2) or r.get("peer_service") or r.get("target_service") or r.get("service") or r.get("service_name")
            if not p_val:
                # Look for any ts-* string value in the row (excluding the queried source service if another ts-* exists)
                for k, v in r.items():
                    if not k.startswith("_") and isinstance(v, str) and v.startswith("ts-"):
                        p_val = v
                        break
                    if isinstance(v, dict) and v.get("_type") == "node" and "微服务" in (v.get("labels") or []):
                        p_val = v.get("caption")
                        break
            if m_val and p_val:
                got_pairs.add(f"{_norm_mech(str(m_val))}::{p_val}")

        ok = got_pairs == exp_pairs
        if ok:
            return (True, f"exact pair set match ({len(exp_pairs)} pairs)")
        return (False, f"pair mismatch: exp={sorted(exp_pairs)}, got={sorted(got_pairs)}")

    return (False, f"unknown eval_type {etype}")


def run_evaluation(
    provider: str = "deepseek",
    model: str = "DeepSeek-V4.1-Flash",
    api_key: str = "",
    base_url: str = "",
    offline: bool = False,
    use_kb_rag: bool = True,
    use_llm_rerank: bool = True,
    use_decomposition: bool = True,
    use_semantic_check: bool = True,
    use_llm_summary: bool = True,
    tag: str = "full_system",
) -> Dict[str, Any]:
    golden_path = ROOT / "data" / "golden30.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    schema = schema_fetcher.get_schema(force_refresh=False)

    cfg = llm_client.resolve_runtime_config(
        provider=provider,
        model=model,
        api_key=api_key or None,
        base_url=base_url or None,
    )
    live_llm = (not offline) and bool(cfg.get("api_key"))
    mode_label = f"llm:{cfg['provider']}/{cfg['model']}" if live_llm else "hybrid_planner_offline"

    results = []
    latencies = []
    by_level = {
        "simple": {"total": 0, "passed": 0},
        "medium": {"total": 0, "passed": 0},
        "complex": {"total": 0, "passed": 0},
    }
    total_repairs = 0

    print(f"\n>>> Starting Evaluation [{tag}] mode={mode_label} rag={use_kb_rag} rerank={use_llm_rerank} decomp={use_decomposition} sem={use_semantic_check}")

    for spec in golden:
        lvl = spec["level"]
        by_level[lvl]["total"] += 1
        req = QueryRequest(
            question=spec["question"],
            level=QueryLevel(lvl),
            options=QueryOptions(
                clarify_on_ambiguity=False,
                use_kb_rag=use_kb_rag,
                use_llm_rerank=use_llm_rerank and live_llm,
                use_decomposition=use_decomposition,
                use_semantic_check=use_semantic_check,
                use_llm_summary=use_llm_summary and live_llm,
                force_offline=offline,
                allow_planner_fallback=not live_llm,
                provider=provider,
                model=model,
                api_key=api_key or None,
                base_url=base_url or None,
            ),
            request_id=f"eval-{tag}-{spec['id']}",
        )
        resp = orchestrator.run_query(req, save_trace=False)
        latencies.append(resp.trace.total_latency_ms)
        total_repairs += len(resp.trace.repairs)

        val_ok = bool(resp.validation and resp.validation.passed)
        exec_ok = resp.status == QueryStatus.SUCCESS and resp.execution.complete and not resp.execution.error
        sum_ok = bool(resp.summary and len(resp.summary.strip()) > 5)

        if not val_ok:
            passed, reason = False, f"validation failed: {resp.validation.violation_category if resp.validation else 'none'}"
        elif not exec_ok:
            passed, reason = False, f"execution status={resp.status.value}, err={resp.execution.error}"
        elif not sum_ok:
            passed, reason = False, "empty summary"
        else:
            passed, reason = compare_result(spec, resp.execution.records)

        if passed:
            by_level[lvl]["passed"] += 1

        results.append(
            {
                "id": spec["id"],
                "level": lvl,
                "category": spec["category"],
                "question": spec["question"],
                "passed": passed,
                "reason": reason,
                "status": resp.status.value,
                "generation_modes": [c.generation_mode for c in resp.cypher_candidates],
                "repairs": len(resp.trace.repairs),
                "cypher": resp.primary_cypher,
                "all_cyphers": [{"sq": c.subquery_id, "cypher": c.query, "params": c.params} for c in resp.cypher_candidates],
                "subquery_count": len(resp.cypher_candidates),
                "record_count": resp.execution.total_records,
                "nodes": len(resp.execution.nodes),
                "edges": len(resp.execution.edges),
                "latency_ms": resp.trace.total_latency_ms,
                "summary": resp.summary,
            }
        )
        rep_tag = f" [repaired×{len(resp.trace.repairs)}]" if resp.trace.repairs else ""
        print(f"[{'PASS' if passed else 'FAIL'}] {spec['id']} ({lvl:7s}) {resp.trace.total_latency_ms:7.1f}ms{rep_tag} | {reason}")

    total = len(results)
    passed_cnt = sum(1 for r in results if r["passed"])
    sorted_lat = sorted(latencies)
    p50 = sorted_lat[len(sorted_lat) // 2] if sorted_lat else 0.0
    p95 = sorted_lat[min(len(sorted_lat) - 1, math.ceil(len(sorted_lat) * 0.95) - 1)] if sorted_lat else 0.0

    return {
        "tag": tag,
        "mode": mode_label,
        "provider": cfg["provider"],
        "model": cfg["model"],
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "schema_hash": schema.schema_hash,
        "graph_version": schema.graph_version,
        "config": {
            "use_kb_rag": use_kb_rag,
            "use_llm_rerank": use_llm_rerank and live_llm,
            "use_decomposition": use_decomposition,
            "use_semantic_check": use_semantic_check,
            "use_llm_summary": use_llm_summary and live_llm,
        },
        "total": total,
        "passed": passed_cnt,
        "accuracy_pct": round(passed_cnt * 100.0 / max(1, total), 2),
        "grade": grade_from_score(passed_cnt, total),
        "p50_latency_ms": round(p50, 2),
        "p95_latency_ms": round(p95, 2),
        "total_repairs": total_repairs,
        "by_level": by_level,
        "results": results,
    }


def write_markdown_summary(report: Dict[str, Any], out_md: Path) -> None:
    lines = [
        "# 独立黄金 30 题评测报告",
        "",
        f"- **评测配置**: `{report['tag']}` | **运行模式**: `{report['mode']}`",
        f"- **图谱版本**: `{report['graph_version']}` (`schema_hash={report['schema_hash']}`)",
        f"- **评测时间**: `{report['timestamp']}`",
        f"- **总成绩**: **{report['passed']} / {report['total']} ({report['accuracy_pct']}%)** → 评级 **{report['grade']} 档**",
        f"- **分层成绩**: 简单 S1–S10 `{report['by_level']['simple']['passed']}/10` | 中等 M1–M12 `{report['by_level']['medium']['passed']}/12` | 困难 H1–H8 `{report['by_level']['complex']['passed']}/8`",
        f"- **端到端时延**: P50 = `{report['p50_latency_ms']} ms` | P95 = `{report['p95_latency_ms']} ms`（预算目标 ≤ 20,000 ms）| 触发受限修复 `{report.get('total_repairs', 0)}` 次",
        "",
        "| 题号 | 难度 | 类别 | 结果 | 记录数 | 修复 | 耗时 (ms) | 评测依据 |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for r in report["results"]:
        lines.append(
            f"| {r['id']} | {r['level']} | {r['category']} | {'✅ PASS' if r['passed'] else '❌ FAIL'} | {r['record_count']} | {r.get('repairs', 0)} | {r['latency_ms']} | {r['reason']} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run independent Golden 30 evaluation on live Neo4j")
    parser.add_argument("--provider", default=settings.llm_provider, choices=["deepseek", "glm", "kimi"])
    parser.add_argument("--model", default=settings.llm_model)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--no-kb", action="store_true")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--no-decomp", action="store_true")
    parser.add_argument("--no-semantic", action="store_true")
    parser.add_argument("--no-llm-summary", action="store_true")
    parser.add_argument("--tag", default="full_system")
    parser.add_argument("--save-as", default="latest_eval_report.json")
    args = parser.parse_args()

    report = run_evaluation(
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        offline=args.offline,
        use_kb_rag=not args.no_kb,
        use_llm_rerank=not args.no_rerank,
        use_decomposition=not args.no_decomp,
        use_semantic_check=not args.no_semantic,
        use_llm_summary=not args.no_llm_summary,
        tag=args.tag,
    )

    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / args.save_as).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.save_as == "latest_eval_report.json":
        write_markdown_summary(report, out_dir / "evaluation_report.md")
    print(f"\n=== SUMMARY ({report['mode']} | {report['tag']}) ===")
    print(f"Score: {report['passed']}/{report['total']} ({report['accuracy_pct']}%) | Grade: {report['grade']} | P50: {report['p50_latency_ms']}ms | P95: {report['p95_latency_ms']}ms | Repairs: {report['total_repairs']}")


if __name__ == "__main__":
    main()
