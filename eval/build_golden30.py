from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.pipeline.executor import cypher_executor
from app.pipeline.schema_fetcher import schema_fetcher
from app.pipeline.validator import cypher_validator


# 30 Golden Evaluation Questions — strictly independent of KB30 (10 Simple + 12 Medium + 8 Hard)
GOLDEN30_SPEC: List[Dict[str, Any]] = [
    # ===== SIMPLE S1–S10 (10) =====
    {
        "id": "S01",
        "level": "simple",
        "category": "单节点计数",
        "question": "统计图谱中微服务的总数",
        "eval_type": "scalar_int",
        "eval_key": "microservice_count",
        "reference_cypher": "MATCH (s:`微服务`) RETURN count(DISTINCT s.nodeId) AS microservice_count",
        "reference_params": {},
    },
    {
        "id": "S02",
        "level": "simple",
        "category": "单节点查询",
        "question": "查询 ts-order-service 微服务包含哪些类",
        "eval_type": "id_set",
        "eval_key": "class_id",
        "reference_cypher": "MATCH (c:`类`) WHERE $service IN c.serviceContexts RETURN c.nodeId AS class_id",
        "reference_params": {"service": "ts-order-service"},
    },
    {
        "id": "S03",
        "level": "simple",
        "category": "属性过滤",
        "question": "查找 ts-user-service 中类类型为 SERVICE_IMPL 的类",
        "eval_type": "id_set",
        "eval_key": "class_id",
        "reference_cypher": "MATCH (c:`类` {`类类型`: 'SERVICE_IMPL'}) WHERE $service IN c.serviceContexts RETURN c.nodeId AS class_id",
        "reference_params": {"service": "ts-user-service"},
    },
    {
        "id": "S04",
        "level": "simple",
        "category": "单节点查询",
        "question": "列出图谱中所有的消息交换机",
        "eval_type": "id_set",
        "eval_key": "exchange_id",
        "reference_cypher": "MATCH (e:`消息交换机`) RETURN e.nodeId AS exchange_id",
        "reference_params": {},
    },
    {
        "id": "S05",
        "level": "simple",
        "category": "属性过滤",
        "question": "查询所有 HTTP 方法为 PUT 的上游 API 端点",
        "eval_type": "id_set",
        "eval_key": "api_id",
        "reference_cypher": "MATCH (u:`上游API` {`HTTP方法`: 'PUT'}) RETURN u.nodeId AS api_id",
        "reference_params": {},
    },
    {
        "id": "S06",
        "level": "simple",
        "category": "入口API发现",
        "question": "列出 ts-station-service 暴露的所有上游入口 API",
        "eval_type": "id_set",
        "eval_key": "api_id",
        "reference_cypher": "MATCH (u:`上游API`) WHERE $service IN u.serviceContexts RETURN u.nodeId AS api_id",
        "reference_params": {"service": "ts-station-service"},
    },
    {
        "id": "S07",
        "level": "simple",
        "category": "单跳归属",
        "question": "查询 StationController 类包含哪些方法",
        "eval_type": "id_set",
        "eval_key": "method_id",
        "reference_cypher": "MATCH (m:`方法`)-[:`归属于`]->(c:`类` {`简名`: 'StationController'}) RETURN m.nodeId AS method_id",
        "reference_params": {},
    },
    {
        "id": "S08",
        "level": "simple",
        "category": "单跳关系",
        "question": "查询图谱中的类继承关系",
        "eval_type": "id_set",
        "eval_key": "sub_id",
        "reference_cypher": "MATCH (sub:`类`)-[:`继承`]->(sup:`类`) RETURN sub.nodeId AS sub_id",
        "reference_params": {},
    },
    {
        "id": "S09",
        "level": "simple",
        "category": "单跳关系",
        "question": "查询 ts-auth-service 中的接口实现关系",
        "eval_type": "id_set",
        "eval_key": "impl_id",
        "reference_cypher": "MATCH (impl:`类`)-[:`接口实现`]->(iface:`类`) WHERE $service IN impl.serviceContexts RETURN impl.nodeId AS impl_id",
        "reference_params": {"service": "ts-auth-service"},
    },
    {
        "id": "S10",
        "level": "simple",
        "category": "基础计数",
        "question": "统计 ts-security-service 微服务下归属的配置节点数量",
        "eval_type": "scalar_int",
        "eval_key": "config_count",
        "reference_cypher": "MATCH (cfg:`配置`)-[:`归属于`]->(s:`微服务` {`服务名称`: $service}) RETURN count(DISTINCT cfg.nodeId) AS config_count",
        "reference_params": {"service": "ts-security-service"},
    },
    # ===== MEDIUM M1–M12 (12) =====
    {
        "id": "M01",
        "level": "medium",
        "category": "下游服务发现",
        "question": "RebookController.rebook 方法调用了哪些下游微服务？",
        "eval_type": "id_set",
        "eval_key": "service_id",
        "reference_cypher": "MATCH (entry:`方法` {`方法名`: 'rebook'}) WHERE entry.`所属类名` ENDS WITH 'RebookController' MATCH p=(entry)-[rs:`调用`*0..6]->(m:`方法`) WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p) WHERE x:`方法`) MATCH (m)-[:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[:`目标服务`]->(svc:`微服务`) RETURN DISTINCT svc.nodeId AS service_id",
        "reference_params": {},
    },
    {
        "id": "M02",
        "level": "medium",
        "category": "下游服务发现",
        "question": "AdminTravelController.addTravel 方法调用了哪些下游微服务？",
        "eval_type": "id_set",
        "eval_key": "service_id",
        "reference_cypher": "MATCH (entry:`方法` {`方法名`: 'addTravel'}) WHERE entry.`所属类名` ENDS WITH 'AdminTravelController' MATCH p=(entry)-[rs:`调用`*0..6]->(m:`方法`) WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p) WHERE x:`方法`) MATCH (m)-[:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[:`目标服务`]->(svc:`微服务`) RETURN DISTINCT svc.nodeId AS service_id",
        "reference_params": {},
    },
    {
        "id": "M03",
        "level": "medium",
        "category": "下游服务发现",
        "question": "SeatController.getLeftTicketOfInterval 方法调用了哪些下游微服务？",
        "eval_type": "id_set",
        "eval_key": "service_id",
        "reference_cypher": "MATCH (entry:`方法` {`方法名`: 'getLeftTicketOfInterval'}) WHERE entry.`所属类名` ENDS WITH 'SeatController' MATCH p=(entry)-[rs:`调用`*0..6]->(m:`方法`) WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p) WHERE x:`方法`) MATCH (m)-[:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[:`目标服务`]->(svc:`微服务`) RETURN DISTINCT svc.nodeId AS service_id",
        "reference_params": {},
    },
    {
        "id": "M04",
        "level": "medium",
        "category": "下游服务发现",
        "question": "InsidePaymentController.pay 方法调用了哪些下游微服务？",
        "eval_type": "id_set",
        "eval_key": "service_id",
        "reference_cypher": "MATCH (entry:`方法` {`方法名`: 'pay'}) WHERE entry.`所属类名` ENDS WITH 'InsidePaymentController' MATCH p=(entry)-[rs:`调用`*0..6]->(m:`方法`) WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p) WHERE x:`方法`) MATCH (m)-[:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[:`目标服务`]->(svc:`微服务`) RETURN DISTINCT svc.nodeId AS service_id",
        "reference_params": {},
    },
    {
        "id": "M05",
        "level": "medium",
        "category": "下游服务发现",
        "question": "TravelPlanController.getByQuickest 方法调用了哪些下游微服务？",
        "eval_type": "id_set",
        "eval_key": "service_id",
        "reference_cypher": "MATCH (entry:`方法` {`方法名`: 'getByQuickest'}) WHERE entry.`所属类名` ENDS WITH 'TravelPlanController' MATCH p=(entry)-[rs:`调用`*0..6]->(m:`方法`) WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p) WHERE x:`方法`) MATCH (m)-[:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[:`目标服务`]->(svc:`微服务`) RETURN DISTINCT svc.nodeId AS service_id",
        "reference_params": {},
    },
    {
        "id": "M06",
        "level": "medium",
        "category": "下游服务发现",
        "question": "ExecuteControlller.executeTicket 方法调用了哪些下游微服务？",
        "eval_type": "id_set",
        "eval_key": "service_id",
        "reference_cypher": "MATCH (entry:`方法` {`方法名`: 'executeTicket'}) WHERE entry.`所属类名` ENDS WITH 'ExecuteControlller' MATCH p=(entry)-[rs:`调用`*0..6]->(m:`方法`) WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p) WHERE x:`方法`) MATCH (m)-[:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[:`目标服务`]->(svc:`微服务`) RETURN DISTINCT svc.nodeId AS service_id",
        "reference_params": {},
    },
    {
        "id": "M07",
        "level": "medium",
        "category": "反向依赖",
        "question": "哪些微服务调用了 ts-order-service？",
        "eval_type": "str_set",
        "eval_key": "caller_service",
        "reference_cypher": "MATCH (d:`下游API`)-[:`目标服务`]->(svc:`微服务` {`服务名称`: 'ts-order-service'}) UNWIND d.serviceContexts AS caller_service RETURN DISTINCT caller_service",
        "reference_params": {},
    },
    {
        "id": "M08",
        "level": "medium",
        "category": "反向依赖",
        "question": "哪些微服务调用了 ts-station-service？",
        "eval_type": "str_set",
        "eval_key": "caller_service",
        "reference_cypher": "MATCH (d:`下游API`)-[:`目标服务`]->(svc:`微服务` {`服务名称`: 'ts-station-service'}) UNWIND d.serviceContexts AS caller_service RETURN DISTINCT caller_service",
        "reference_params": {},
    },
    {
        "id": "M09",
        "level": "medium",
        "category": "反向依赖",
        "question": "哪些微服务调用了 ts-user-service？",
        "eval_type": "str_set",
        "eval_key": "caller_service",
        "reference_cypher": "MATCH (d:`下游API`)-[:`目标服务`]->(svc:`微服务` {`服务名称`: 'ts-user-service'}) UNWIND d.serviceContexts AS caller_service RETURN DISTINCT caller_service",
        "reference_params": {},
    },
    {
        "id": "M10",
        "level": "medium",
        "category": "聚合统计",
        "question": "统计 ts-rebook-service 通过 HTTP 调用各下游微服务的接口数量",
        "eval_type": "kv_map",
        "eval_key": "target_service",
        "eval_val": "api_call_count",
        "reference_cypher": "MATCH (d:`下游API`)-[:`目标服务`]->(svc:`微服务`) WHERE 'ts-rebook-service' IN d.serviceContexts RETURN svc.`服务名称` AS target_service, count(DISTINCT d.nodeId) AS api_call_count",
        "reference_params": {},
    },
    {
        "id": "M11",
        "level": "medium",
        "category": "聚合统计",
        "question": "按配置节点数量统计排名前 10 的微服务",
        "eval_type": "kv_map",
        "eval_key": "service_name",
        "eval_val": "config_count",
        "reference_cypher": "MATCH (cfg:`配置`)-[:`归属于`]->(s:`微服务`) RETURN s.`服务名称` AS service_name, count(DISTINCT cfg.nodeId) AS config_count ORDER BY config_count DESC, service_name ASC LIMIT 10",
        "reference_params": {},
    },
    {
        "id": "M12",
        "level": "medium",
        "category": "组合属性过滤",
        "question": "查询 ts-notification-service 中 HTTP 方法为 POST 的上游 API",
        "eval_type": "id_set",
        "eval_key": "api_id",
        "reference_cypher": "MATCH (u:`上游API` {`HTTP方法`: 'POST'}) WHERE 'ts-notification-service' IN u.serviceContexts RETURN u.nodeId AS api_id",
        "reference_params": {},
    },
    # ===== HARD H1–H8 (8) =====
    {
        "id": "H01",
        "level": "complex",
        "category": "MQ三阶段链路",
        "question": "email 队列的消息由谁发布、经过哪个交换机、最终被谁消费？",
        "eval_type": "str_set",
        "eval_key": "producer_method",
        "reference_cypher": "MATCH (pub:`方法`)-[r1:`消息流` {`消息流类型`: '发布至'}]->(ex:`消息交换机`)-[r2:`消息流` {`消息流类型`: '路由至'}]->(q:`消息队列` {`队列名称`: 'email'})-[r3:`消息流` {`消息流类型`: '消费自'}]->(sub:`方法`) WHERE r1.`路由键` = r2.`路由键` RETURN DISTINCT pub.`全限定名` AS producer_method",
        "reference_params": {},
    },
    {
        "id": "H02",
        "level": "complex",
        "category": "跨服务MQ通信",
        "question": "ts-preserve-other-service 与 ts-notification-service 之间有哪些 MQ 消息通信？",
        "eval_type": "str_set",
        "eval_key": "queue_name",
        "reference_cypher": "MATCH (pub:`方法`)-[r1:`消息流` {`消息流类型`: '发布至'}]->(ex:`消息交换机`)-[r2:`消息流` {`消息流类型`: '路由至'}]->(q:`消息队列`)-[r3:`消息流` {`消息流类型`: '消费自'}]->(sub:`方法`) WHERE r1.`路由键` = r2.`路由键` AND 'ts-preserve-other-service' IN pub.serviceContexts AND 'ts-notification-service' IN sub.serviceContexts RETURN DISTINCT q.`队列名称` AS queue_name",
        "reference_params": {},
    },
    {
        "id": "H03",
        "level": "complex",
        "category": "多跳跨服务RPC",
        "question": "追踪 ts-travel2-service 经由中间服务间接调用的两跳下游微服务链路",
        "eval_type": "str_set",
        "eval_key": "hop2_service",
        "reference_cypher": "MATCH (m1:`方法`)-[:`调用` {`关系语义`: '通信边界'}]->(d1:`下游API`)-[:`调用` {`关系语义`: '跨服务HTTP调用'}]->(u1:`上游API`)-[:`归属于`]->(m2:`方法`) WHERE 'ts-travel2-service' IN m1.serviceContexts MATCH p2=(m2)-[rs:`调用`*0..4]->(m2b:`方法`) WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p2) WHERE x:`方法`) MATCH (m2b)-[:`调用` {`关系语义`: '通信边界'}]->(d2:`下游API`)-[:`目标服务`]->(s3:`微服务`) WHERE NOT ('ts-travel2-service' IN m2.serviceContexts) AND s3.`服务名称` <> 'ts-travel2-service' RETURN DISTINCT s3.`服务名称` AS hop2_service",
        "reference_params": {},
    },
    {
        "id": "H04",
        "level": "complex",
        "category": "多跳跨服务RPC",
        "question": "追踪 ts-preserve-service 经由中间服务间接调用的两跳下游微服务链路",
        "eval_type": "str_set",
        "eval_key": "hop2_service",
        "reference_cypher": "MATCH (m1:`方法`)-[:`调用` {`关系语义`: '通信边界'}]->(d1:`下游API`)-[:`调用` {`关系语义`: '跨服务HTTP调用'}]->(u1:`上游API`)-[:`归属于`]->(m2:`方法`) WHERE 'ts-preserve-service' IN m1.serviceContexts MATCH p2=(m2)-[rs:`调用`*0..4]->(m2b:`方法`) WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p2) WHERE x:`方法`) MATCH (m2b)-[:`调用` {`关系语义`: '通信边界'}]->(d2:`下游API`)-[:`目标服务`]->(s3:`微服务`) WHERE NOT ('ts-preserve-service' IN m2.serviceContexts) AND s3.`服务名称` <> 'ts-preserve-service' RETURN DISTINCT s3.`服务名称` AS hop2_service",
        "reference_params": {},
    },
    {
        "id": "H05",
        "level": "complex",
        "category": "跨服务变更影响分析",
        "question": "修改 BasicController.queryForTravel 方法会影响哪些上游入口 API？",
        "eval_type": "id_set",
        "eval_key": "api_id",
        "reference_cypher": "MATCH (target:`方法` {`方法名`: 'queryForTravel'}) WHERE target.`所属类名` ENDS WITH 'BasicController' MATCH p=(entry:`方法`)-[rs:`调用`*0..6]->(target) WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p) WHERE x:`方法`) MATCH (u:`上游API`)-[:`归属于`]->(entry) RETURN DISTINCT u.nodeId AS api_id UNION MATCH (target:`方法` {`方法名`: 'queryForTravel'}) WHERE target.`所属类名` ENDS WITH 'BasicController' MATCH (u:`上游API`)-[:`归属于`]->(target) MATCH (up_caller:`方法`)-[:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[:`调用` {`关系语义`: '跨服务HTTP调用'}]->(u) MATCH p2=(up_entry:`方法`)-[rs2:`调用`*0..5]->(up_caller) WHERE ALL(rel IN rs2 WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p2) WHERE x:`方法`) MATCH (up_api:`上游API`)-[:`归属于`]->(up_entry) RETURN DISTINCT up_api.nodeId AS api_id",
        "reference_params": {},
    },
    {
        "id": "H06",
        "level": "complex",
        "category": "跨服务变更影响分析",
        "question": "修改 StationController.queryForStationId 方法会影响哪些上游入口 API？",
        "eval_type": "id_set",
        "eval_key": "api_id",
        "reference_cypher": "MATCH (target:`方法` {`方法名`: 'queryForStationId'}) WHERE target.`所属类名` ENDS WITH 'StationController' MATCH p=(entry:`方法`)-[rs:`调用`*0..6]->(target) WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p) WHERE x:`方法`) MATCH (u:`上游API`)-[:`归属于`]->(entry) RETURN DISTINCT u.nodeId AS api_id UNION MATCH (target:`方法` {`方法名`: 'queryForStationId'}) WHERE target.`所属类名` ENDS WITH 'StationController' MATCH (u:`上游API`)-[:`归属于`]->(target) MATCH (up_caller:`方法`)-[:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[:`调用` {`关系语义`: '跨服务HTTP调用'}]->(u) MATCH p2=(up_entry:`方法`)-[rs2:`调用`*0..5]->(up_caller) WHERE ALL(rel IN rs2 WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p2) WHERE x:`方法`) MATCH (up_api:`上游API`)-[:`归属于`]->(up_entry) RETURN DISTINCT up_api.nodeId AS api_id",
        "reference_params": {},
    },
    {
        "id": "H07",
        "level": "complex",
        "category": "服务级反向影响穿透",
        "question": "ts-order-service 发生接口变更会影响哪些上游微服务的入口 API？",
        "eval_type": "id_set",
        "eval_key": "api_id",
        "reference_cypher": "MATCH (d:`下游API`)-[:`目标服务`]->(svc:`微服务` {`服务名称`: 'ts-order-service'}) MATCH (caller:`方法`)-[:`调用` {`关系语义`: '通信边界'}]->(d) MATCH p=(entry:`方法`)-[rs:`调用`*0..5]->(caller) WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p) WHERE x:`方法`) MATCH (up_api:`上游API`)-[:`归属于`]->(entry) RETURN DISTINCT up_api.nodeId AS api_id",
        "reference_params": {},
    },
    {
        "id": "H08",
        "level": "complex",
        "category": "RPC+MQ双向联合依赖",
        "question": "查询 ts-food-service 的双向跨服务依赖清单（同时包含入向与出向的同步 RPC 与异步 MQ）",
        "eval_type": "pair_set",
        "eval_keys": ["mechanism", "peer_service"],
        "reference_cypher": "MATCH (d:`下游API`)-[:`目标服务`]->(svc:`微服务`) WHERE 'ts-food-service' IN d.serviceContexts RETURN DISTINCT 'RPC' AS mechanism, svc.`服务名称` AS peer_service UNION MATCH (d:`下游API`)-[:`目标服务`]->(svc:`微服务` {`服务名称`: 'ts-food-service'}) UNWIND d.serviceContexts AS peer_service RETURN DISTINCT 'RPC' AS mechanism, peer_service UNION MATCH (pub:`方法`)-[r1:`消息流` {`消息流类型`: '发布至'}]->(ex:`消息交换机`)-[r2:`消息流` {`消息流类型`: '路由至'}]->(q:`消息队列`)-[r3:`消息流` {`消息流类型`: '消费自'}]->(sub:`方法`) WHERE r1.`路由键` = r2.`路由键` AND ('ts-food-service' IN pub.serviceContexts OR 'ts-food-service' IN sub.serviceContexts) UNWIND (CASE WHEN 'ts-food-service' IN pub.serviceContexts THEN sub.serviceContexts ELSE pub.serviceContexts END) AS peer_service RETURN DISTINCT 'MQ' AS mechanism, peer_service",
        "reference_params": {},
    },
]


