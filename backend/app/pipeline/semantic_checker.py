from __future__ import annotations

import re
from typing import List

from app.models.schemas import ExecutionResult, QueryPlan, SemanticCheckResult


class SemanticChecker:
    """
    Layer 5: Result Semantic Consistency Checker & Empty-Result Root-Cause Diagnoser.
    Detects:
    - MQ routing-key omission when joining Exchange -> Queue (prevents cross-queue false paths)
    - Wrong traversal direction vs planned intent
    - Missing DISTINCT on entity/service aggregation queries
    - Empty results with concrete root-cause diagnosis (rather than blindly rewriting to non-empty)
    """

    def check(
        self,
        question: str,
        cypher: str,
        plan: QueryPlan,
        execution: ExecutionResult,
    ) -> SemanticCheckResult:
        issues: List[str] = []
        suggestions: List[str] = []
        upper = cypher.upper()

        # 1. Execution failure check
        if execution.error:
            return SemanticCheckResult(
                passed=False,
                issues=[f"执行阶段报错: {execution.error}"],
                diagnosis="Cypher 在 Neo4j 执行期出现异常或超时",
                suggested_fix="请检查路径跳数界限、属性类型与参数绑定后重写查询。",
            )

        # 2. MQ routing-key join guard: if query traverses both 发布至 and 路由至 via 消息交换机, MUST match 路由键
        if "发布至" in cypher and "路由至" in cypher and "路由键" not in cypher:
            issues.append("MQ 发布至与路由至阶段未关联 `路由键`，因实库共享 (default) 交换机会产生跨队列伪路径")
            suggestions.append("添加 WHERE r1.`路由键` = r2.`路由键` 条件以精准匹配路由规则。")

        # 3. Microservice membership trap: (:类)-[:归属于]->(:微服务) does not exist in real schema
        if re.search(r":`?类`?[^\n]*-\[:`?归属于`?\]->\([^\)]*:`?微服务`?", cypher):
            issues.append("实库中 `类` 与 `微服务` 之间不存在 `归属于` 边，必须改用 `serviceContexts` 或 `moduleId` 过滤")
            suggestions.append("将 `(c:类)-[:归属于]->(s:微服务)` 替换为 `WHERE $service IN c.serviceContexts`。")

        if re.search(r":`?方法`?[^\n]*-\[:`?归属于`?\]->\([^\)]*:`?微服务`?", cypher):
            issues.append("实库中 `方法` 与 `微服务` 之间不存在直接 `归属于` 边，必须使用 `serviceContexts` 或 `moduleId`")
            suggestions.append("使用 `WHERE $service IN m.serviceContexts` 或 `m.moduleId = $service`。")

        # 4. Aggregation deduplication check
        if plan.expected_shape == "count" and "COUNT(" in upper and "DISTINCT" not in upper and "*" in cypher:
            issues.append("多跳路径计数未使用 DISTINCT，可能按路径条数误计实体数量")
            suggestions.append("将 count(...) 改为 count(DISTINCT x.nodeId)。")

        # 5. Empty result diagnosis (never force fake non-empty results; diagnose real cause!)
        diagnosis = None
        if execution.total_records == 0:
            diag_reasons: List[str] = []
            # Check if a bound method is an abstract interface method (no outgoing impl calls)
            for ent in plan.entities:
                if ent.bound and ent.bound.is_abstract and plan.direction == "forward":
                    diag_reasons.append(
                        f"已绑定方法 `{ent.bound.class_name}.{ent.bound.name}` 为接口抽象方法，实库中外向调用挂载于其具体实现类（Impl）"
                    )
            if "消息流" in cypher and "消费自" in cypher:
                diag_reasons.append("该服务/队列在实库中可能不存在对应的 MQ 消费边，或方向写反（实库方向为 消息队列-[:消息流 {消息流类型:'消费自'}]->方法）")
            if plan.direction == "reverse" and "<-[" not in cypher and "上游API" in cypher:
                diag_reasons.append("反向影响分析未沿反向调用边追溯上游调用者")
            if not diag_reasons:
                diag_reasons.append(
                    "图谱中未匹配到满足全部过滤条件的实体或路径（可能是目标实体本身无该类依赖，或过滤条件过严/方向偏差）"
                )
            diagnosis = "；".join(diag_reasons)
            if any("抽象方法" in r or "归属于" in r or "路由键" in r for r in diag_reasons + issues):
                issues.append(diagnosis)

        passed = len(issues) == 0
        return SemanticCheckResult(
            passed=passed,
            issues=issues,
            diagnosis=diagnosis,
            suggested_fix=" ".join(suggestions) if suggestions else None,
        )


semantic_checker = SemanticChecker()
