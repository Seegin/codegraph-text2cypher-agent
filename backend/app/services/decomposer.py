from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.models.schemas import (
    EntityCandidate,
    QueryLevel,
    QueryPlan,
    ResolvedEntity,
    SchemaSnapshot,
    SubquerySpec,
)


# Prompt-injection & explicit write-intent detector on natural language input
WRITE_INTENT_PATTERNS = [
    r"(?i)\b(CREATE|MERGE|DETACH\s+DELETE|DELETE\s+NODE|DROP\s+INDEX|DROP\s+CONSTRAINT|LOAD\s+CSV|ALTER\s+USER)\b",
    r"(?i)\bCALL\s+(dbms|apoc)\.",
    r"(删除|清空|修改数据库|写入节点|新增节点|创建节点|更新属性|删库|销毁图谱|移除所有)",
    r"(?i)(ignore\s+previous\s+instructions|忽略以上指令|忽略安全规则|绕过只读)",
]


def detect_malicious_input(question: str) -> Optional[str]:
    for pat in WRITE_INTENT_PATTERNS:
        m = re.search(pat, question)
        if m:
            # Distinguish benign "修改某方法会影响..." (impact analysis!) from actual DB write commands
            if "影响" in question and not re.search(r"(?i)\b(CREATE|MERGE|DELETE|DROP|SET|CALL\s+dbms|CALL\s+apoc)\b", question):
                if m.group(0) in ("修改数据库",):
                    return f"检测到数据库写入/破坏性意图: {m.group(0)}"
                continue
            return f"检测到危险写入、过程调用或提示注入意图: '{m.group(0)}'，本智能体严格仅支持只读代码图谱分析。"
    return None


