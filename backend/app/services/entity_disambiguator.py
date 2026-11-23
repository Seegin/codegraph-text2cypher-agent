from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from app.core.config import settings
from app.models.schemas import EntityCandidate, ResolvedEntity, SchemaSnapshot
from app.pipeline.schema_fetcher import neo4j_manager


# Common English stop tokens inside Chinese/English mixed technical questions that are NOT method/class names
NON_ENTITY_TOKENS = {
    "API",
    "REST",
    "RPC",
    "MQ",
    "HTTP",
    "GET",
    "POST",
    "PUT",
    "DELETE",
    "PATCH",
    "HEAD",
    "URL",
    "JSON",
    "SQL",
    "YAML",
    "XML",
    "DB",
    "NEO4J",
    "CYPHER",
    "TOP",
    "TOP10",
    "TOP5",
    "ID",
    "UUID",
    "CONTROLLER",
    "SERVICE",
    "SERVICE_INTERFACE",
    "SERVICE_IMPL",
    "OTHER",
    "SPRING",
    "RABBITMQ",
    "BEAN",
    "CREATE",
    "MERGE",
    "MATCH",
    "RETURN",
    "WHERE",
    "DROP",
    "SET",
    "REMOVE",
    "CALL",
    "DBMS",
    "APOC",
}