def main() -> None:
    # Verify ZERO overlap with KB30 questions
    kb_path = ROOT / "data" / "kb30.json"
    kb_questions = {item["question"].strip() for item in json.loads(kb_path.read_text(encoding="utf-8"))}

    schema = schema_fetcher.get_schema(force_refresh=False)
    frozen: List[Dict[str, Any]] = []
    now = datetime.utcnow().isoformat() + "Z"

    for spec in GOLDEN30_SPEC:
        assert spec["question"].strip() not in kb_questions, f"LEAKAGE DETECTED: {spec['id']} overlaps KB30!"
        val = cypher_validator.validate(spec["reference_cypher"], spec["reference_params"], schema, run_explain=True)
        assert val.passed, f"{spec['id']} reference cypher failed validation: {[l.message for l in val.layers if not l.passed]}"
        exe = cypher_executor.execute(spec["reference_cypher"], spec["reference_params"])
        assert not exe.error and exe.total_records > 0, f"{spec['id']} empty or failed: err={exe.error}, rows={exe.total_records}"

        if spec["eval_type"] == "scalar_int":
            expected = int(exe.records[0][spec["eval_key"]])
        elif spec["eval_type"] in ("id_set", "str_set"):
            expected = sorted({str(r[spec["eval_key"]]) for r in exe.records if r.get(spec["eval_key"]) is not None})
        elif spec["eval_type"] == "kv_map":
            expected = {str(r[spec["eval_key"]]): int(r[spec["eval_val"]]) for r in exe.records}
        elif spec["eval_type"] == "pair_set":
            k1, k2 = spec["eval_keys"]
            expected = sorted({f"{r[k1]}::{r[k2]}" for r in exe.records if r.get(k1) and r.get(k2)})
        else:
            raise ValueError(spec["eval_type"])

        entry = dict(spec)
        entry["expected"] = expected
        entry["expected_count"] = len(expected) if isinstance(expected, (list, dict)) else 1
        entry["schema_hash"] = schema.schema_hash
        entry["graph_version"] = schema.graph_version
        entry["frozen_at"] = now
        frozen.append(entry)
        print(f"[FROZEN] {spec['id']} ({spec['level']}/{spec['category']}) -> expected_count={entry['expected_count']}")

    out_path = ROOT / "data" / "golden30.json"
    out_path.write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSUCCESS: Froze {len(frozen)} independent Golden questions (10S/12M/8H, zero KB30 overlap) -> {out_path}")


if __name__ == "__main__":
    main()
