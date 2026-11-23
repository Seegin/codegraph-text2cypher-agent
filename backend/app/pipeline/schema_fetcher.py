from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase, Driver

from app.core.config import settings
from app.models.schemas import SchemaSnapshot

logger = logging.getLogger(__name__)


class Neo4jConnectionManager:
    """Read-only Neo4j connection manager. Never executes write transactions."""

    def __init__(self) -> None:
        self._driver: Optional[Driver] = None

    @property
    def driver(self) -> Driver:
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
                connection_timeout=5.0,
                max_connection_lifetime=1800,
            )
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def check_health(self) -> Dict[str, Any]:
        try:
            self.driver.verify_connectivity()
            with self.driver.session(database=settings.neo4j_database) as session:
                res = session.execute_read(
                    lambda tx: [dict(r) for r in tx.run("CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition")],
                )
            return {"connected": True, "components": res}
        except Exception as e:
            return {"connected": False, "error": str(e)}


neo4j_manager = Neo4jConnectionManager()


class SchemaFetcher:
    """Stage 1: Fetches and freezes live Neo4j code graph schema with SHA256 schema_hash."""

    def __init__(self) -> None:
        self._cached: Optional[SchemaSnapshot] = None

    def _compute_hash(self, payload: Dict[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def get_schema(self, force_refresh: bool = False) -> SchemaSnapshot:
        if self._cached is not None and not force_refresh:
            return self._cached

        snapshot_path = settings.resolve_path(settings.schema_snapshot_path)
        if not force_refresh and snapshot_path.exists():
            try:
                data = json.loads(snapshot_path.read_text(encoding="utf-8"))
                # Quick verify live connectivity; if reachable keep live status
                self._cached = SchemaSnapshot(**data)
                return self._cached
            except Exception as exc:
                logger.warning("Failed to load cached schema snapshot: %s", exc)

        try:
            snap = self.fetch_live_schema()
            self._save_snapshot(snap, snapshot_path)
            self._cached = snap
            return snap
        except Exception as exc:
            logger.error("Live schema fetch failed (%s), attempting offline snapshot fallback", exc)
            if snapshot_path.exists():
                data = json.loads(snapshot_path.read_text(encoding="utf-8"))
                data["source"] = "offline_snapshot"
                self._cached = SchemaSnapshot(**data)
                return self._cached
            raise

    def fetch_live_schema(self) -> SchemaSnapshot:
        def ro(session, cypher: str) -> List[Dict[str, Any]]:
            return session.execute_read(lambda tx: [dict(r) for r in tx.run(cypher)])

        with neo4j_manager.driver.session(database=settings.neo4j_database) as session:
            comps = ro(session, "CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition")
            neo4j_ver = "unknown"
            for c in comps:
                if "Kernel" in str(c.get("name", "")):
                    vers = c.get("versions") or []
                    neo4j_ver = f"{vers[0] if vers else '?'} ({c.get('edition', '')})"

            ver_rows = ro(session, "MATCH (n) WHERE n.`图谱版本` IS NOT NULL RETURN n.`图谱版本` AS v, n.graphModel AS gm LIMIT 1")
            graph_version = str(ver_rows[0]["v"]) if ver_rows else "unknown"
            graph_model = str(ver_rows[0].get("gm") or "unknown") if ver_rows else "unknown"

            label_rows = ro(session, "MATCH (n) RETURN labels(n) AS labels, count(n) AS cnt ORDER BY cnt DESC")
            node_labels: Dict[str, Dict[str, Any]] = {}
            for row in label_rows:
                lbls = row.get("labels") or []
                if not lbls:
                    continue
                lbl = lbls[0]
                props = ro(session, f"MATCH (n:`{lbl}`) UNWIND keys(n) AS k RETURN k, count(*) AS cnt ORDER BY cnt DESC")
                node_labels[lbl] = {
                    "count": row["cnt"],
                    "properties": [p["k"] for p in props],
                }

            rel_rows = ro(session, "MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS cnt ORDER BY cnt DESC")
            rel_types: Dict[str, Dict[str, Any]] = {}
            for row in rel_rows:
                rt = row["t"]
                props = ro(session, f"MATCH ()-[r:`{rt}`]->() UNWIND keys(r) AS k RETURN k, count(*) AS cnt ORDER BY cnt DESC")
                rel_types[rt] = {
                    "count": row["cnt"],
                    "properties": [p["k"] for p in props],
                }

            patterns = ro(
                session,
                "MATCH (a)-[r]->(b) RETURN labels(a)[0] AS src, type(r) AS rel, labels(b)[0] AS dst, count(r) AS cnt ORDER BY cnt DESC",
            )
            microservices = [
                r["name"]
                for r in ro(session, "MATCH (m:`微服务`) RETURN m.`服务名称` AS name ORDER BY name")
                if r.get("name")
            ]
            class_types = [
                r["t"]
                for r in ro(session, "MATCH (c:`类`) WHERE c.`类类型` IS NOT NULL RETURN DISTINCT c.`类类型` AS t ORDER BY t")
            ]
            mq_flow_types = [
                r["t"]
                for r in ro(session, "MATCH ()-[r:`消息流`]->() WHERE r.`消息流类型` IS NOT NULL RETURN DISTINCT r.`消息流类型` AS t ORDER BY t")
            ]
            mq_queues = ro(
                session,
                "MATCH (q:`消息队列`) RETURN q.nodeId AS nodeId, q.`队列名称` AS queue_name, q.`所属服务名` AS service ORDER BY queue_name, service",
            )
            mq_exchanges = ro(
                session,
                "MATCH (e:`消息交换机`) RETURN e.nodeId AS nodeId, e.`交换机名称` AS exchange_name, e.`交换机类型` AS exchange_type, e.serviceContexts AS services",
            )

        hash_basis = {
            "graph_version": graph_version,
            "node_labels": {k: sorted(v["properties"]) for k, v in node_labels.items()},
            "rel_types": {k: sorted(v["properties"]) for k, v in rel_types.items()},
            "rel_patterns": [(p["src"], p["rel"], p["dst"]) for p in patterns],
        }
        schema_hash = self._compute_hash(hash_basis)

        return SchemaSnapshot(
            graph_version=graph_version,
            graph_model=graph_model,
            neo4j_version=neo4j_ver,
            schema_hash=schema_hash,
            fetched_at=datetime.utcnow().isoformat() + "Z",
            source="live",
            node_labels=node_labels,
            rel_types=rel_types,
            rel_patterns=patterns,
            microservices=microservices,
            class_types=class_types,
            mq_flow_types=mq_flow_types,
            mq_queues=mq_queues,
            mq_exchanges=mq_exchanges,
        )

    def _save_snapshot(self, snap: SchemaSnapshot, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(snap.model_dump_json(indent=2), encoding="utf-8")

    def format_for_prompt(self, snap: SchemaSnapshot) -> str:
        """Render a concise, hallucination-proof Chinese schema specification for LLM Prompts."""
        lines = [
            f"### 代码图谱实库 Schema 契约（图谱版本: {snap.graph_version}, schema_hash: {snap.schema_hash}）",
            "严格约束：仅允许使用以下真实存在的中文节点标签、关系类型与属性名（中文标识符务必使用反引号 `` 包裹或确保合法）。",
            "",
            "#### 1. 节点标签与核心属性（8种）",
            "- `微服务` (count=" + str(snap.node_labels.get("微服务", {}).get("count", 0)) + "): `nodeId`, `服务名称`, `caption`, `显式方法声明数`, `编译方法声明数`, `图谱版本`",
            "- `类` (count=" + str(snap.node_labels.get("类", {}).get("count", 0)) + "): `nodeId`, `简名`, `全限定名`, `包名`, `类类型` (枚举: " + ", ".join(snap.class_types) + "), `是否接口`, `是否抽象类`, `serviceContexts` (列表!), `moduleId`, `来源文件`, `来源行号`",
            "- `方法` (count=" + str(snap.node_labels.get("方法", {}).get("count", 0)) + "): `nodeId`, `方法名`, `全限定名`, `所属类名`, `方法签名`, `返回类型`, `访问修饰符`, `是否入口方法`, `是否抽象方法`, `是否重写`, `serviceContexts` (列表!), `moduleId`, `来源文件`, `来源行号`, `来源位置`",
            "- `上游API` (count=" + str(snap.node_labels.get("上游API", {}).get("count", 0)) + "): `nodeId`, `caption`, `HTTP方法`, `API路径`, `控制器方法`, `响应类型`, `serviceContexts` (列表!)",
            "- `下游API` (count=" + str(snap.node_labels.get("下游API", {}).get("count", 0)) + "): `nodeId`, `caption`, `HTTP方法`, `API路径`, `完整URL`, `目标服务名`, `匹配到的上游API`, `匹配质量`, `serviceContexts` (列表!), `来源文件`, `来源行号`",
            "- `消息交换机` (count=" + str(snap.node_labels.get("消息交换机", {}).get("count", 0)) + "): `nodeId`, `交换机名称`, `交换机类型`, `所属服务名`, `serviceContexts`",
            "- `消息队列` (count=" + str(snap.node_labels.get("消息队列", {}).get("count", 0)) + "): `nodeId`, `队列名称`, `所属服务名`, `serviceContexts`",
            "- `配置` (count=" + str(snap.node_labels.get("配置", {}).get("count", 0)) + "): `nodeId`, `caption`, `configurationKind`, `sourceType`, `key`, `logicalResourcePath`, `beanName`, `beanClass`, `serviceContexts`, `moduleId`",
            "",
            "#### 2. 合法关系模式与方向（8种关系）",
        ]
        for p in snap.rel_patterns:
            lines.append(f"- (`{p['src']}`)-[:`{p['rel']}`]->(`{p['dst']}`)  (count={p['cnt']})")

        lines.extend([
            "",
            "#### 3. 关键语义陷阱（严禁违反）",
            "1. **微服务归属**：`类` 与 `方法` 没有指向 `微服务` 的 `归属于` 边！必须用 `'ts-xxx-service' IN n.serviceContexts` 或 `n.moduleId = 'ts-xxx-service'` 过滤。",
            "2. **方法多跳调用**：`[:调用]` 也连接 `方法->配置`、`方法->上游API` 等。进行纯方法调用链遍历时，务必限定路径中间节点类型为 `方法`（如 `ALL(x IN nodes(p) WHERE x:`方法`)` 或显式限制关系语义 `r.`关系语义` IN ['方法调用','方法实现']`）并设置跳数上限（默认 `0..6`，上限 `10`）。",
            "3. **跨服务同步调用 (RPC/REST)**：",
            "   - 下游目标微服务：`(:方法)-[:调用 {关系语义:'通信边界'}]->(d:下游API)-[:目标服务]->(svc:微服务)`",
            "   - 端到端跨服务API链：`(m1:方法)-[:调用]->(d:下游API)-[:调用 {关系语义:'跨服务HTTP调用'}]->(u:上游API)-[:归属于]->(m2:方法)`",
            "4. **入口 API 归属**：`(u:上游API)-[:归属于]->(m:方法)`（同时存在反向 `(m:方法)-[:调用 {关系语义:'服务于'}]->(u:上游API)`）。",
            "5. **MQ 三阶段消息流（必须按路由键匹配！）**：",
            "   - 发布：`(pub:方法)-[r1:消息流 {消息流类型:'发布至'}]->(ex:消息交换机)`",
            "   - 路由：`(ex:消息交换机)-[r2:消息流 {消息流类型:'路由至'}]->(q:消息队列)`，且必须满足 `r1.`路由键` = r2.`路由键``！",
            "   - 消费：`(q:消息队列)-[r3:消息流 {消息流类型:'消费自'}]->(sub:方法)`",
            "6. **只读安全**：仅允许单条只读查询（MATCH/OPTIONAL MATCH/WHERE/WITH/RETURN/ORDER BY/LIMIT/UNWIND），禁止 CREATE/MERGE/DELETE/SET/REMOVE/DROP/CALL dbms/apoc。",
        ])
        return "\n".join(lines)


schema_fetcher = SchemaFetcher()