class EntityDisambiguator:
    """
    Entity Recognition & Homonym Disambiguation against live Neo4j code graph:
    - Maps colloquial Chinese terms to real schema types
    - Recognizes microservices, queues, classes, qualified Class.method, and standalone methods
    - Queries live Neo4j for real candidates with full provenance evidence
      (nodeId, 所属类名, serviceContexts, 来源文件, 来源行号, 是否入口方法, 是否抽象方法)
    - Flags homonyms for interactive confirmation or resolves deterministically when configured
    """

    def resolve_entities(
        self,
        question: str,
        schema: SchemaSnapshot,
        scope: Optional[str] = None,
        selected_entity_ids: Optional[Dict[str, str]] = None,
        clarify_on_ambiguity: bool = True,
        direction_hint: str = "forward",
    ) -> List[ResolvedEntity]:
        selected_ids = selected_entity_ids or {}
        resolved: List[ResolvedEntity] = []
        seen_mentions: Set[str] = set()

        # 1. Extract microservice mentions (exact match against all 41 real microservices)
        mentioned_services: List[str] = []
        for svc in sorted(schema.microservices, key=len, reverse=True):
            if svc in question and svc not in mentioned_services:
                mentioned_services.append(svc)
        if scope and scope in schema.microservices and scope not in mentioned_services:
            mentioned_services.append(scope)

        for svc in mentioned_services:
            seen_mentions.add(svc)
            resolved.append(
                ResolvedEntity(
                    mention=svc,
                    entity_type="微服务",
                    status="bound",
                    bound=EntityCandidate(
                        node_id=f"svc:{svc}",
                        label="微服务",
                        name=svc,
                        service_contexts=[svc],
                        graph_version=schema.graph_version,
                    ),
                )
            )

        # 2. Extract message queue mentions
        known_queues = {q["queue_name"] for q in schema.mq_queues if q.get("queue_name")}
        for qn in sorted(known_queues, key=len, reverse=True):
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(qn)}(?![A-Za-z0-9_])", question):
                seen_mentions.add(qn)
                q_cands = [
                    EntityCandidate(
                        node_id=q["nodeId"],
                        label="消息队列",
                        name=q["queue_name"],
                        service_contexts=[q["service"]] if q.get("service") else [],
                        graph_version=schema.graph_version,
                    )
                    for q in schema.mq_queues
                    if q.get("queue_name") == qn
                ]
                resolved.append(
                    ResolvedEntity(
                        mention=qn,
                        entity_type="消息队列",
                        status="bound",
                        bound=q_cands[0],
                        candidates=q_cands,
                    )
                )

        # 3. Extract qualified Class.method mentions (e.g., FoodController.getAllFood, RouteController.queryById)
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\.([a-zA-Z_][A-Za-z0-9_]*)\b", question):
            full_mention = m.group(0)
            cls_part, meth_part = m.group(1), m.group(2)
            if full_mention in seen_mentions:
                continue
            seen_mentions.add(full_mention)
            seen_mentions.add(cls_part)
            seen_mentions.add(meth_part)
            cands = self._lookup_methods(meth_part, class_hint=cls_part, services=mentioned_services)
            resolved.append(
                self._finalize_resolution(
                    full_mention, "方法", cands, selected_ids, clarify_on_ambiguity, direction_hint
                )
            )

        # 4. Extract Class names (PascalCase tokens not already matched)
        for m in re.finditer(r"\b([A-Z][a-zA-Z0-9_]{2,})\b", question):
            tok = m.group(1)
            if tok.upper() in NON_ENTITY_TOKENS or tok in seen_mentions:
                continue
            # Check if it's part of a service name like ts-xxx
            if any(tok in s for s in mentioned_services):
                continue
            cands = self._lookup_classes(tok, services=mentioned_services)
            if cands:
                seen_mentions.add(tok)
                resolved.append(
                    self._finalize_resolution(
                        tok, "类", cands, selected_ids, clarify_on_ambiguity, direction_hint
                    )
                )

        # 5. Extract standalone Method names (camelCase or snake_case tokens, or explicit '方法 X' / 'X 方法')
        method_tokens: List[str] = []
        # Pattern: camelCase (starts lowercase, has uppercase or underscore) or explicitly next to 方法/函数
        for m in re.finditer(r"(?<![A-Za-z0-9_-])([a-z][a-z0-9]*[A-Z][A-Za-z0-9_]*|[a-z]+_[a-z0-9_]+)(?![A-Za-z0-9_-])", question):
            t = m.group(1)
            if t not in seen_mentions and t.upper() not in NON_ENTITY_TOKENS:
                method_tokens.append(t)
        for m in re.finditer(r"(?:方法|函数)\s*['\"`]?([a-zA-Z_][A-Za-z0-9_]*)['\"`]?|['\"`]?([a-zA-Z_][A-Za-z0-9_]*)['\"`]?\s*(?:方法|函数)", question):
            t = m.group(1) or m.group(2)
            if t and t not in seen_mentions and t not in method_tokens and t.upper() not in NON_ENTITY_TOKENS:
                method_tokens.append(t)

        for tok in method_tokens:
            if tok in seen_mentions or any(tok in s for s in mentioned_services):
                continue
            cands = self._lookup_methods(tok, services=mentioned_services)
            if cands:
                seen_mentions.add(tok)
                resolved.append(
                    self._finalize_resolution(
                        tok, "方法", cands, selected_ids, clarify_on_ambiguity, direction_hint
                    )
                )

        return resolved

    def _lookup_methods(
        self,
        method_name: str,
        class_hint: Optional[str] = None,
        services: Optional[List[str]] = None,
    ) -> List[EntityCandidate]:
        cypher = """
        MATCH (m:`方法` {`方法名`: $name})
        OPTIONAL MATCH (m)-[:`归属于`]->(c:`类`)
        RETURN m.nodeId AS node_id,
               m.`方法名` AS name,
               m.`全限定名` AS qualified_name,
               m.`所属类名` AS class_name,
               m.serviceContexts AS service_contexts,
               m.moduleId AS module_id,
               m.`来源文件` AS source_file,
               m.`来源行号` AS source_line,
               m.`是否入口方法` AS is_entry,
               m.`是否抽象方法` AS is_abstract,
               m.`图谱版本` AS graph_version,
               c.`类类型` AS class_type
        ORDER BY m.`是否入口方法` DESC, m.`是否抽象方法` ASC, m.`全限定名` ASC
        LIMIT 25
        """
        rows = self._run_ro(cypher, {"name": method_name})
        cands = [
            EntityCandidate(
                node_id=str(r["node_id"]),
                label="方法",
                name=str(r["name"]),
                qualified_name=r.get("qualified_name"),
                class_name=r.get("class_name"),
                service_contexts=list(r.get("service_contexts") or []),
                module_id=r.get("module_id"),
                source_file=r.get("source_file"),
                source_line=r.get("source_line"),
                is_entry=r.get("is_entry"),
                is_abstract=r.get("is_abstract"),
                graph_version=r.get("graph_version"),
                extra={"class_type": r.get("class_type")},
            )
            for r in rows
        ]
        if class_hint:
            narrowed = [
                c
                for c in cands
                if c.class_name
                and (c.class_name.endswith("." + class_hint) or c.class_name == class_hint)
            ]
            if narrowed:
                cands = narrowed
        if services:
            narrowed = [c for c in cands if any(s in c.service_contexts for s in services)]
            if narrowed:
                cands = narrowed
        return cands

    def _lookup_classes(
        self,
        class_name: str,
        services: Optional[List[str]] = None,
    ) -> List[EntityCandidate]:
        cypher = """
        MATCH (c:`类`)
        WHERE c.`简名` = $name OR c.`全限定名` = $name
        RETURN c.nodeId AS node_id,
               c.`简名` AS name,
               c.`全限定名` AS qualified_name,
               c.serviceContexts AS service_contexts,
               c.moduleId AS module_id,
               c.`来源文件` AS source_file,
               c.`来源行号` AS source_line,
               c.`是否接口` AS is_abstract,
               c.`图谱版本` AS graph_version,
               c.`类类型` AS class_type
        ORDER BY c.`全限定名` ASC
        LIMIT 20
        """
        rows = self._run_ro(cypher, {"name": class_name})
        cands = [
            EntityCandidate(
                node_id=str(r["node_id"]),
                label="类",
                name=str(r["name"]),
                qualified_name=r.get("qualified_name"),
                class_name=r.get("qualified_name"),
                service_contexts=list(r.get("service_contexts") or []),
                module_id=r.get("module_id"),
                source_file=r.get("source_file"),
                source_line=r.get("source_line"),
                is_abstract=r.get("is_abstract"),
                graph_version=r.get("graph_version"),
                extra={"class_type": r.get("class_type")},
            )
            for r in rows
        ]
        if services:
            narrowed = [c for c in cands if any(s in c.service_contexts for s in services)]
            if narrowed:
                cands = narrowed
        return cands

    def _finalize_resolution(
        self,
        mention: str,
        entity_type: str,
        candidates: List[EntityCandidate],
        selected_ids: Dict[str, str],
        clarify_on_ambiguity: bool,
        direction_hint: str,
    ) -> ResolvedEntity:
        if not candidates:
            return ResolvedEntity(mention=mention, entity_type=entity_type, status="not_found")

        # User already explicitly chose a nodeId for this mention
        chosen_id = selected_ids.get(mention)
        if chosen_id:
            for c in candidates:
                if c.node_id == chosen_id:
                    return ResolvedEntity(
                        mention=mention,
                        entity_type=entity_type,
                        status="bound",
                        bound=c,
                        candidates=candidates,
                    )

        if len(candidates) == 1:
            return ResolvedEntity(
                mention=mention,
                entity_type=entity_type,
                status="bound",
                bound=candidates[0],
                candidates=candidates,
            )

        # Multiple candidates detected!
        # Filter out pure abstract interface duplicates if non-abstract concrete implementations exist?
        # Wait: in interactive clarify mode, expose real homonyms so the user sees full evidence and confirms!
        if clarify_on_ambiguity:
            return ResolvedEntity(
                mention=mention,
                entity_type=entity_type,
                status="ambiguous",
                candidates=candidates[:12],
            )

        # Auto-disambiguation mode (batch / evaluation): pick best concrete candidate deterministically
        best = self._rank_best_candidate(candidates, direction_hint)
        return ResolvedEntity(
            mention=mention,
            entity_type=entity_type,
            status="bound",
            bound=best,
            candidates=candidates[:12],
        )

    def _rank_best_candidate(
        self, candidates: List[EntityCandidate], direction_hint: str
    ) -> EntityCandidate:
        def score(c: EntityCandidate) -> Tuple[int, int, int, str]:
            ctype = str(c.extra.get("class_type") or "")
            is_abs = 1 if c.is_abstract else 0
            if direction_hint == "reverse":
                # For reverse impact, prefer SERVICE_IMPL or CONTROLLER concrete method over interface
                role_rank = {"SERVICE_IMPL": 0, "CONTROLLER": 1, "OTHER": 2, "SERVICE_INTERFACE": 3}.get(ctype, 2)
            else:
                # For forward downstream, prefer CONTROLLER (entry) -> SERVICE_IMPL -> OTHER -> INTERFACE
                role_rank = {"CONTROLLER": 0, "SERVICE_IMPL": 1, "OTHER": 2, "SERVICE_INTERFACE": 3}.get(ctype, 2)
            entry_rank = 0 if c.is_entry else 1
            return (is_abs, role_rank, entry_rank, c.qualified_name or c.node_id)

        return sorted(candidates, key=score)[0]

    def _run_ro(self, cypher: str, params: Dict[str, object]) -> List[Dict[str, object]]:
        try:
            with neo4j_manager.driver.session(database=settings.neo4j_database) as session:
                return session.execute_read(lambda tx: [dict(r) for r in tx.run(cypher, params, timeout=3.0)])
        except Exception:
            return []


entity_disambiguator = EntityDisambiguator()
