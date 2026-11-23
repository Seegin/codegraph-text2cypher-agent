from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from neo4j.graph import Node, Path, Relationship

from app.core.config import settings
from app.models.schemas import ExecutionResult, GraphEdge, GraphNode, PathEvidence
from app.pipeline.schema_fetcher import neo4j_manager


DISPLAY_PROP_WHITELIST = [
    "nodeId",
    "服务名称",
    "简名",
    "全限定名",
    "类类型",
    "是否接口",
    "方法名",
    "所属类名",
    "方法签名",
    "是否入口方法",
    "是否抽象方法",
    "HTTP方法",
    "API路径",
    "完整URL",
    "目标服务名",
    "控制器方法",
    "队列名称",
    "交换机名称",
    "交换机类型",
    "所属服务名",
    "configurationKind",
    "key",
    "logicalResourcePath",
    "beanName",
    "serviceContexts",
    "moduleId",
    "来源文件",
    "来源行号",
    "图谱版本",
    "caption",
]


def _pick_node_label(node: Node) -> str:
    lbls = list(node.labels)
    return lbls[0] if lbls else "Unknown"


def _pick_node_display(props: Dict[str, Any], node_type: str) -> str:
    for key in ("服务名称", "caption", "队列名称", "交换机名称", "方法名", "简名", "API路径", "key", "beanName", "nodeId"):
        val = props.get(key)
        if val is not None and str(val).strip():
            if node_type == "方法" and props.get("所属类名"):
                short_cls = str(props["所属类名"]).split(".")[-1]
                return f"{short_cls}.{val}"
            return str(val)
    return str(props.get("nodeId", "unknown"))