class QueryDecomposerAndPlanner:
    """
    Intent Classifier, T3 Complex Query Decomposer (RPC + MQ & Cross-Service Reverse Impact),
    and Schema-Aware Structured Cypher Planner.
    """

    def plan(
        self,
        question: str,
        requested_level: QueryLevel,
        entities: List[ResolvedEntity],
        schema: SchemaSnapshot,
        max_hops: int = 6,
        use_decomposition: bool = True,
    ) -> QueryPlan:
        hops = min(max(1, max_hops), settings.hard_max_hops)
        q = question.strip()
        q_low = q.lower()

        svcs = [e.bound for e in entities if e.entity_type == "微服务" and e.bound]
        methods = [e.bound for e in entities if e.entity_type == "方法" and e.bound]
        classes = [e.bound for e in entities if e.entity_type == "类" and e.bound]
        queues = [e.bound for e in entities if e.entity_type == "消息队列" and e.bound]

        has_mq = any(k in q_low for k in ("mq", "消息", "队列", "交换机", "rabbitmq", "发布", "消费", "路由"))
        has_rpc = any(k in q_low for k in ("rpc", "rest", "http", "同步", "跨服务依赖", "全量依赖", "综合依赖"))
        has_impact = any(
            k in q
            for k in (
                "影响",
                "上游调用",
                "上游服务",
                "上游微服务",
                "哪些上游",
                "谁调用了",
                "哪些服务调用了",
                "哪些微服务调用了",
                "被哪些",
                "反向",
            )
        )
        has_downstream = any(k in q for k in ("下游", "调用了哪些", "依赖了哪些", "依赖哪些", "调用哪些"))
        has_count = any(k in q for k in ("多少", "数量", "统计", "总数", "几个", "排名", "top", "Top", "前"))
        has_api = any(k in q_low for k in ("api", "接口", "端点", "路由", "url", "get", "post", "put", "delete"))

        # --- 1. T3 Hybrid RPC + MQ Cross-Service Dependency (Decomposed!) ---
        if svcs and (
            (has_mq and has_rpc)
            or ("跨服务依赖" in q)
            or ("同步" in q and "异步" in q)
            or ("综合依赖" in q)
        ):
            svc_name = svcs[0].name
            is_bi = any(k in q for k in ("入向", "双向", "全部跨服务", "来源", "入站"))
            direction = "both" if is_bi else "forward"
            subqueries: List[SubquerySpec] = []
            if use_decomposition:
                subqueries = self._build_hybrid_rpc_mq_subqueries(svc_name, bidirectional=is_bi)
            return QueryPlan(
                intent="hybrid_rpc_mq_bidirectional" if is_bi else "hybrid_rpc_mq_outbound",
                category="REST+MQ综合依赖",
                level=QueryLevel.COMPLEX,
                direction=direction,
                max_hops=hops,
                mechanisms=["RPC", "MQ"],
                expected_shape="hybrid_dependencies",
                entities=entities,
                requires_decomposition=use_decomposition,
                subqueries=subqueries,
                join_keys=["target_id", "mechanism"],
            )

        # --- 2. T3 MQ Three-Stage Message Chains ---
        if has_mq:
            if len(svcs) >= 2:
                return QueryPlan(
                    intent="mq_between_services",
                    category="MQ发布者与完整链路",
                    level=QueryLevel.COMPLEX,
                    direction="forward",
                    max_hops=3,
                    mechanisms=["MQ"],
                    expected_shape="mq_chain",
                    entities=entities,
                    subqueries=self._build_mq_between_services(svcs[0].name, svcs[1].name),
                )
            if queues:
                return QueryPlan(
                    intent="mq_queue_full_chain",
                    category="MQ发布者与完整链路",
                    level=QueryLevel.COMPLEX,
                    direction="both",
                    max_hops=3,
                    mechanisms=["MQ"],
                    expected_shape="mq_chain",
                    entities=entities,
                    subqueries=self._build_mq_queue_chain(queues[0].name),
                )
            if svcs and any(k in q for k in ("发布", "发送", "链路", "通信", "消息流")):
                is_consume_only = ("消费" in q) and ("发布" not in q) and ("发送" not in q) and ("链路" not in q)
                intent = "mq_service_consumer_chain" if is_consume_only else "mq_service_publish_chain"
                return QueryPlan(
                    intent=intent,
                    category="MQ发布者与完整链路",
                    level=QueryLevel.COMPLEX,
                    direction="reverse" if is_consume_only else "forward",
                    max_hops=3,
                    mechanisms=["MQ"],
                    expected_shape="mq_chain",
                    entities=entities,
                    subqueries=self._build_mq_service_chain(svcs[0].name, consume_only=is_consume_only),
                )
            if "交换机" in q and "队列" not in q and "发布" not in q and "消费" not in q:
                return QueryPlan(
                    intent="list_mq_exchanges",
                    category="单节点查询",
                    level=QueryLevel.SIMPLE,
                    direction="none",
                    mechanisms=["MQ"],
                    expected_shape="entities",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_exchanges",
                            mechanism="MQ",
                            description="查询所有消息交换机及其归属微服务",
                            direction="none",
                            cypher="MATCH (e:`消息交换机`)-[r:`归属于`]->(s:`微服务`) RETURN e.nodeId AS exchange_id, e.`交换机名称` AS exchange_name, e.`交换机类型` AS exchange_type, collect(DISTINCT s.`服务名称`) AS services, e",
                            params={},
                        )
                    ],
                )
            if any(k in q for k in ("消费", "监听", "接收")) and not any(k in q for k in ("发布", "发送", "从哪来", "完整")):
                return QueryPlan(
                    intent="mq_consumers",
                    category="MQ发布者与完整链路",
                    level=QueryLevel.MEDIUM,
                    direction="forward",
                    mechanisms=["MQ"],
                    expected_shape="mq_chain",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_mq_sub",
                            mechanism="MQ",
                            description="查询所有 MQ 消费队列及消费方法",
                            direction="forward",
                            cypher="MATCH (q:`消息队列`)-[r:`消息流` {`消息流类型`: '消费自'}]->(sub:`方法`) RETURN q.nodeId AS queue_id, q.`队列名称` AS queue_name, sub.nodeId AS consumer_method_id, sub.`全限定名` AS consumer_method, sub.serviceContexts AS consumer_services, q, r, sub ORDER BY queue_name",
                            params={},
                        )
                    ],
                )
            if any(k in q for k in ("发布", "发送", "生产者")) and not any(k in q for k in ("消费", "完整", "链路", "到哪去")):
                return QueryPlan(
                    intent="mq_publishers",
                    category="MQ发布者与完整链路",
                    level=QueryLevel.MEDIUM,
                    direction="forward",
                    mechanisms=["MQ"],
                    expected_shape="mq_chain",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_mq_pub",
                            mechanism="MQ",
                            description="查询所有 MQ 发布方法与路由键",
                            direction="forward",
                            cypher="MATCH (pub:`方法`)-[r:`消息流` {`消息流类型`: '发布至'}]->(ex:`消息交换机`) RETURN pub.nodeId AS pub_method_id, pub.`全限定名` AS pub_method, pub.serviceContexts AS producer_services, r.`路由键` AS routing_key, ex.`交换机名称` AS exchange_name, pub, r, ex ORDER BY routing_key, pub_method",
                            params={},
                        )
                    ],
                )
            if any(k in q for k in ("列出", "所有消息队列", "有哪些队列", "全部队列")) and "完整" not in q and "链路" not in q:
                return QueryPlan(
                    intent="list_mq_queues",
                    category="单节点查询",
                    level=QueryLevel.SIMPLE,
                    direction="none",
                    mechanisms=["MQ"],
                    expected_shape="entities",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_queues",
                            mechanism="MQ",
                            description="列出全部消息队列及归属微服务",
                            direction="none",
                            cypher="MATCH (q:`消息队列`)-[r:`归属于`]->(s:`微服务`) RETURN q.nodeId AS queue_id, q.`队列名称` AS queue_name, s.`服务名称` AS service_name, q, r, s ORDER BY queue_name, service_name",
                            params={},
                        )
                    ],
                )
            # Full MQ chains overview
            return QueryPlan(
                intent="mq_all_full_chains",
                category="MQ发布者与完整链路",
                level=QueryLevel.COMPLEX,
                direction="forward",
                max_hops=3,
                mechanisms=["MQ"],
                expected_shape="mq_chain",
                entities=entities,
                subqueries=[
                    SubquerySpec(
                        subquery_id="sq_mq_all",
                        mechanism="MQ",
                        description="查询图谱中全部三阶段 MQ 完整发布-路由-消费链路（按路由键关联）",
                        direction="forward",
                        cypher="MATCH (pub:`方法`)-[r1:`消息流` {`消息流类型`: '发布至'}]->(ex:`消息交换机`)-[r2:`消息流` {`消息流类型`: '路由至'}]->(q:`消息队列`)-[r3:`消息流` {`消息流类型`: '消费自'}]->(sub:`方法`) WHERE r1.`路由键` = r2.`路由键` RETURN pub.serviceContexts AS producer_services, pub.`全限定名` AS producer_method, ex.`交换机名称` AS exchange_name, r1.`路由键` AS routing_key, q.`队列名称` AS queue_name, sub.serviceContexts AS consumer_services, sub.`全限定名` AS consumer_method, pub, r1, ex, r2, q, r3, sub ORDER BY routing_key, producer_method",
                        params={},
                    )
                ],
            )

        # --- 3. T3 Reverse Impact Analysis ---
        if has_impact:
            if methods:
                target_m = methods[0]
                want_cross = any(k in q for k in ("上游", "跨服务", "入口", "API", "api", "影响", "调用链"))
                subqueries = self._build_method_reverse_impact_subqueries(target_m, hops, include_cross=want_cross and use_decomposition)
                return QueryPlan(
                    intent="method_reverse_impact",
                    category="反向影响分析",
                    level=QueryLevel.COMPLEX if want_cross else QueryLevel.MEDIUM,
                    direction="reverse",
                    max_hops=hops,
                    mechanisms=["RPC", "IMPACT"],
                    expected_shape="impact",
                    entities=entities,
                    requires_decomposition=len(subqueries) > 1,
                    subqueries=subqueries,
                    join_keys=["api_id"],
                )
            if svcs:
                svc_name = svcs[0].name
                want_apis = any(k in q_low for k in ("入口", "api", "端点", "影响哪些上游"))
                if want_apis:
                    return QueryPlan(
                        intent="service_reverse_affected_upstream_apis",
                        category="反向影响分析",
                        level=QueryLevel.COMPLEX,
                        direction="reverse",
                        max_hops=min(hops, 5),
                        mechanisms=["RPC", "IMPACT"],
                        expected_shape="impact",
                        entities=entities,
                        subqueries=[
                            SubquerySpec(
                                subquery_id="sq_svc_rev_apis",
                                mechanism="IMPACT",
                                description=f"反向追踪依赖 {svc_name} 的上游微服务入口 API",
                                direction="reverse",
                                max_hops=min(hops, 5),
                                cypher=(
                                    "MATCH (d:`下游API`)-[rt:`目标服务`]->(svc:`微服务` {`服务名称`: $service}) "
                                    "MATCH (caller:`方法`)-[rc:`调用` {`关系语义`: '通信边界'}]->(d) "
                                    f"MATCH p=(entry:`方法`)-[rs:`调用`*0..{min(hops, 5)}]->(caller) "
                                    "WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) "
                                    "AND ALL(x IN nodes(p) WHERE x:`方法`) "
                                    "MATCH (up_api:`上游API`)-[ru:`归属于`]->(entry) "
                                    "UNWIND up_api.serviceContexts AS upstream_service "
                                    "RETURN DISTINCT upstream_service, up_api.nodeId AS api_id, up_api.caption AS affected_api, "
                                    "d.caption AS called_api, up_api, ru, p, rc, d, rt, svc "
                                    "ORDER BY upstream_service, affected_api"
                                ),
                                params={"service": svc_name},
                            )
                        ],
                    )
                return QueryPlan(
                    intent="service_reverse_callers",
                    category="反向影响分析",
                    level=QueryLevel.MEDIUM,
                    direction="reverse",
                    max_hops=2,
                    mechanisms=["RPC"],
                    expected_shape="paths",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_svc_callers",
                            mechanism="RPC",
                            description=f"查询调用 {svc_name} 的所有上游微服务与方法",
                            direction="reverse",
                            cypher=(
                                "MATCH (m:`方法`)-[r1:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[r2:`目标服务`]->(svc:`微服务` {`服务名称`: $service}) "
                                "UNWIND d.serviceContexts AS caller_service "
                                "RETURN DISTINCT caller_service, m.nodeId AS caller_method_id, m.`全限定名` AS caller_method, "
                                "d.caption AS api, m, r1, d, r2, svc ORDER BY caller_service, api"
                            ),
                            params={"service": svc_name},
                        )
                    ],
                )

        # --- 4. Downstream Service Discovery & Multi-hop RPC ---
        if has_downstream or ("两跳" in q or "多跳" in q or "间接" in q or "端到端" in q):
            if methods:
                m0 = methods[0]
                if "两跳" in q or "间接" in q or "端到端" in q:
                    return QueryPlan(
                        intent="method_multi_hop_rpc",
                        category="下游服务发现",
                        level=QueryLevel.COMPLEX,
                        direction="forward",
                        max_hops=hops,
                        mechanisms=["RPC"],
                        expected_shape="paths",
                        entities=entities,
                        subqueries=[
                            SubquerySpec(
                                subquery_id="sq_m_2hop",
                                mechanism="RPC",
                                description=f"追踪方法 {m0.qualified_name} 经跨服务 API 的两跳下游调用链",
                                direction="forward",
                                max_hops=hops,
                                cypher=(
                                    "MATCH (start:`方法` {nodeId: $node_id}) "
                                    f"MATCH p1=(start)-[r1:`调用`*0..{min(hops, 4)}]->(m1:`方法`) "
                                    "WHERE ALL(rel IN r1 WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p1) WHERE x:`方法`) "
                                    "MATCH (m1)-[rc1:`调用` {`关系语义`: '通信边界'}]->(d1:`下游API`)-[rh:`调用` {`关系语义`: '跨服务HTTP调用'}]->(u1:`上游API`)-[ru1:`归属于`]->(m2:`方法`) "
                                    f"OPTIONAL MATCH p2=(m2)-[r2:`调用`*0..3]->(m2b:`方法`)-[rc2:`调用` {{`关系语义`: '通信边界'}}]->(d2:`下游API`)-[rt2:`目标服务`]->(s2:`微服务`) "
                                    "WHERE ALL(rel IN r2 WHERE rel.`关系语义` IN ['方法调用', '方法实现']) "
                                    "RETURN DISTINCT m2.serviceContexts[0] AS hop1_service, d1.caption AS hop1_api, "
                                    "s2.`服务名称` AS hop2_service, d2.caption AS hop2_api, p1, rc1, d1, rh, u1, ru1, m2, p2, d2, s2 "
                                    "ORDER BY hop1_service, hop2_service"
                                ),
                                params={"node_id": m0.node_id},
                            )
                        ],
                    )
                return QueryPlan(
                    intent="method_downstream_services",
                    category="下游服务发现",
                    level=QueryLevel.MEDIUM,
                    direction="forward",
                    max_hops=hops,
                    mechanisms=["RPC"],
                    expected_shape="paths",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_m_down",
                            mechanism="RPC",
                            description=f"追踪方法 {m0.qualified_name} 调用的下游微服务及下游 API",
                            direction="forward",
                            max_hops=hops,
                            cypher=(
                                "MATCH (entry:`方法` {nodeId: $node_id}) "
                                f"MATCH p=(entry)-[rs:`调用`*0..{hops}]->(m:`方法`) "
                                "WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) "
                                "AND ALL(x IN nodes(p) WHERE x:`方法`) "
                                "MATCH (m)-[rc:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[rt:`目标服务`]->(svc:`微服务`) "
                                "RETURN DISTINCT svc.nodeId AS service_id, svc.`服务名称` AS target_service, "
                                "d.caption AS downstream_api, p, rc, d, rt, svc ORDER BY target_service, downstream_api"
                            ),
                            params={"node_id": m0.node_id},
                        )
                    ],
                )
            if svcs:
                svc_name = svcs[0].name
                if any(k in q for k in ("两跳", "间接", "二级", "二跳")):
                    return QueryPlan(
                        intent="multi_hop_cross_service_rpc",
                        category="下游服务发现",
                        level=QueryLevel.COMPLEX,
                        direction="forward",
                        max_hops=hops,
                        mechanisms=["RPC"],
                        expected_shape="paths",
                        entities=entities,
                        subqueries=[
                            SubquerySpec(
                                subquery_id="sq_svc_2hop",
                                mechanism="RPC",
                                description=f"追踪 {svc_name} 经中间服务两跳可达的下游微服务链",
                                direction="forward",
                                cypher=(
                                    "MATCH (m1:`方法`)-[r1:`调用` {`关系语义`: '通信边界'}]->(d1:`下游API`)-[r2:`调用` {`关系语义`: '跨服务HTTP调用'}]->(u1:`上游API`)-[r3:`归属于`]->(m2:`方法`) "
                                    "WHERE $service IN m1.serviceContexts "
                                    "MATCH p2=(m2)-[rs:`调用`*0..4]->(m2b:`方法`) "
                                    "WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p2) WHERE x:`方法`) "
                                    "MATCH (m2b)-[r4:`调用` {`关系语义`: '通信边界'}]->(d2:`下游API`)-[r5:`目标服务`]->(s3:`微服务`) "
                                    "WHERE NOT ($service IN m2.serviceContexts) AND s3.`服务名称` <> $service "
                                    "RETURN DISTINCT $service AS hop0_service, m2.serviceContexts[0] AS hop1_service, "
                                    "s3.`服务名称` AS hop2_service, d1.caption AS hop1_api, d2.caption AS hop2_api, "
                                    "m1, r1, d1, r2, u1, r3, p2, r4, d2, r5, s3 ORDER BY hop1_service, hop2_service"
                                ),
                                params={"service": svc_name},
                            )
                        ],
                    )
                if has_count:
                    return QueryPlan(
                        intent="service_rpc_downstream_summary",
                        category="下游服务发现",
                        level=QueryLevel.MEDIUM,
                        direction="forward",
                        mechanisms=["RPC"],
                        expected_shape="count",
                        entities=entities,
                        subqueries=[
                            SubquerySpec(
                                subquery_id="sq_svc_down_cnt",
                                mechanism="RPC",
                                description=f"统计 {svc_name} 通过 HTTP 调用各下游微服务的接口数",
                                direction="forward",
                                cypher=(
                                    "MATCH (d:`下游API`)-[r:`目标服务`]->(svc:`微服务`) "
                                    "WHERE $service IN d.serviceContexts "
                                    "RETURN svc.nodeId AS service_id, svc.`服务名称` AS target_service, "
                                    "count(DISTINCT d.nodeId) AS api_call_count ORDER BY api_call_count DESC, target_service ASC"
                                ),
                                params={"service": svc_name},
                            )
                        ],
                    )
                return QueryPlan(
                    intent="service_rpc_downstream",
                    category="下游服务发现",
                    level=QueryLevel.MEDIUM,
                    direction="forward",
                    mechanisms=["RPC"],
                    expected_shape="paths",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_svc_down",
                            mechanism="RPC",
                            description=f"查询 {svc_name} 调用的所有下游微服务及调用 API",
                            direction="forward",
                            cypher=(
                                "MATCH (m:`方法`)-[r1:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[r2:`目标服务`]->(svc:`微服务`) "
                                "WHERE $service IN d.serviceContexts "
                                "RETURN DISTINCT svc.nodeId AS service_id, svc.`服务名称` AS target_service, "
                                "d.caption AS downstream_api, m.`全限定名` AS caller_method, m, r1, d, r2, svc "
                                "ORDER BY target_service, downstream_api"
                            ),
                            params={"service": svc_name},
                        )
                    ],
                )

        # --- 5. Entry API Discovery & HTTP Filtering ---
        http_m = re.search(r"\b(GET|POST|PUT|DELETE|PATCH)\b", q)
        if http_m and has_api:
            http_verb = http_m.group(1)
            if svcs:
                return QueryPlan(
                    intent="filter_service_uapi_by_http",
                    category="属性过滤",
                    level=QueryLevel.SIMPLE,
                    direction="none",
                    expected_shape="entities",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_svc_http",
                            mechanism="STRUCTURAL",
                            description=f"查询 {svcs[0].name} 中 HTTP 方法为 {http_verb} 的上游 API",
                            direction="none",
                            cypher="MATCH (u:`上游API` {`HTTP方法`: $http_method}) WHERE $service IN u.serviceContexts RETURN u.nodeId AS api_id, u.`HTTP方法` AS http_method, u.`API路径` AS path, u.caption AS api, u ORDER BY path",
                            params={"http_method": http_verb, "service": svcs[0].name},
                        )
                    ],
                )
            return QueryPlan(
                intent="filter_uapi_by_http_method",
                category="属性过滤",
                level=QueryLevel.SIMPLE,
                direction="none",
                expected_shape="entities",
                entities=entities,
                subqueries=[
                    SubquerySpec(
                        subquery_id="sq_http",
                        mechanism="STRUCTURAL",
                        description=f"查询所有 HTTP 方法为 {http_verb} 的上游 API",
                        direction="none",
                        cypher="MATCH (u:`上游API` {`HTTP方法`: $http_method}) RETURN u.nodeId AS api_id, u.`HTTP方法` AS http_method, u.`API路径` AS path, u.serviceContexts AS services, u ORDER BY path",
                        params={"http_method": http_verb},
                    )
                ],
            )

        if has_api and classes and not any(k in q for k in ("实现", "继承")):
            return QueryPlan(
                intent="class_entry_apis",
                category="入口API发现",
                level=QueryLevel.SIMPLE,
                direction="forward",
                expected_shape="entities",
                entities=entities,
                subqueries=[
                    SubquerySpec(
                        subquery_id="sq_cls_api",
                        mechanism="STRUCTURAL",
                        description=f"查询类 {classes[0].name} 暴露的上游入口 API",
                        direction="forward",
                        cypher="MATCH (u:`上游API`)-[r1:`归属于`]->(m:`方法`)-[r2:`归属于`]->(c:`类` {nodeId: $class_id}) RETURN u.nodeId AS api_id, u.caption AS api, m.nodeId AS method_id, m.`方法名` AS method_name, c.`全限定名` AS class_fqn, u, r1, m, r2, c ORDER BY api",
                        params={"class_id": classes[0].node_id},
                    )
                ],
            )

        if has_api and svcs and not any(k in q for k in ("接口类", "接口实现", "实现类")):
            if has_count:
                return QueryPlan(
                    intent="count_service_apis",
                    category="入口API发现",
                    level=QueryLevel.SIMPLE,
                    direction="none",
                    expected_shape="count",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_svc_api_cnt",
                            mechanism="STRUCTURAL",
                            description=f"统计 {svcs[0].name} 暴露的上游 API 数量",
                            direction="none",
                            cypher="MATCH (u:`上游API`) WHERE $service IN u.serviceContexts RETURN $service AS service_name, count(DISTINCT u.nodeId) AS api_count",
                            params={"service": svcs[0].name},
                        )
                    ],
                )
            return QueryPlan(
                intent="service_entry_apis",
                category="入口API发现",
                level=QueryLevel.SIMPLE,
                direction="forward",
                expected_shape="entities",
                entities=entities,
                subqueries=[
                    SubquerySpec(
                        subquery_id="sq_svc_apis",
                        mechanism="STRUCTURAL",
                        description=f"列出 {svcs[0].name} 暴露的所有上游入口 API 及其处理方法",
                        direction="forward",
                        cypher="MATCH (u:`上游API`)-[r:`归属于`]->(m:`方法`) WHERE $service IN u.serviceContexts RETURN u.nodeId AS api_id, u.`HTTP方法` AS http_method, u.`API路径` AS path, m.nodeId AS method_id, m.`全限定名` AS handler_method, u, r, m ORDER BY path",
                        params={"service": svcs[0].name},
                    )
                ],
            )

        if has_api and has_count and not svcs and not classes:
            top_m = re.search(r"(?:top|前)\s*(\d+)", q_low)
            limit_n = int(top_m.group(1)) if top_m else 10
            if any(k in q for k in ("各", "每个", "排名", "top", "Top", "前")):
                return QueryPlan(
                    intent="top_services_by_api_count",
                    category="入口API发现",
                    level=QueryLevel.MEDIUM,
                    direction="none",
                    expected_shape="count",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_top_api",
                            mechanism="STRUCTURAL",
                            description=f"按上游 API 数量统计排名前 {limit_n} 的微服务",
                            direction="none",
                            cypher=f"MATCH (u:`上游API`) UNWIND u.serviceContexts AS svc RETURN svc AS service_name, count(DISTINCT u.nodeId) AS api_count ORDER BY api_count DESC, service_name ASC LIMIT {min(limit_n, 41)}",
                            params={},
                        )
                    ],
                )
            return QueryPlan(
                intent="count_all_uapis",
                category="单节点查询",
                level=QueryLevel.SIMPLE,
                direction="none",
                expected_shape="count",
                entities=entities,
                subqueries=[
                    SubquerySpec(
                        subquery_id="sq_all_uapi_cnt",
                        mechanism="STRUCTURAL",
                        description="统计图谱中所有上游 API 总数",
                        direction="none",
                        cypher="MATCH (u:`上游API`) RETURN count(DISTINCT u.nodeId) AS total_upstream_apis",
                        params={},
                    )
                ],
            )

        # --- 6. Structural OOP / Ownership / Filter / Count Queries ---
        if any(k in q for k in ("接口实现", "实现了", "实现类", "实现哪些接口")):
            if classes:
                c0 = classes[0]
                is_iface = bool(c0.is_abstract)
                if is_iface:
                    cy = "MATCH (impl:`类`)-[r:`接口实现`]->(iface:`类` {nodeId: $class_id}) RETURN impl.nodeId AS impl_id, impl.`全限定名` AS impl_class, iface.`全限定名` AS interface_class, impl, r, iface"
                else:
                    cy = "MATCH (impl:`类` {nodeId: $class_id})-[r:`接口实现`]->(iface:`类`) RETURN impl.nodeId AS impl_id, impl.`全限定名` AS impl_class, iface.nodeId AS iface_id, iface.`全限定名` AS interface_class, impl, r, iface"
                return QueryPlan(
                    intent="class_interface_impl",
                    category="单节点查询",
                    level=QueryLevel.SIMPLE,
                    direction="forward",
                    expected_shape="entities",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_impl",
                            mechanism="STRUCTURAL",
                            description=f"查询类 {c0.name} 的接口实现关系",
                            direction="forward",
                            cypher=cy,
                            params={"class_id": c0.node_id},
                        )
                    ],
                )
            if svcs:
                return QueryPlan(
                    intent="service_interface_impls",
                    category="单节点查询",
                    level=QueryLevel.SIMPLE,
                    direction="forward",
                    expected_shape="entities",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_svc_impl",
                            mechanism="STRUCTURAL",
                            description=f"查询 {svcs[0].name} 中的接口实现关系",
                            direction="forward",
                            cypher="MATCH (impl:`类`)-[r:`接口实现`]->(iface:`类`) WHERE $service IN impl.serviceContexts RETURN impl.nodeId AS impl_id, impl.`简名` AS impl_name, iface.nodeId AS iface_id, iface.`简名` AS iface_name, impl, r, iface ORDER BY impl_name",
                            params={"service": svcs[0].name},
                        )
                    ],
                )

        if "继承" in q:
            return QueryPlan(
                intent="class_inheritance",
                category="单节点查询",
                level=QueryLevel.SIMPLE,
                direction="forward",
                expected_shape="entities",
                entities=entities,
                subqueries=[
                    SubquerySpec(
                        subquery_id="sq_inherit",
                        mechanism="STRUCTURAL",
                        description="查询图谱中的类继承关系",
                        direction="forward",
                        cypher="MATCH (sub:`类`)-[r:`继承`]->(sup:`类`) RETURN sub.nodeId AS sub_id, sub.`全限定名` AS subclass, sup.nodeId AS sup_id, sup.`全限定名` AS superclass, sub, r, sup",
                        params={},
                    )
                ],
            )

        # Method belongs to class
        if methods and any(k in q for k in ("属于哪个类", "归属于", "所属类", "属于哪些类", "在哪个类")):
            m0 = methods[0]
            return QueryPlan(
                intent="method_owner_class",
                category="单节点查询",
                level=QueryLevel.SIMPLE,
                direction="forward",
                expected_shape="entities",
                entities=entities,
                subqueries=[
                    SubquerySpec(
                        subquery_id="sq_m_cls",
                        mechanism="STRUCTURAL",
                        description=f"查询方法 {m0.name} 归属的类",
                        direction="forward",
                        cypher="MATCH (m:`方法` {nodeId: $node_id})-[r:`归属于`]->(c:`类`) RETURN m.nodeId AS method_id, m.`全限定名` AS method_fqn, c.nodeId AS class_id, c.`全限定名` AS class_fqn, m.serviceContexts AS services, m, r, c",
                        params={"node_id": m0.node_id},
                    )
                ],
            )

        # Methods inside a class
        if classes and any(k in q for k in ("哪些方法", "所有方法", "方法列表", "包含的方法", "方法数")):
            c0 = classes[0]
            if has_count:
                cy = "MATCH (m:`方法`)-[:`归属于`]->(c:`类` {nodeId: $class_id}) RETURN c.`全限定名` AS class_fqn, count(DISTINCT m.nodeId) AS method_count"
                shape = "count"
            else:
                cy = "MATCH (m:`方法`)-[r:`归属于`]->(c:`类` {nodeId: $class_id}) RETURN m.nodeId AS method_id, m.`方法名` AS method_name, m.`方法签名` AS signature, m.`是否入口方法` AS is_entry, m, r, c ORDER BY method_name"
                shape = "entities"
            return QueryPlan(
                intent="class_methods",
                category="单节点查询",
                level=QueryLevel.SIMPLE,
                direction="reverse",
                expected_shape=shape,
                entities=entities,
                subqueries=[
                    SubquerySpec(
                        subquery_id="sq_cls_methods",
                        mechanism="STRUCTURAL",
                        description=f"查询类 {c0.name} 包含的方法",
                        direction="reverse",
                        cypher=cy,
                        params={"class_id": c0.node_id},
                    )
                ],
            )

        # Config nodes
        if "配置" in q:
            if svcs:
                svc_name = svcs[0].name
                if has_count:
                    return QueryPlan(
                        intent="count_service_configs",
                        category="属性过滤",
                        level=QueryLevel.SIMPLE,
                        direction="forward",
                        expected_shape="count",
                        entities=entities,
                        subqueries=[
                            SubquerySpec(
                                subquery_id="sq_cfg_cnt",
                                mechanism="STRUCTURAL",
                                description=f"统计 {svc_name} 归属的配置节点数",
                                direction="forward",
                                cypher="MATCH (cfg:`配置`)-[:`归属于`]->(s:`微服务` {`服务名称`: $service}) RETURN s.`服务名称` AS service_name, count(DISTINCT cfg.nodeId) AS config_count",
                                params={"service": svc_name},
                            )
                        ],
                    )
                return QueryPlan(
                    intent="list_service_configs",
                    category="属性过滤",
                    level=QueryLevel.SIMPLE,
                    direction="forward",
                    expected_shape="entities",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_cfg_list",
                            mechanism="STRUCTURAL",
                            description=f"查询 {svc_name} 归属的配置节点",
                            direction="forward",
                            cypher="MATCH (cfg:`配置`)-[r:`归属于`]->(s:`微服务` {`服务名称`: $service}) RETURN cfg.nodeId AS config_id, cfg.configurationKind AS kind, coalesce(cfg.key, cfg.beanName, cfg.logicalResourcePath) AS config_name, cfg, r, s ORDER BY kind, config_name LIMIT 100",
                            params={"service": svc_name},
                        )
                    ],
                )
            if has_count:
                return QueryPlan(
                    intent="top_services_by_config_count",
                    category="属性过滤",
                    level=QueryLevel.MEDIUM,
                    direction="forward",
                    expected_shape="count",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_cfg_top",
                            mechanism="STRUCTURAL",
                            description="按配置节点数统计排名前 10 的微服务",
                            direction="forward",
                            cypher="MATCH (cfg:`配置`)-[:`归属于`]->(s:`微服务`) RETURN s.`服务名称` AS service_name, count(DISTINCT cfg.nodeId) AS config_count ORDER BY config_count DESC, service_name ASC LIMIT 10",
                            params={},
                        )
                    ],
                )

        # Class type filters (CONTROLLER, SERVICE_INTERFACE, SERVICE_IMPL)
        ctype_map = {
            "controller": "CONTROLLER",
            "控制器": "CONTROLLER",
            "service_interface": "SERVICE_INTERFACE",
            "service_impl": "SERVICE_IMPL",
            "服务实现": "SERVICE_IMPL",
        }
        matched_ctype = next((v for k, v in ctype_map.items() if k in q_low), None)
        if matched_ctype:
            if svcs:
                return QueryPlan(
                    intent="filter_service_classes_by_type",
                    category="属性过滤",
                    level=QueryLevel.SIMPLE,
                    direction="none",
                    expected_shape="count" if has_count else "entities",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_svc_ctype",
                            mechanism="STRUCTURAL",
                            description=f"查询 {svcs[0].name} 中类类型为 {matched_ctype} 的类",
                            direction="none",
                            cypher=(
                                "MATCH (c:`类` {`类类型`: $class_type}) WHERE $service IN c.serviceContexts RETURN count(DISTINCT c.nodeId) AS class_count"
                                if has_count
                                else "MATCH (c:`类` {`类类型`: $class_type}) WHERE $service IN c.serviceContexts RETURN c.nodeId AS class_id, c.`简名` AS class_name, c.`全限定名` AS fqn, c ORDER BY fqn"
                            ),
                            params={"class_type": matched_ctype, "service": svcs[0].name},
                        )
                    ],
                )
            return QueryPlan(
                intent="filter_classes_by_type",
                category="属性过滤",
                level=QueryLevel.SIMPLE,
                direction="none",
                expected_shape="count" if has_count else "entities",
                entities=entities,
                subqueries=[
                    SubquerySpec(
                        subquery_id="sq_ctype",
                        mechanism="STRUCTURAL",
                        description=f"查找所有类类型为 {matched_ctype} 的类",
                        direction="none",
                        cypher=(
                            "MATCH (c:`类` {`类类型`: $class_type}) RETURN count(DISTINCT c.nodeId) AS class_count"
                            if has_count
                            else "MATCH (c:`类` {`类类型`: $class_type}) RETURN c.nodeId AS class_id, c.`简名` AS class_name, c.`全限定名` AS fqn, c.serviceContexts AS services, c ORDER BY fqn"
                        ),
                        params={"class_type": matched_ctype},
                    )
                ],
            )

        # Interface class filter
        if "接口类" in q or ("接口" in q and "类" in q):
            if svcs:
                return QueryPlan(
                    intent="filter_interface_classes",
                    category="属性过滤",
                    level=QueryLevel.SIMPLE,
                    direction="none",
                    expected_shape="entities",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_svc_iface",
                            mechanism="STRUCTURAL",
                            description=f"查找 {svcs[0].name} 中的所有接口类",
                            direction="none",
                            cypher="MATCH (c:`类`) WHERE $service IN c.serviceContexts AND c.`是否接口` = true RETURN c.nodeId AS class_id, c.`简名` AS class_name, c.`全限定名` AS fqn, c ORDER BY fqn",
                            params={"service": svcs[0].name},
                        )
                    ],
                )
            return QueryPlan(
                intent="filter_all_interface_classes",
                category="属性过滤",
                level=QueryLevel.SIMPLE,
                direction="none",
                expected_shape="count" if has_count else "entities",
                entities=entities,
                subqueries=[
                    SubquerySpec(
                        subquery_id="sq_all_iface",
                        mechanism="STRUCTURAL",
                        description="查找图谱中所有接口类",
                        direction="none",
                        cypher=(
                            "MATCH (c:`类` {`是否接口`: true}) RETURN count(DISTINCT c.nodeId) AS interface_count"
                            if has_count
                            else "MATCH (c:`类` {`是否接口`: true}) RETURN c.nodeId AS class_id, c.`简名` AS class_name, c.`全限定名` AS fqn, c.serviceContexts AS services, c ORDER BY fqn"
                        ),
                        params={},
                    )
                ],
            )

        # Service classes / methods
        if svcs:
            svc_name = svcs[0].name
            if "入口方法" in q:
                return QueryPlan(
                    intent="service_entry_methods",
                    category="属性过滤",
                    level=QueryLevel.SIMPLE,
                    direction="none",
                    expected_shape="count" if has_count else "entities",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_svc_entry_m",
                            mechanism="STRUCTURAL",
                            description=f"查询 {svc_name} 的入口方法",
                            direction="none",
                            cypher=(
                                "MATCH (m:`方法` {`是否入口方法`: true}) WHERE $service IN m.serviceContexts RETURN $service AS service_name, count(DISTINCT m.nodeId) AS entry_method_count"
                                if has_count
                                else "MATCH (m:`方法` {`是否入口方法`: true}) WHERE $service IN m.serviceContexts RETURN m.nodeId AS method_id, m.`方法名` AS method_name, m.`全限定名` AS fqn, m ORDER BY fqn"
                            ),
                            params={"service": svc_name},
                        )
                    ],
                )
            if "方法" in q:
                return QueryPlan(
                    intent="service_methods",
                    category="单节点查询",
                    level=QueryLevel.SIMPLE,
                    direction="none",
                    expected_shape="count" if has_count else "entities",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_svc_m",
                            mechanism="STRUCTURAL",
                            description=f"查询 {svc_name} 的方法",
                            direction="none",
                            cypher=(
                                "MATCH (m:`方法`) WHERE $service IN m.serviceContexts RETURN $service AS service_name, count(DISTINCT m.nodeId) AS method_count"
                                if has_count
                                else "MATCH (m:`方法`) WHERE $service IN m.serviceContexts RETURN m.nodeId AS method_id, m.`方法名` AS method_name, m.`全限定名` AS fqn, m ORDER BY fqn LIMIT 100"
                            ),
                            params={"service": svc_name},
                        )
                    ],
                )
            if "类" in q:
                return QueryPlan(
                    intent="service_classes",
                    category="单节点查询",
                    level=QueryLevel.SIMPLE,
                    direction="none",
                    expected_shape="count" if has_count else "entities",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_svc_cls",
                            mechanism="STRUCTURAL",
                            description=f"查询 {svc_name} 包含的类",
                            direction="none",
                            cypher=(
                                "MATCH (c:`类`) WHERE $service IN c.serviceContexts RETURN $service AS service_name, count(DISTINCT c.nodeId) AS class_count"
                                if has_count
                                else "MATCH (c:`类`) WHERE $service IN c.serviceContexts RETURN c.nodeId AS class_id, c.`简名` AS class_name, c.`全限定名` AS fqn, c.`类类型` AS class_type, c ORDER BY fqn"
                            ),
                            params={"service": svc_name},
                        )
                    ],
                )

        # List / count all microservices
        if any(k in q for k in ("微服务", "所有服务", "全部服务")) and not methods and not classes:
            if has_count and not any(k in q for k in ("列出", "有哪些")):
                return QueryPlan(
                    intent="count_microservices",
                    category="单节点查询",
                    level=QueryLevel.SIMPLE,
                    direction="none",
                    expected_shape="count",
                    entities=entities,
                    subqueries=[
                        SubquerySpec(
                            subquery_id="sq_cnt_svcs",
                            mechanism="STRUCTURAL",
                            description="统计图谱中微服务总数",
                            direction="none",
                            cypher="MATCH (s:`微服务`) RETURN count(DISTINCT s.nodeId) AS microservice_count",
                            params={},
                        )
                    ],
                )
            return QueryPlan(
                intent="list_microservices",
                category="单节点查询",
                level=QueryLevel.SIMPLE,
                direction="none",
                expected_shape="entities",
                entities=entities,
                subqueries=[
                    SubquerySpec(
                        subquery_id="sq_list_svcs",
                        mechanism="STRUCTURAL",
                        description="列出图谱中全部微服务",
                        direction="none",
                        cypher="MATCH (s:`微服务`) RETURN s.nodeId AS nodeId, s.`服务名称` AS service_name, s ORDER BY s.`服务名称`",
                        params={},
                    )
                ],
            )

        # Generic method direct calls fallback if method is mentioned
        if methods:
            m0 = methods[0]
            return QueryPlan(
                intent="method_direct_calls",
                category="下游服务发现",
                level=QueryLevel.MEDIUM,
                direction="forward",
                max_hops=hops,
                mechanisms=["CALL", "RPC"],
                expected_shape="paths",
                entities=entities,
                subqueries=[
                    SubquerySpec(
                        subquery_id="sq_m_calls",
                        mechanism="STRUCTURAL",
                        description=f"查询方法 {m0.qualified_name} 的直接调用关系",
                        direction="forward",
                        cypher="MATCH (m:`方法` {nodeId: $node_id})-[r:`调用`]->(t) WHERE t:`方法` OR t:`下游API` RETURN m.`全限定名` AS source_method, labels(t)[0] AS target_type, coalesce(t.`全限定名`, t.caption) AS target_name, t.nodeId AS target_id, m, r, t ORDER BY target_type, target_name",
                        params={"node_id": m0.node_id},
                    )
                ],
            )

        # Fallback generic plan (LLM will synthesize using Schema + Skills + Top-3 KB examples)
        level = requested_level if requested_level != QueryLevel.AUTO else QueryLevel.MEDIUM
        return QueryPlan(
            intent="general_code_graph_query",
            category="单节点查询" if level == QueryLevel.SIMPLE else "下游服务发现",
            level=level,
            direction="forward",
            max_hops=hops,
            mechanisms=["STRUCTURAL"],
            expected_shape="entities",
            entities=entities,
        )

    def _build_hybrid_rpc_mq_subqueries(
        self, service: str, bidirectional: bool = False
    ) -> List[SubquerySpec]:
        if bidirectional:
            return [
                SubquerySpec(
                    subquery_id="sq_rpc_out",
                    mechanism="RPC",
                    description=f"子查询 A（同步 RPC 出向）：查询 {service} 主动调用的下游微服务（WHERE $service IN d.serviceContexts），返回 'RPC' AS mechanism、svc.`服务名称` AS peer_service 及拓扑节点/边",
                    direction="forward",
                    cypher=(
                        "MATCH (m:`方法`)-[r1:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[r2:`目标服务`]->(svc:`微服务`) "
                        "WHERE $service IN d.serviceContexts "
                        "RETURN DISTINCT 'RPC' AS mechanism, 'OUTBOUND' AS direction, svc.`服务名称` AS peer_service, "
                        "svc.nodeId AS target_id, d.caption AS channel, m, r1, d, r2, svc "
                        "ORDER BY peer_service, channel"
                    ),
                    params={"service": service},
                ),
                SubquerySpec(
                    subquery_id="sq_rpc_in",
                    mechanism="RPC",
                    description=f"子查询 B（同步 RPC 入向）：查询哪些上游微服务调用了 {service}（MATCH (d:`下游API`)-[:`目标服务`]->(svc:`微服务` {{`服务名称`: $service}})，UNWIND d.serviceContexts AS peer_service），返回 'RPC' AS mechanism、peer_service 及拓扑节点/边",
                    direction="reverse",
                    cypher=(
                        "MATCH (m:`方法`)-[r1:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[r2:`目标服务`]->(svc:`微服务` {`服务名称`: $service}) "
                        "UNWIND d.serviceContexts AS peer_service "
                        "RETURN DISTINCT 'RPC' AS mechanism, 'INBOUND' AS direction, peer_service, "
                        "'svc:' + peer_service AS target_id, d.caption AS channel, m, r1, d, r2, svc "
                        "ORDER BY peer_service, channel"
                    ),
                    params={"service": service},
                ),
                SubquerySpec(
                    subquery_id="sq_mq_bi",
                    mechanism="MQ",
                    description=f"子查询 C（异步 MQ 双向）：查询 {service} 作为发布方或消费方的完整三阶段消息流（必须满足 r1.`路由键` = r2.`路由键`），返回 'MQ' AS mechanism、对端微服务名 AS peer_service 及完整节点/边",
                    direction="both",
                    cypher=(
                        "MATCH (pub:`方法`)-[r1:`消息流` {`消息流类型`: '发布至'}]->(ex:`消息交换机`)"
                        "-[r2:`消息流` {`消息流类型`: '路由至'}]->(q:`消息队列`)"
                        "-[r3:`消息流` {`消息流类型`: '消费自'}]->(sub:`方法`) "
                        "WHERE r1.`路由键` = r2.`路由键` AND ($service IN pub.serviceContexts OR $service IN sub.serviceContexts) "
                        "UNWIND (CASE WHEN $service IN pub.serviceContexts THEN sub.serviceContexts ELSE pub.serviceContexts END) AS peer_svc "
                        "RETURN DISTINCT 'MQ' AS mechanism, "
                        "(CASE WHEN $service IN pub.serviceContexts THEN 'OUTBOUND' ELSE 'INBOUND' END) AS direction, "
                        "peer_svc AS peer_service, 'svc:' + peer_svc AS target_id, "
                        "q.`队列名称` + ' (routingKey=' + r1.`路由键` + ')' AS channel, "
                        "pub, r1, ex, r2, q, r3, sub"
                    ),
                    params={"service": service},
                ),
            ]
        return [
            SubquerySpec(
                subquery_id="sq_rpc_out",
                mechanism="RPC",
                description=f"子查询 A（同步 RPC 出向）：还原 {service} 经由 下游API->目标服务 的全部同步依赖",
                direction="forward",
                cypher=(
                    "MATCH (m:`方法`)-[r1:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[r2:`目标服务`]->(svc:`微服务`) "
                    "WHERE $service IN d.serviceContexts "
                    "RETURN DISTINCT 'RPC' AS mechanism, svc.`服务名称` AS target_service, "
                    "svc.nodeId AS target_id, d.caption AS channel, m, r1, d, r2, svc "
                    "ORDER BY target_service, channel"
                ),
                params={"service": service},
            ),
            SubquerySpec(
                subquery_id="sq_mq_out",
                mechanism="MQ",
                description=f"子查询 B（异步 MQ 出向）：还原 {service} 的 发布至->路由至(路由键匹配)->消费自 完整异步依赖",
                direction="forward",
                cypher=(
                    "MATCH (pub:`方法`)-[r1:`消息流` {`消息流类型`: '发布至'}]->(ex:`消息交换机`)"
                    "-[r2:`消息流` {`消息流类型`: '路由至'}]->(q:`消息队列`)"
                    "-[r3:`消息流` {`消息流类型`: '消费自'}]->(sub:`方法`) "
                    "WHERE r1.`路由键` = r2.`路由键` AND $service IN pub.serviceContexts "
                    "MATCH (q)-[r4:`归属于`]->(svc:`微服务`) "
                    "RETURN DISTINCT 'MQ' AS mechanism, svc.`服务名称` AS target_service, "
                    "svc.nodeId AS target_id, q.`队列名称` + ' (routingKey=' + r1.`路由键` + ')' AS channel, "
                    "pub, r1, ex, r2, q, r3, sub, r4, svc "
                    "ORDER BY target_service"
                ),
                params={"service": service},
            ),
        ]

    def _build_mq_between_services(self, src_svc: str, dst_svc: str) -> List[SubquerySpec]:
        return [
            SubquerySpec(
                subquery_id="sq_mq_pair",
                mechanism="MQ",
                description=f"查询 {src_svc} 与 {dst_svc} 之间的完整三阶段 MQ 消息通信",
                direction="forward",
                cypher=(
                    "MATCH (pub:`方法`)-[r1:`消息流` {`消息流类型`: '发布至'}]->(ex:`消息交换机`)"
                    "-[r2:`消息流` {`消息流类型`: '路由至'}]->(q:`消息队列`)"
                    "-[r3:`消息流` {`消息流类型`: '消费自'}]->(sub:`方法`) "
                    "WHERE r1.`路由键` = r2.`路由键` "
                    "AND (($s1 IN pub.serviceContexts AND $s2 IN sub.serviceContexts) "
                    "OR ($s2 IN pub.serviceContexts AND $s1 IN sub.serviceContexts)) "
                    "RETURN pub.serviceContexts AS producer_services, pub.`全限定名` AS producer_method, "
                    "ex.`交换机名称` AS exchange_name, r1.`路由键` AS routing_key, q.`队列名称` AS queue_name, "
                    "sub.serviceContexts AS consumer_services, sub.`全限定名` AS consumer_method, "
                    "pub, r1, ex, r2, q, r3, sub"
                ),
                params={"s1": src_svc, "s2": dst_svc},
            )
        ]

    def _build_mq_queue_chain(self, queue_name: str) -> List[SubquerySpec]:
        return [
            SubquerySpec(
                subquery_id="sq_mq_queue",
                mechanism="MQ",
                description=f"还原 {queue_name} 队列的完整发布者 -> 交换机 -> 队列 -> 消费者链路",
                direction="both",
                cypher=(
                    "MATCH (pub:`方法`)-[r1:`消息流` {`消息流类型`: '发布至'}]->(ex:`消息交换机`)"
                    "-[r2:`消息流` {`消息流类型`: '路由至'}]->(q:`消息队列` {`队列名称`: $queue_name})"
                    "-[r3:`消息流` {`消息流类型`: '消费自'}]->(sub:`方法`) "
                    "WHERE r1.`路由键` = r2.`路由键` "
                    "RETURN pub.serviceContexts AS producer_services, pub.`全限定名` AS producer_method, "
                    "ex.`交换机名称` AS exchange_name, r1.`路由键` AS routing_key, q.`队列名称` AS queue_name, "
                    "sub.serviceContexts AS consumer_services, sub.`全限定名` AS consumer_method, "
                    "pub, r1, ex, r2, q, r3, sub ORDER BY producer_method"
                ),
                params={"queue_name": queue_name},
            )
        ]

    def _build_mq_service_chain(self, service: str, consume_only: bool = False) -> List[SubquerySpec]:
        filter_clause = "$service IN sub.serviceContexts" if consume_only else "$service IN pub.serviceContexts"
        return [
            SubquerySpec(
                subquery_id="sq_mq_svc",
                mechanism="MQ",
                description=f"还原 {service} 的完整 MQ 三阶段链路",
                direction="reverse" if consume_only else "forward",
                cypher=(
                    "MATCH (pub:`方法`)-[r1:`消息流` {`消息流类型`: '发布至'}]->(ex:`消息交换机`)"
                    "-[r2:`消息流` {`消息流类型`: '路由至'}]->(q:`消息队列`)"
                    "-[r3:`消息流` {`消息流类型`: '消费自'}]->(sub:`方法`) "
                    f"WHERE r1.`路由键` = r2.`路由键` AND {filter_clause} "
                    "RETURN pub.serviceContexts AS producer_services, pub.`全限定名` AS producer_method, "
                    "ex.`交换机名称` AS exchange_name, r1.`路由键` AS routing_key, q.`队列名称` AS queue_name, "
                    "sub.serviceContexts AS consumer_services, sub.`全限定名` AS consumer_method, "
                    "pub, r1, ex, r2, q, r3, sub ORDER BY routing_key, producer_method"
                ),
                params={"service": service},
            )
        ]

    def _build_method_reverse_impact_subqueries(
        self, target: EntityCandidate, hops: int, include_cross: bool = True
    ) -> List[SubquerySpec]:
        h_local = min(hops, 6)
        sqs = [
            SubquerySpec(
                subquery_id="sq_impact_local",
                mechanism="IMPACT",
                description=f"子查询 A（本服务反向影响）：追溯调用 {target.qualified_name} 的本服务入口 API",
                direction="reverse",
                max_hops=h_local,
                cypher=(
                    "MATCH (target:`方法` {nodeId: $node_id}) "
                    f"MATCH p=(entry:`方法`)-[rs:`调用`*0..{h_local}]->(target) "
                    "WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) "
                    "AND ALL(x IN nodes(p) WHERE x:`方法`) "
                    "MATCH (u:`上游API`)-[ru:`归属于`]->(entry) "
                    "RETURN DISTINCT 'LOCAL_ENTRY_API' AS impact_scope, u.nodeId AS api_id, "
                    "u.caption AS affected_api, u.serviceContexts[0] AS affected_service, "
                    "entry.`全限定名` AS entry_method, u, ru, p ORDER BY affected_api"
                ),
                params={"node_id": target.node_id},
            )
        ]
        if include_cross:
            sqs.append(
                SubquerySpec(
                    subquery_id="sq_impact_cross",
                    mechanism="RPC",
                    description=(
                        f"子查询 B（跨服务上游入口 API 反向影响）：先沿本服务反向调用找到 {target.qualified_name} 对应的本地 上游API，"
                        "再经 下游API-[:调用 {关系语义:'跨服务HTTP调用'}]->上游API 反向找到上游调用方法 up_caller，"
                        "最后继续沿上游服务内反向调用 (up_entry:方法)-[:调用*0..5]->(up_caller) 穿透到上游微服务的入口 (up_api:上游API)-[:归属于]->(up_entry)，"
                        "返回 up_api.nodeId AS api_id、up_api.caption AS affected_api 及完整路径"
                    ),
                    direction="reverse",
                    max_hops=min(hops, 5),
                    cypher=(
                        "MATCH (target:`方法` {nodeId: $node_id}) "
                        f"MATCH p1=(local_entry:`方法`)-[rs1:`调用`*0..{min(hops, 4)}]->(target) "
                        "WHERE ALL(rel IN rs1 WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p1) WHERE x:`方法`) "
                        "MATCH (u:`上游API`)-[ru:`归属于`]->(local_entry) "
                        "MATCH (up_caller:`方法`)-[rc:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[rh:`调用` {`关系语义`: '跨服务HTTP调用'}]->(u) "
                        "MATCH p2=(up_entry:`方法`)-[rs2:`调用`*0..5]->(up_caller) "
                        "WHERE ALL(rel IN rs2 WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p2) WHERE x:`方法`) "
                        "MATCH (up_api:`上游API`)-[ru2:`归属于`]->(up_entry) "
                        "RETURN DISTINCT 'CROSS_SERVICE_UPSTREAM' AS impact_scope, "
                        "up_api.nodeId AS api_id, "
                        "up_api.caption AS affected_api, "
                        "up_api.serviceContexts[0] AS affected_service, "
                        "up_entry.`全限定名` AS entry_method, "
                        "up_api, ru2, p2, rc, d, rh, u, ru, p1 "
                        "ORDER BY affected_service, affected_api"
                    ),
                    params={"node_id": target.node_id},
                )
            )
        return sqs


query_decomposer = QueryDecomposerAndPlanner()
