from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import subprocess
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import PROJECT_ROOT, settings
from app.models.schemas import (
    CypherCandidate,
    ExecutionResult,
    GraphEdge,
    GraphNode,
    PathEvidence,
    QueryLevel,
    QueryPlan,
    QueryRequest,
    QueryResponse,
    QueryStatus,
    QueryTrace,
    RepairAttempt,
    SafetyLayerResult,
    SemanticCheckResult,
    StageTrace,
    ValidationReport,
)
from app.pipeline.executor import cypher_executor
from app.pipeline.formatter import result_formatter
from app.pipeline.llm_client import llm_client
from app.pipeline.prompt_builder import prompt_builder
from app.pipeline.retriever import kb_retriever
from app.pipeline.schema_fetcher import schema_fetcher
from app.pipeline.semantic_checker import semantic_checker
from app.pipeline.skill_loader import skill_loader
from app.pipeline.validator import cypher_validator
from app.services.database import app_db
from app.services.decomposer import detect_malicious_input, query_decomposer
from app.services.entity_disambiguator import entity_disambiguator

logger = logging.getLogger(__name__)


def _get_code_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT),
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.decode().strip() or "workspace"
    except Exception:
        return "workspace"


class Text2CypherOrchestrator:
    """
    Master Six-Stage Text2Cypher Agent Orchestrator:
      Stage 1: Schema Fetcher
      Pre-stage: Malicious-Input Guard + Entity Recognition & Homonym Disambiguation
      Stage 2: Query Plan + T3 Decomposition + Skill Loader + Hybrid KB Retrieval + Prompt Builder
      Stage 3: Approved LLM (DeepSeek/GLM/Kimi) or Transparent Structured Hybrid Planner
      Stage 4: Five-Layer Read-Only Safety + Schema + Neo4j EXPLAIN Validator
      Stage 5: Controlled Read-Only Execution + Subquery Merge + Layer-5 Semantic Check + Bounded Repair (<=2)
      Stage 6: Evidence-Grounded Summary + Cytoscape Topology + Trace & Markdown Report
    """

    def run_query(self, req: QueryRequest, save_trace: bool = True) -> QueryResponse:
        t_start = time.perf_counter()
        request_id = req.request_id or f"req-{uuid.uuid4().hex[:10]}"
        trace_id = f"tr-{uuid.uuid4().hex[:12]}"
        stages: List[StageTrace] = []
        repairs: List[RepairAttempt] = []
        opts = req.options

        # Validate approved LLM provider upfront (strict course policy!)
        try:
            llm_cfg = llm_client.resolve_runtime_config(
                provider=opts.provider,
                model=opts.model,
                api_key=opts.api_key,
                base_url=opts.base_url,
            )
        except ValueError as exc:
            trace = QueryTrace(
                trace_id=trace_id,
                request_id=request_id,
                model_provider=str(opts.provider or settings.llm_provider),
                model_version=str(opts.model or settings.llm_model),
                execution_mode="rejected_policy",
                code_commit=_get_code_commit(),
                total_latency_ms=round((time.perf_counter() - t_start) * 1000, 2),
                stages=[StageTrace(stage="policy_check", status="failed", latency_ms=0.1, summary=str(exc))],
            )
            resp = QueryResponse(
                request_id=request_id,
                trace_id=trace_id,
                question=req.question,
                status=QueryStatus.REJECTED,
                level=req.level,
                summary=str(exc),
                trace=trace,
            )
            resp.markdown_report = result_formatter.build_markdown_report(resp)
            if save_trace:
                app_db.save_trace(resp)
            return resp

        has_llm_key = bool(llm_cfg.get("api_key")) and (not opts.force_offline)

        # ===== STAGE 1: Schema Fetcher =====
        t1 = time.perf_counter()
        schema = schema_fetcher.get_schema(force_refresh=False)
        stages.append(
            StageTrace(
                stage="1_schema_fetcher",
                status="ok",
                latency_ms=round((time.perf_counter() - t1) * 1000, 2),
                summary=f"加载冻结 Schema ({schema.graph_version}, hash={schema.schema_hash}, {len(schema.node_labels)}标签/{len(schema.rel_types)}关系)",
                details={"schema_hash": schema.schema_hash, "source": schema.source},
            )
        )

        # ===== PRE-STAGE A: Prompt-Injection & Write-Intent Guard =====
        t_guard = time.perf_counter()
        malicious_reason = detect_malicious_input(req.question)
        if malicious_reason:
            val_report = ValidationReport(
                passed=False,
                violation_category="MALICIOUS_INPUT_OR_WRITE_INTENT",
                layers=[
                    SafetyLayerResult(
                        layer="L0_input_intent_guard",
                        passed=False,
                        message=malicious_reason,
                        violations=[malicious_reason],
                    )
                ],
            )
            stages.append(
                StageTrace(
                    stage="0_input_safety_guard",
                    status="failed",
                    latency_ms=round((time.perf_counter() - t_guard) * 1000, 2),
                    summary=malicious_reason,
                )
            )
            trace = QueryTrace(
                trace_id=trace_id,
                request_id=request_id,
                schema_hash=schema.schema_hash,
                graph_version=schema.graph_version,
                schema_source=schema.source,
                model_provider=llm_cfg["provider"],
                model_version=llm_cfg["model"],
                execution_mode="blocked_readonly_guard",
                code_commit=_get_code_commit(),
                total_latency_ms=round((time.perf_counter() - t_start) * 1000, 2),
                stages=stages,
            )
            resp = QueryResponse(
                request_id=request_id,
                trace_id=trace_id,
                question=req.question,
                status=QueryStatus.REJECTED,
                level=req.level,
                validation=val_report,
                summary=f"⛔ 安全防线拦截：{malicious_reason}",
                trace=trace,
            )
            resp.markdown_report = result_formatter.build_markdown_report(resp)
            if save_trace:
                app_db.save_trace(resp)
            return resp

        # ===== PRE-STAGE B: Entity Recognition & Homonym Disambiguation =====
        t_ent = time.perf_counter()
        direction_hint = (
            "reverse"
            if any(k in req.question for k in ("影响", "上游调用", "上游服务", "哪些上游", "反向", "谁调用", "被哪些"))
            else "forward"
        )
        entities = entity_disambiguator.resolve_entities(
            question=req.question,
            schema=schema,
            scope=req.scope,
            selected_entity_ids=opts.selected_entity_ids,
            clarify_on_ambiguity=opts.clarify_on_ambiguity,
            direction_hint=direction_hint,
        )
        ambiguous = [e for e in entities if e.status == "ambiguous"]
        bound_cnt = sum(1 for e in entities if e.status == "bound")
        stages.append(
            StageTrace(
                stage="1b_entity_disambiguation",
                status="warning" if ambiguous else "ok",
                latency_ms=round((time.perf_counter() - t_ent) * 1000, 2),
                summary=(
                    f"发现 {len(ambiguous)} 处同名实体歧义，等待用户确认具体 nodeId"
                    if ambiguous
                    else f"识别并绑定 {bound_cnt} 个真实图谱实体"
                ),
                details={
                    "bound": [
                        {"mention": e.mention, "node_id": e.bound.node_id, "label": e.bound.label}
                        for e in entities
                        if e.status == "bound" and e.bound
                    ],
                    "ambiguous": [e.mention for e in ambiguous],
                },
            )
        )

        # Pause and ask user for disambiguation when interactive mode finds ambiguous homonyms!
        if ambiguous and opts.clarify_on_ambiguity:
            mention_list = "、".join(f"`{e.mention}`（{len(e.candidates)} 个候选）" for e in ambiguous)
            clarification_msg = (
                f"检测到同名代码实体歧义：{mention_list}。请在下方候选证据卡中确认所属类、微服务与源码路径后继续查询，避免误匹配。"
            )
            trace = QueryTrace(
                trace_id=trace_id,
                request_id=request_id,
                schema_hash=schema.schema_hash,
                graph_version=schema.graph_version,
                schema_source=schema.source,
                model_provider=llm_cfg["provider"],
                model_version=llm_cfg["model"],
                execution_mode="awaiting_clarification",
                code_commit=_get_code_commit(),
                total_latency_ms=round((time.perf_counter() - t_start) * 1000, 2),
                stages=stages,
            )
            resp = QueryResponse(
                request_id=request_id,
                trace_id=trace_id,
                question=req.question,
                status=QueryStatus.NEEDS_CLARIFICATION,
                level=req.level,
                summary=clarification_msg,
                clarification_prompt=clarification_msg,
                ambiguous_entities=ambiguous,
                trace=trace,
            )
            resp.markdown_report = result_formatter.build_markdown_report(resp)
            if save_trace:
                app_db.save_trace(resp)
            return resp

        # ===== STAGE 2: Planning, T3 Decomposition, KB RAG Retrieval & Skill Prompt Building =====
        t2 = time.perf_counter()
        plan: QueryPlan = query_decomposer.plan(
            question=req.question,
            requested_level=req.level,
            entities=entities,
            schema=schema,
            max_hops=opts.max_hops,
            use_decomposition=opts.use_decomposition,
        )

        # KB30 two-stage retrieval (TF-IDF + approved LLM semantic rerank)
        retrieved_examples: List[Dict[str, Any]] = []
        retrieval_mode = "disabled"
        if opts.use_kb_rag:
            kb_entries = app_db.list_kb()
            retrieved_examples, retrieval_mode = kb_retriever.retrieve(
                question=req.question,
                entries=kb_entries,
                category_hint=plan.category,
                intent_hint=plan.intent,
                top_k=3,
                use_llm_rerank=opts.use_llm_rerank and has_llm_key,
                provider=opts.provider,
                model=opts.model,
                api_key=opts.api_key,
                base_url=opts.base_url,
            )
        plan.retrieval_mode = retrieval_mode

        sys_prompt, usr_prompt, skill_versions = prompt_builder.build_generation_prompt(
            question=req.question,
            schema=schema,
            plan=plan,
            kb_examples=retrieved_examples,
        )
        plan.selected_skills = [f"{k}@{v}" for k, v in skill_versions.items()]
        stages.append(
            StageTrace(
                stage="2_plan_rag_prompt",
                status="ok",
                latency_ms=round((time.perf_counter() - t2) * 1000, 2),
                summary=(
                    f"意图={plan.intent}({plan.level.value}), 方向={plan.direction}, "
                    f"{'已分解为 ' + str(len(plan.subqueries)) + ' 个子查询' if plan.requires_decomposition else '单查询'}, "
                    f"检索模式={retrieval_mode}(Top{len(retrieved_examples)}), 加载 {len(skill_versions)} 个 Skill"
                ),
                details={
                    "intent": plan.intent,
                    "category": plan.category,
                    "retrieval_mode": retrieval_mode,
                    "top_kb_ids": [e["id"] for e in retrieved_examples],
                    "skills": skill_versions,
                    "subqueries": [sq.subquery_id for sq in plan.subqueries],
                },
            )
        )

        # ===== STAGE 3 + 4 + 5: Generation, Five-Layer Validation, Execution & Bounded Repair =====
        skill_ver_str = ",".join(f"{k}@{v}" for k, v in skill_versions.items())
        execution_mode = f"llm:{llm_cfg['provider']}/{llm_cfg['model']}" if has_llm_key else "hybrid_planner_offline"

        # Decide whether we execute decomposed multi-subqueries or single query
        if plan.requires_decomposition and len(plan.subqueries) > 1:
            candidates, combined_val, execution, sem_check = self._execute_decomposed(
                req=req,
                plan=plan,
                schema=schema,
                kb_examples=retrieved_examples,
                llm_cfg=llm_cfg,
                has_llm_key=has_llm_key,
                skill_ver_str=skill_ver_str,
                stages=stages,
                repairs=repairs,
            )
        else:
            candidates, combined_val, execution, sem_check = self._execute_single(
                req=req,
                plan=plan,
                schema=schema,
                sys_prompt=sys_prompt,
                usr_prompt=usr_prompt,
                llm_cfg=llm_cfg,
                has_llm_key=has_llm_key,
                skill_ver_str=skill_ver_str,
                stages=stages,
                repairs=repairs,
            )

        primary_cypher = candidates[0].query if candidates else ""
        primary_params = candidates[0].params if candidates else {}

        # Determine final QueryStatus
        if combined_val and not combined_val.passed:
            final_status = QueryStatus.REJECTED
        elif execution.error:
            final_status = QueryStatus.TIMEOUT if "timeout" in execution.error.lower() else QueryStatus.ERROR
        elif execution.total_records == 0:
            final_status = QueryStatus.EMPTY
        else:
            final_status = QueryStatus.SUCCESS

        # ===== STAGE 6: Result Formatter & Evidence-Grounded Summary =====
        t6 = time.perf_counter()
        if final_status == QueryStatus.REJECTED and combined_val:
            reasons = "; ".join(l.message for l in combined_val.layers if not l.passed)
            summary = f"⛔ 安全与 Schema 校验未通过（{combined_val.violation_category}）：{reasons}"
        else:
            summary = result_formatter.format_summary(
                question=req.question,
                plan=plan,
                execution=execution,
                provider=opts.provider,
                model=opts.model,
                api_key=opts.api_key if (has_llm_key and opts.use_llm_summary) else "__OFFLINE__",
                base_url=opts.base_url,
                allow_llm=has_llm_key and opts.use_llm_summary,
            )
            if final_status == QueryStatus.EMPTY and sem_check and sem_check.diagnosis:
                summary += f"\n\n**空结果诊断**：{sem_check.diagnosis}"

        stages.append(
            StageTrace(
                stage="6_result_formatter",
                status="ok",
                latency_ms=round((time.perf_counter() - t6) * 1000, 2),
                summary=f"输出自然语言总结与拓扑图数据（{len(execution.nodes)}节点 / {len(execution.edges)}边 / {len(execution.paths)}条路径）",
            )
        )

        total_ms = round((time.perf_counter() - t_start) * 1000, 2)
        trace = QueryTrace(
            trace_id=trace_id,
            request_id=request_id,
            schema_hash=schema.schema_hash,
            graph_version=schema.graph_version,
            schema_source=schema.source,
            skill_versions=skill_versions,
            kb_version=f"kb-{len(app_db.list_kb())}",
            model_provider=llm_cfg["provider"],
            model_version=llm_cfg["model"],
            execution_mode=execution_mode,
            code_commit=_get_code_commit(),
            total_latency_ms=total_ms,
            retrieved_examples=[
                {"id": e["id"], "category": e["category"], "question": e["question"], "score": e.get("tfidf_score"), "rerank_mode": e.get("rerank_mode")}
                for e in retrieved_examples
            ],
            stages=stages,
            repairs=repairs,
            semantic_check=sem_check,
        )

        resp = QueryResponse(
            request_id=request_id,
            trace_id=trace_id,
            question=req.question,
            status=final_status,
            level=plan.level,
            plan=plan,
            cypher_candidates=candidates,
            primary_cypher=primary_cypher,
            primary_params=primary_params,
            validation=combined_val,
            execution=execution,
            summary=summary,
            trace=trace,
        )
        resp.markdown_report = result_formatter.build_markdown_report(resp)
        if save_trace:
            app_db.save_trace(resp)
        return resp

    def _execute_single(
        self,
        req: QueryRequest,
        plan: QueryPlan,
        schema,
        sys_prompt: str,
        usr_prompt: str,
        llm_cfg: Dict[str, str],
        has_llm_key: bool,
        skill_ver_str: str,
        stages: List[StageTrace],
        repairs: List[RepairAttempt],
    ) -> Tuple[List[CypherCandidate], ValidationReport, ExecutionResult, Optional[SemanticCheckResult]]:
        opts = req.options
        t3 = time.perf_counter()
        llm_usage: Dict[str, Any] = {}

        if has_llm_key:
            try:
                gen, llm_usage, _ = llm_client.generate_cypher(
                    system_prompt=sys_prompt,
                    user_prompt=usr_prompt,
                    provider=opts.provider,
                    model=opts.model,
                    api_key=opts.api_key,
                    base_url=opts.base_url,
                )
                cand = CypherCandidate(
                    query=gen["cypher"],
                    params=gen.get("params") or {},
                    schema_hash=schema.schema_hash,
                    skill_version=skill_ver_str,
                    model_used=f"{llm_cfg['provider']}/{llm_cfg['model']}",
                    generation_mode="llm",
                    reasoning=gen.get("reasoning"),
                )
            except Exception as exc:
                logger.warning("LLM generation failed (%s)", exc)
                if opts.allow_planner_fallback and plan.subqueries and plan.subqueries[0].cypher:
                    sq = plan.subqueries[0]
                    cand = CypherCandidate(
                        query=sq.cypher,
                        params=sq.params,
                        schema_hash=schema.schema_hash,
                        skill_version=skill_ver_str,
                        model_used=f"hybrid_planner (LLM error fallback: {exc})",
                        generation_mode="hybrid_planner",
                        reasoning=sq.description,
                    )
                else:
                    val = ValidationReport(
                        passed=False,
                        violation_category="LLM_GENERATION_FAILED",
                        layers=[SafetyLayerResult(layer="L0_llm_generation", passed=False, message=str(exc))],
                    )
                    return [], val, ExecutionResult(complete=False, error=str(exc)), None
        elif plan.subqueries and plan.subqueries[0].cypher:
            sq = plan.subqueries[0]
            cand = CypherCandidate(
                query=sq.cypher,
                params=sq.params,
                schema_hash=schema.schema_hash,
                skill_version=skill_ver_str,
                model_used="hybrid_planner (schema-aware offline engine)",
                generation_mode="hybrid_planner",
                reasoning=sq.description,
            )
        else:
            # No LLM key and unmatched open-ended query: provide explicit actionable message
            val = ValidationReport(
                passed=False,
                violation_category="LLM_KEY_REQUIRED_FOR_OPEN_QUERY",
                layers=[
                    SafetyLayerResult(
                        layer="L0_model_availability",
                        passed=False,
                        message="该开放式问题需调用已批准大模型（DeepSeek / GLM / Kimi）生成 Cypher，请在右上角「模型配置」或 .env 中配置 API Key。",
                    )
                ],
            )
            return [], val, ExecutionResult(complete=False, error="未配置 DeepSeek/GLM/Kimi API Key"), None

        stages.append(
            StageTrace(
                stage="3_cypher_generation",
                status="ok",
                latency_ms=round((time.perf_counter() - t3) * 1000, 2),
                summary=f"生成 Cypher 候选（模式={cand.generation_mode}, 模型={cand.model_used}）",
                details={"usage": llm_usage, "reasoning": cand.reasoning},
            )
        )

        # Stage 4 + 5 + Bounded Repair Loop (<= settings.max_repair_rounds)
        current_cand = cand
        all_candidates = [cand]
        last_val: ValidationReport
        last_exec = ExecutionResult()
        last_sem: Optional[SemanticCheckResult] = None

        for attempt in range(settings.max_repair_rounds + 1):
            t4 = time.perf_counter()
            val = cypher_validator.validate(
                current_cand.query,
                current_cand.params,
                schema,
                run_explain=True,
            )
            last_val = val
            stages.append(
                StageTrace(
                    stage=f"4_validator_r{attempt}",
                    status="ok" if val.passed else "failed",
                    latency_ms=round((time.perf_counter() - t4) * 1000, 2),
                    summary=(
                        f"五层安全与 EXPLAIN 校验全部通过（{len(val.layers)}层）"
                        if val.passed
                        else f"校验拒绝 ({val.violation_category}): " + "; ".join(l.message for l in val.layers if not l.passed)
                    ),
                )
            )

            # Never repair explicit write/procedure security attacks — block immediately!
            if not val.passed and val.violation_category in ("WRITE_OR_PROCEDURE_BLOCKED", "INPUT_OR_BOUNDS_VIOLATION"):
                return all_candidates, val, ExecutionResult(complete=False, error="Blocked by read-only safety guard"), None

            if val.passed:
                t5 = time.perf_counter()
                exe = cypher_executor.execute(val.sanitized_query or current_cand.query, current_cand.params)
                last_exec = exe
                stages.append(
                    StageTrace(
                        stage=f"5_executor_r{attempt}",
                        status="ok" if not exe.error else "failed",
                        latency_ms=round((time.perf_counter() - t5) * 1000, 2),
                        summary=(
                            f"只读执行完成：{exe.total_records} 条记录, {len(exe.nodes)} 节点, {len(exe.edges)} 边 ({exe.elapsed_ms}ms)"
                            if not exe.error
                            else f"执行错误: {exe.error}"
                        ),
                    )
                )

                if opts.use_semantic_check:
                    sem = semantic_checker.check(req.question, current_cand.query, plan, exe)
                    last_sem = sem
                    if sem.passed:
                        return all_candidates, val, exe, sem
                    feedback = "; ".join(sem.issues) + (f" 建议: {sem.suggested_fix}" if sem.suggested_fix else "")
                else:
                    return all_candidates, val, exe, None
            else:
                feedback = "; ".join(l.message for l in val.layers if not l.passed)

            # Attempt bounded repair ONLY if LLM key is available and rounds remain
            if attempt < settings.max_repair_rounds and has_llm_key:
                rep_sys, rep_usr = prompt_builder.build_repair_prompt(
                    question=req.question,
                    failed_cypher=current_cand.query,
                    failed_params=current_cand.params,
                    error_feedback=feedback,
                    schema=schema,
                    plan=plan,
                )
                try:
                    rep_gen, _, _ = llm_client.generate_cypher(
                        system_prompt=rep_sys,
                        user_prompt=rep_usr,
                        provider=opts.provider,
                        model=opts.model,
                        api_key=opts.api_key,
                        base_url=opts.base_url,
                    )
                    repaired_cand = CypherCandidate(
                        query=rep_gen["cypher"],
                        params=rep_gen.get("params") or current_cand.params,
                        schema_hash=schema.schema_hash,
                        skill_version=skill_ver_str,
                        model_used=f"{llm_cfg['provider']}/{llm_cfg['model']}",
                        generation_mode="repaired",
                        reasoning=rep_gen.get("reasoning"),
                    )
                    repairs.append(
                        RepairAttempt(
                            round=attempt + 1,
                            trigger_reason=feedback,
                            original_cypher=current_cand.query,
                            repaired_cypher=repaired_cand.query,
                            validation_passed=True,
                        )
                    )
                    current_cand = repaired_cand
                    all_candidates.insert(0, repaired_cand)
                    continue
                except Exception as exc:
                    repairs.append(
                        RepairAttempt(
                            round=attempt + 1,
                            trigger_reason=feedback,
                            original_cypher=current_cand.query,
                            repaired_cypher="",
                            validation_passed=False,
                            error=str(exc),
                        )
                    )
            break

        return all_candidates, last_val, last_exec, last_sem

    def _execute_decomposed(
        self,
        req: QueryRequest,
        plan: QueryPlan,
        schema,
        kb_examples: List[Dict[str, Any]],
        llm_cfg: Dict[str, str],
        has_llm_key: bool,
        skill_ver_str: str,
        stages: List[StageTrace],
        repairs: List[RepairAttempt],
    ) -> Tuple[List[CypherCandidate], ValidationReport, ExecutionResult, Optional[SemanticCheckResult]]:
        """
        T3 Complex Query Decomposition & Merge Engine:
        Independently validates and executes each decomposed subquery (e.g., RPC + MQ, or Local + Cross-Service Impact),
        then correlates, deduplicates by stable nodeId + communication mechanism, and merges topology graphs.
        """
        t_dec = time.perf_counter()
        candidates: List[CypherCandidate] = []
        merged_records: List[Dict[str, Any]] = []
        seen_record_keys: set = set()
        nodes_map: Dict[str, GraphNode] = {}
        edges_map: Dict[str, GraphEdge] = {}
        merged_paths: List[PathEvidence] = []
        all_layers: List[SafetyLayerResult] = []
        total_exec_ms = 0.0
        any_failed = False
        all_complete = True

        def _run_one_subquery(sq):
            sq_repairs: List[RepairAttempt] = []
            cypher_text = ""
            params: Dict[str, Any] = {}
            gen_mode: str = "llm" if has_llm_key else "hybrid_planner"
            model_used = (
                f"{llm_cfg['provider']}/{llm_cfg['model']}"
                if has_llm_key
                else "hybrid_planner (T3 decomposed)"
            )
            reasoning = sq.description

            if has_llm_key:
                sub_instr = (
                    f"{sq.description}\n"
                    f"通信机制标签：'{sq.mechanism}'，遍历方向：{sq.direction}，跳数上限：{sq.max_hops}。\n"
                    f"请在 RETURN 中显式包含机制列（如 '{sq.mechanism}' AS mechanism）与目标实体的稳定 nodeId、名称及路径/节点对象以便合并去重。"
                )
                s_p, u_p, _ = prompt_builder.build_generation_prompt(
                    question=req.question,
                    schema=schema,
                    plan=plan,
                    kb_examples=kb_examples,
                    subquery_instruction=sub_instr,
                )
                try:
                    gen, _, _ = llm_client.generate_cypher(
                        system_prompt=s_p,
                        user_prompt=u_p,
                        provider=req.options.provider,
                        model=req.options.model,
                        api_key=req.options.api_key,
                        base_url=req.options.base_url,
                    )
                    cypher_text = gen["cypher"]
                    params = gen.get("params") or {}
                    reasoning = gen.get("reasoning") or sq.description
                except Exception as exc:
                    logger.warning("LLM decomposed subquery %s failed (%s)", sq.subquery_id, exc)
                    if req.options.allow_planner_fallback and sq.cypher:
                        cypher_text = sq.cypher
                        params = dict(sq.params)
                        gen_mode = "hybrid_planner"
                        model_used = f"hybrid_planner (LLM fallback: {exc})"
                    else:
                        return (
                            sq,
                            CypherCandidate(
                                query="",
                                params={},
                                schema_hash=schema.schema_hash,
                                skill_version=skill_ver_str,
                                model_used=model_used,
                                generation_mode="llm",
                                reasoning=str(exc),
                                subquery_id=sq.subquery_id,
                                mechanism=sq.mechanism,
                            ),
                            ValidationReport(
                                passed=False,
                                violation_category="LLM_GENERATION_FAILED",
                                layers=[SafetyLayerResult(layer="L0_llm_generation", passed=False, message=str(exc))],
                            ),
                            ExecutionResult(complete=False, error=str(exc)),
                            sq_repairs,
                        )
            else:
                cypher_text = sq.cypher or ""
                params = dict(sq.params)

            val = None
            exe = ExecutionResult()
            for attempt in range(settings.max_repair_rounds + 1):
                val = cypher_validator.validate(cypher_text, params, schema, run_explain=True)
                if val.passed:
                    exe = cypher_executor.execute(
                        val.sanitized_query or cypher_text,
                        params,
                        mechanism_hint=sq.mechanism,
                    )
                    if not exe.error:
                        sub_sem = (
                            semantic_checker.check(req.question, cypher_text, plan, exe)
                            if req.options.use_semantic_check
                            else None
                        )
                        if not sub_sem or sub_sem.passed:
                            break
                        feedback = "; ".join(sub_sem.issues) + (f" 建议: {sub_sem.suggested_fix}" if sub_sem.suggested_fix else "")
                    else:
                        feedback = f"执行报错: {exe.error}"
                else:
                    if val.violation_category in ("WRITE_OR_PROCEDURE_BLOCKED", "INPUT_OR_BOUNDS_VIOLATION"):
                        break
                    feedback = "; ".join(l.message for l in val.layers if not l.passed)

                if attempt < settings.max_repair_rounds and has_llm_key:
                    rep_sys, rep_usr = prompt_builder.build_repair_prompt(
                        question=f"{req.question}（子查询要求：{sq.description}）",
                        failed_cypher=cypher_text,
                        failed_params=params,
                        error_feedback=feedback,
                        schema=schema,
                        plan=plan,
                    )
                    try:
                        rep_gen, _, _ = llm_client.generate_cypher(
                            system_prompt=rep_sys,
                            user_prompt=rep_usr,
                            provider=req.options.provider,
                            model=req.options.model,
                            api_key=req.options.api_key,
                            base_url=req.options.base_url,
                        )
                        orig_q = cypher_text
                        cypher_text = rep_gen["cypher"]
                        params = rep_gen.get("params") or params
                        gen_mode = "repaired"
                        sq_repairs.append(
                            RepairAttempt(
                                round=attempt + 1,
                                trigger_reason=f"[{sq.subquery_id}] {feedback}",
                                original_cypher=orig_q,
                                repaired_cypher=cypher_text,
                                validation_passed=True,
                            )
                        )
                        continue
                    except Exception as exc:
                        sq_repairs.append(
                            RepairAttempt(
                                round=attempt + 1,
                                trigger_reason=f"[{sq.subquery_id}] {feedback}",
                                original_cypher=cypher_text,
                                repaired_cypher="",
                                validation_passed=False,
                                error=str(exc),
                            )
                        )
                break

            cand = CypherCandidate(
                query=cypher_text,
                params=params,
                schema_hash=schema.schema_hash,
                skill_version=skill_ver_str,
                model_used=model_used,
                generation_mode=gen_mode,  # type: ignore[arg-type]
                reasoning=reasoning,
                subquery_id=sq.subquery_id,
                mechanism=sq.mechanism,
            )
            assert val is not None
            return (sq, cand, val, exe, sq_repairs)

        # Execute decomposed subqueries IN PARALLEL to cut complex T3 latency nearly in half!
        with ThreadPoolExecutor(max_workers=min(4, len(plan.subqueries))) as pool:
            sq_outcomes = list(pool.map(_run_one_subquery, plan.subqueries))

        for sq, cand, val, exe, sq_repairs in sq_outcomes:
            candidates.append(cand)
            repairs.extend(sq_repairs)
            for lyr in val.layers:
                all_layers.append(
                    SafetyLayerResult(
                        layer=f"{sq.subquery_id}:{lyr.layer}",
                        passed=lyr.passed,
                        message=lyr.message,
                        violations=lyr.violations,
                        details=lyr.details,
                    )
                )
            if not val.passed:
                any_failed = True
                continue

            total_exec_ms += exe.elapsed_ms
            if not exe.complete:
                all_complete = False
            if exe.error:
                any_failed = True
                continue

            # Correlate & deduplicate records without losing rows that use semantic column aliases
            for rec in exe.records:
                rec_copy = dict(rec)
                rec_copy.setdefault("mechanism", sq.mechanism)
                rec_copy["_subquery"] = sq.subquery_id

                # Normalize canonical join fields if present under semantic aliases
                if "api_id" not in rec_copy:
                    for alias in ("upstream_api_id", "affected_api_id", "uapi_id"):
                        if rec_copy.get(alias):
                            rec_copy["api_id"] = rec_copy[alias]
                            break
                if "target_id" not in rec_copy:
                    for alias in ("service_id", "peer_service_id", "target_service_id"):
                        if rec_copy.get(alias):
                            rec_copy["target_id"] = rec_copy[alias]
                            break

                # Build canonical deduplication key from join keys + scalar identifiers
                key_parts = []
                for k in plan.join_keys + ["channel", "affected_api", "upstream_api", "target_service", "peer_service", "upstream_service"]:
                    v = rec_copy.get(k)
                    if v is not None and not isinstance(v, (dict, list)) and str(v) != "":
                        key_parts.append(f"{k}={v}")
                if not key_parts:
                    # Fallback: hash all scalar fields in the row so distinct rows never collapse to ()
                    key_parts = sorted(
                        f"{k}={v}"
                        for k, v in rec_copy.items()
                        if not k.startswith("_") and not isinstance(v, (dict, list)) and v is not None
                    )
                dedup_key = tuple(key_parts)
                if dedup_key not in seen_record_keys:
                    seen_record_keys.add(dedup_key)
                    merged_records.append(rec_copy)

            for n in exe.nodes:
                nodes_map.setdefault(n.id, n)
            for e in exe.edges:
                edges_map.setdefault(e.id, e)
            merged_paths.extend(exe.paths)

        all_nodes = list(nodes_map.values())
        all_edges = list(edges_map.values())
        tot_n, tot_e = len(all_nodes), len(all_edges)
        truncated = False
        if tot_n > settings.display_max_nodes:
            all_nodes = all_nodes[: settings.display_max_nodes]
            kept = {n.id for n in all_nodes}
            all_edges = [e for e in all_edges if e.source in kept and e.target in kept]
            truncated = True
        if len(all_edges) > settings.display_max_edges:
            all_edges = all_edges[: settings.display_max_edges]
            truncated = True

        merged_exec = ExecutionResult(
            records=merged_records,
            total_records=len(merged_records),
            nodes=all_nodes,
            edges=all_edges,
            paths=merged_paths[:200],
            display_truncated=truncated,
            total_nodes_before_truncation=tot_n,
            total_edges_before_truncation=tot_e,
            elapsed_ms=round(total_exec_ms, 2),
            complete=all_complete,
        )
        combined_val = ValidationReport(
            passed=not any_failed,
            layers=all_layers,
            violation_category="SUBQUERY_VALIDATION_FAILED" if any_failed else None,
            sanitized_query="\n\n-- SUBQUERY SPLIT --\n\n".join(c.query for c in candidates),
        )
        stages.append(
            StageTrace(
                stage="3_4_5_decomposed_exec_merge",
                status="ok" if not any_failed else "warning",
                latency_ms=round((time.perf_counter() - t_dec) * 1000, 2),
                summary=(
                    f"完成 {len(plan.subqueries)} 个子查询独立校验执行与关联去重合并："
                    f"{merged_exec.total_records} 条去重记录, {len(merged_exec.nodes)} 节点, {len(merged_exec.edges)} 边"
                ),
            )
        )
        sem = (
            semantic_checker.check(req.question, combined_val.sanitized_query or "", plan, merged_exec)
            if req.options.use_semantic_check
            else None
        )
        return candidates, combined_val, merged_exec, sem


orchestrator = Text2CypherOrchestrator()