def _compact_props(props: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in DISPLAY_PROP_WHITELIST:
        if k in props and props[k] is not None:
            out[k] = props[k]
    if not out:
        for k, v in list(props.items())[:12]:
            if isinstance(v, (str, int, float, bool, list)):
                out[k] = v
    return out


def _classify_mechanism(rel_type: str, rel_props: Dict[str, Any]) -> str:
    if rel_type == "消息流":
        return "MQ"
    if rel_type == "目标服务":
        return "RPC"
    sem = str(rel_props.get("关系语义") or "")
    if sem in ("通信边界", "跨服务HTTP调用"):
        return "RPC"
    if rel_type == "调用":
        return "CALL"
    return "STRUCTURAL"


def _serialize_value(val: Any) -> Any:
    if isinstance(val, Node):
        props = dict(val)
        return {
            "_type": "node",
            "nodeId": props.get("nodeId") or val.element_id,
            "labels": list(val.labels),
            "caption": _pick_node_display(props, _pick_node_label(val)),
            "properties": _compact_props(props),
        }
    if isinstance(val, Relationship):
        props = dict(val)
        return {
            "_type": "relationship",
            "relType": val.type,
            "relKey": props.get("关系键") or val.element_id,
            "mechanism": _classify_mechanism(val.type, props),
            "properties": {
                k: props[k]
                for k in ("关系语义", "调用类型", "消息流类型", "路由键", "目标服务名", "来源文件", "来源行号")
                if k in props and props[k] is not None
            },
        }
    if isinstance(val, Path):
        return {
            "_type": "path",
            "length": len(val.relationships),
            "nodes": [_serialize_value(n) for n in val.nodes],
            "relationships": [_serialize_value(r) for r in val.relationships],
        }
    if isinstance(val, list):
        return [_serialize_value(x) for x in val]
    if isinstance(val, dict):
        return {str(k): _serialize_value(v) for k, v in val.items()}
    return val


class CypherExecutor:
    """
    Stage 5: Controlled Read-Only Cypher Executor.
    - Always runs inside Neo4j `session.execute_read(...)`
    - Enforces strict transaction timeout (default 5.0s)
    - Separates complete evaluation records from display-truncated topology (<= 100 nodes / 500 edges)
    """

    def execute(
        self,
        cypher: str,
        params: Optional[Dict[str, Any]] = None,
        timeout_sec: Optional[float] = None,
        mechanism_hint: Optional[str] = None,
    ) -> ExecutionResult:
        t0 = time.perf_counter()
        timeout = timeout_sec or settings.neo4j_query_timeout_sec
        nodes_map: Dict[str, GraphNode] = {}
        edges_map: Dict[str, GraphEdge] = {}
        paths_list: List[PathEvidence] = []
        seen_paths: set = set()
        serialized_records: List[Dict[str, Any]] = []
        complete = True

        def _extract_graph_object(obj: Any) -> None:
            if isinstance(obj, Node):
                self._register_node(obj, nodes_map)
            elif isinstance(obj, Relationship):
                self._register_relationship(obj, nodes_map, edges_map, mechanism_hint)
            elif isinstance(obj, Path):
                p_nodes = []
                p_edges = []
                for n in obj.nodes:
                    gn = self._register_node(n, nodes_map)
                    p_nodes.append({"id": gn.id, "label": gn.label, "node_type": gn.node_type, "service": gn.service})
                mechs = set()
                for r in obj.relationships:
                    ge = self._register_relationship(r, nodes_map, edges_map, mechanism_hint)
                    mechs.add(ge.mechanism)
                    p_edges.append({"id": ge.id, "source": ge.source, "target": ge.target, "rel_type": ge.rel_type, "label": ge.label, "mechanism": ge.mechanism})
                sig = tuple(n["id"] for n in p_nodes) + tuple(e["rel_type"] for e in p_edges)
                if sig not in seen_paths and len(paths_list) < 200:
                    seen_paths.add(sig)
                    mech = mechanism_hint or ("MQ" if "MQ" in mechs else ("RPC" if "RPC" in mechs else "CALL"))
                    chain_str = " → ".join(n["label"] for n in p_nodes)
                    paths_list.append(
                        PathEvidence(
                            mechanism=mech,
                            hops=len(p_edges),
                            nodes=p_nodes,
                            edges=p_edges,
                            summary=chain_str,
                        )
                    )
            elif isinstance(obj, list):
                for item in obj:
                    _extract_graph_object(item)
            elif isinstance(obj, dict):
                for item in obj.values():
                    _extract_graph_object(item)

        try:
            with neo4j_manager.driver.session(database=settings.neo4j_database) as session:
                def _work(tx):
                    res = tx.run(cypher, params or {}, timeout=timeout)
                    rows = []
                    for idx, rec in enumerate(res):
                        if idx >= settings.max_result_rows:
                            nonlocal complete
                            complete = False
                            break
                        rows.append(dict(rec))
                    return rows

                raw_rows = session.execute_read(_work)

            for row in raw_rows:
                for val in row.values():
                    _extract_graph_object(val)
                serialized_records.append({k: _serialize_value(v) for k, v in row.items()})

            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            all_nodes = list(nodes_map.values())
            all_edges = list(edges_map.values())
            total_n, total_e = len(all_nodes), len(all_edges)
            truncated = False

            if total_n > settings.display_max_nodes:
                all_nodes = all_nodes[: settings.display_max_nodes]
                kept_ids = {n.id for n in all_nodes}
                all_edges = [e for e in all_edges if e.source in kept_ids and e.target in kept_ids]
                truncated = True
            if len(all_edges) > settings.display_max_edges:
                all_edges = all_edges[: settings.display_max_edges]
                truncated = True

            return ExecutionResult(
                records=serialized_records,
                total_records=len(serialized_records),
                nodes=all_nodes,
                edges=all_edges,
                paths=paths_list,
                display_truncated=truncated,
                total_nodes_before_truncation=total_n,
                total_edges_before_truncation=total_e,
                elapsed_ms=elapsed_ms,
                complete=complete,
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            return ExecutionResult(
                elapsed_ms=elapsed_ms,
                complete=False,
                error=str(exc),
            )

    def _register_node(self, node: Node, nodes_map: Dict[str, GraphNode]) -> GraphNode:
        props = dict(node)
        nid = str(props.get("nodeId") or node.element_id)
        if nid not in nodes_map:
            ntype = _pick_node_label(node)
            sctx = props.get("serviceContexts")
            svc = (
                props.get("服务名称")
                or props.get("所属服务名")
                or props.get("moduleId")
                or (sctx[0] if isinstance(sctx, list) and sctx else None)
            )
            nodes_map[nid] = GraphNode(
                id=nid,
                label=_pick_node_display(props, ntype),
                node_type=ntype,
                service=str(svc) if svc else None,
                properties=_compact_props(props),
            )
        return nodes_map[nid]

    def _register_relationship(
        self,
        rel: Relationship,
        nodes_map: Dict[str, GraphNode],
        edges_map: Dict[str, GraphEdge],
        mechanism_hint: Optional[str] = None,
    ) -> GraphEdge:
        start_gn = self._register_node(rel.start_node, nodes_map)
        end_gn = self._register_node(rel.end_node, nodes_map)
        props = dict(rel)
        eid = str(props.get("关系键") or f"{start_gn.id}->{rel.type}->{end_gn.id}")
        if eid not in edges_map:
            mech = mechanism_hint or _classify_mechanism(rel.type, props)
            edge_label = rel.type
            if rel.type == "消息流" and props.get("消息流类型"):
                rk = f" [{props['路由键']}]" if props.get("路由键") else ""
                edge_label = f"{props['消息流类型']}{rk}"
            elif rel.type == "调用" and props.get("关系语义") and props["关系语义"] != "方法调用":
                edge_label = str(props["关系语义"])
            edges_map[eid] = GraphEdge(
                id=eid,
                source=start_gn.id,
                target=end_gn.id,
                rel_type=rel.type,
                label=edge_label,
                mechanism=mech,
                properties={
                    k: props[k]
                    for k in ("关系语义", "调用类型", "消息流类型", "路由键", "目标服务名", "来源文件", "来源行号")
                    if k in props and props[k] is not None
                },
            )
        return edges_map[eid]


cypher_executor = CypherExecutor()
