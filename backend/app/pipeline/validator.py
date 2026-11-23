from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.config import settings
from app.models.schemas import SafetyLayerResult, SchemaSnapshot, ValidationReport
from app.pipeline.schema_fetcher import neo4j_manager


# Strictly forbidden write / DDL / admin keywords (checked outside strings & backticks)
FORBIDDEN_KEYWORDS = {
    "CREATE",
    "MERGE",
    "DELETE",
    "DETACH",
    "SET",
    "REMOVE",
    "DROP",
    "FOREACH",
    "ALTER",
    "GRANT",
    "REVOKE",
    "DENY",
    "TERMINATE",
    "IMPORT",
}

FORBIDDEN_PHRASES = [
    r"\bLOAD\s+CSV\b",
    r"\bSHOW\s+USERS\b",
    r"\bSHOW\s+ROLES\b",
    r"\bSHOW\s+PRIVILEGES\b",
    r"\bSHOW\s+DATABASES\b",
    r"\bCREATE\s+USER\b",
    r"\bALTER\s+USER\b",
    r"\bSTART\s+DATABASE\b",
    r"\bSTOP\s+DATABASE\b",
    r"\bENABLE\s+SERVER\b",
    r"\bDISABLE\s+SERVER\b",
    r"\bRENAME\s+(USER|ROLE)\b",
    r"\bUSING\s+PERIODIC\s+COMMIT\b",
]

# Neo4j EXPLAIN write operators that must NEVER appear in a read-only query plan
WRITE_PLAN_OPERATORS = {
    "Create",
    "CreateNode",
    "CreateRelationship",
    "Merge",
    "MergeNode",
    "MergeRelationship",
    "Delete",
    "DetachDelete",
    "SetProperty",
    "SetNodeProperty",
    "SetRelationshipProperty",
    "SetLabels",
    "RemoveLabels",
    "DropIndex",
    "DropConstraint",
    "CreateIndex",
    "CreateConstraint",
    "LoadCSV",
    "Foreach",
    "AdministrationCommand",
}

ALLOWED_START_KEYWORDS = {"MATCH", "OPTIONAL", "WITH", "UNWIND", "CALL", "RETURN"}


def strip_comments_and_strings(cypher: str) -> Tuple[str, List[str]]:
    """
    Lexical scanner: removes // and /* */ comments and replaces quoted string literals
    with empty placeholders, preserving backtick identifiers separately.
    Returns (code_without_strings_or_comments, extracted_backtick_identifiers).
    """
    out: List[str] = []
    backticks: List[str] = []
    i = 0
    n = len(cypher)
    while i < n:
        ch = cypher[i]
        # Line comment //
        if ch == "/" and i + 1 < n and cypher[i + 1] == "/":
            i += 2
            while i < n and cypher[i] != "\n":
                i += 1
            out.append(" ")
            continue
        # Block comment /* ... */
        if ch == "/" and i + 1 < n and cypher[i + 1] == "*":
            i += 2
            while i + 1 < n and not (cypher[i] == "*" and cypher[i + 1] == "/"):
                i += 1
            i += 2
            out.append(" ")
            continue
        # Single or double quoted string
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                if cypher[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if cypher[i] == quote:
                    i += 1
                    break
                i += 1
            out.append(" '' ")
            continue
        # Backtick escaped identifier
        if ch == "`":
            i += 1
            buf = []
            while i < n:
                if cypher[i] == "`":
                    if i + 1 < n and cypher[i + 1] == "`":
                        buf.append("`")
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(cypher[i])
                i += 1
            ident = "".join(buf)
            backticks.append(ident)
            out.append(f"`{ident}`")
            continue
        out.append(ch)
        i += 1
    return "".join(out), backticks


def _strip_backticks_for_keyword_scan(code: str) -> str:
    return re.sub(r"`[^`]*`", " __IDENT__ ", code)


class CypherValidator:
    """
    Multi-layer Read-Only Safety & Schema Validator (Appendix E):
    - Layer 1: Non-empty, single statement, bounded hops
    - Layer 2: Structural read-only whitelist & procedure ban
    - Layer 3a: Live Schema label/relationship/property validation
    - Layer 3b: Neo4j EXPLAIN query plan verification (zero write operators)
    """

    def validate(
        self,
        cypher: str,
        params: Optional[Dict[str, Any]],
        schema: SchemaSnapshot,
        run_explain: bool = True,
    ) -> ValidationReport:
        layers: List[SafetyLayerResult] = []
        cleaned = (cypher or "").strip()

        # --- Layer 1: Input & Single-Statement Structural Guard ---
        l1_violations: List[str] = []
        if not cleaned:
            l1_violations.append("Cypher 语句为空")
        if len(cleaned) > 12000:
            l1_violations.append("Cypher 语句长度超出安全上限 (12000 chars)")

        code_no_str, _ = strip_comments_and_strings(cleaned) if cleaned else ("", [])
        # Strip a single harmless trailing semicolon
        trimmed_code = code_no_str.strip()
        if trimmed_code.endswith(";"):
            trimmed_code = trimmed_code[:-1].strip()
        if ";" in trimmed_code:
            l1_violations.append("检测到多语句分号分隔注入，仅允许单条只读查询")

        # Check variable-length hop bounds
        for m in re.finditer(r"\*\s*(\d*)\s*(\.\.)?\s*(\d*)", trimmed_code):
            full = m.group(0)
            low_s, dots, high_s = m.group(1), m.group(2), m.group(3)
            # Only trigger inside relationship brackets [:...*...]
            start_pos = m.start()
            ctx_left = trimmed_code[max(0, start_pos - 60):start_pos]
            if "[" in ctx_left and "]" not in ctx_left.split("[")[-1]:
                if not low_s and not high_s:
                    l1_violations.append(f"禁止无界可变长路径 '{full}'，必须显式设置跳数上限 (<= {settings.hard_max_hops})")
                elif dots and not high_s:
                    l1_violations.append(f"禁止无上限可变长路径 '{full}'，上限不得超过 {settings.hard_max_hops}")
                else:
                    hi = int(high_s) if high_s else int(low_s)
                    if hi > settings.hard_max_hops:
                        l1_violations.append(f"路径跳数上限 {hi} 超出系统硬限制 ({settings.hard_max_hops})")

        l1_passed = len(l1_violations) == 0
        layers.append(
            SafetyLayerResult(
                layer="L1_input_and_bounds",
                passed=l1_passed,
                message="单语句与路径预算检查通过" if l1_passed else "; ".join(l1_violations),
                violations=l1_violations,
            )
        )
        if not l1_passed:
            return ValidationReport(
                passed=False,
                layers=layers,
                violation_category="INPUT_OR_BOUNDS_VIOLATION",
            )

        # --- Layer 2: Structural Read-Only Whitelist & Unauthorized Procedure Block ---
        l2_violations: List[str] = []
        kw_scan_text = _strip_backticks_for_keyword_scan(trimmed_code)
        upper_text = kw_scan_text.upper()

        # Check leading clause whitelist
        first_tok_m = re.match(r"^\s*([A-Z]+)", upper_text)
        if not first_tok_m or first_tok_m.group(1) not in ALLOWED_START_KEYWORDS:
            tok = first_tok_m.group(1) if first_tok_m else "<NONE>"
            l2_violations.append(f"非法起始子句 '{tok}'，只读白名单仅允许 {sorted(ALLOWED_START_KEYWORDS)}")

        # Check forbidden write/admin keywords
        tokens = set(re.findall(r"\b[A-Z_]+\b", upper_text))
        hit_kw = sorted(tokens & FORBIDDEN_KEYWORDS)
        if hit_kw:
            l2_violations.append(f"检测到禁止的写入/管理关键字: {', '.join(hit_kw)}")

        for pat in FORBIDDEN_PHRASES:
            if re.search(pat, upper_text):
                l2_violations.append(f"检测到禁止的管理/外部加载子句匹配: {pat}")

        # Block ALL procedure CALL <namespace.proc>(...), while permitting CALL { ... } read subqueries
        for m in re.finditer(r"\bCALL\b\s*([^\s({]+|\{)", kw_scan_text, re.IGNORECASE):
            after = m.group(1).strip()
            if after != "{":
                l2_violations.append(f"禁止调用未经授权的过程: CALL {after}")

        # Must contain a RETURN clause
        if not re.search(r"\bRETURN\b", upper_text):
            l2_violations.append("只读查询必须包含 RETURN 子句")

        l2_passed = len(l2_violations) == 0
        layers.append(
            SafetyLayerResult(
                layer="L2_readonly_whitelist",
                passed=l2_passed,
                message="静态只读结构白名单校验通过" if l2_passed else "; ".join(l2_violations),
                violations=l2_violations,
            )
        )
        if not l2_passed:
            return ValidationReport(
                passed=False,
                layers=layers,
                violation_category="WRITE_OR_PROCEDURE_BLOCKED",
            )

        # --- Layer 3a: Live Schema Label & Relationship Validation ---
        l3a_violations, schema_details = self._validate_against_schema(trimmed_code, schema)
        l3a_passed = len(l3a_violations) == 0
        layers.append(
            SafetyLayerResult(
                layer="L3a_schema_contract",
                passed=l3a_passed,
                message="Schema 标签/关系/属性校验通过" if l3a_passed else "; ".join(l3a_violations),
                violations=l3a_violations,
                details=schema_details,
            )
        )
        if not l3a_passed:
            return ValidationReport(
                passed=False,
                layers=layers,
                violation_category="SCHEMA_VIOLATION",
            )

        # --- Layer 3b: Neo4j EXPLAIN Pre-check (Syntax + Plan Write-Operator Verification) ---
        explain_summary: Optional[Dict[str, Any]] = None
        if run_explain:
            l3b_violations, explain_summary = self._run_explain_check(cleaned.rstrip(";"), params or {})
            l3b_passed = len(l3b_violations) == 0
            layers.append(
                SafetyLayerResult(
                    layer="L3b_neo4j_explain",
                    passed=l3b_passed,
                    message="Neo4j EXPLAIN 预检通过（只读执行计划合法）" if l3b_passed else "; ".join(l3b_violations),
                    violations=l3b_violations,
                    details=explain_summary or {},
                )
            )
            if not l3b_passed:
                return ValidationReport(
                    passed=False,
                    layers=layers,
                    violation_category="EXPLAIN_OR_SYNTAX_ERROR",
                    explain_summary=explain_summary,
                )

        return ValidationReport(
            passed=True,
            layers=layers,
            explain_summary=explain_summary,
            sanitized_query=cleaned.rstrip(";"),
        )

    def _validate_against_schema(self, code: str, schema: SchemaSnapshot) -> Tuple[List[str], Dict[str, Any]]:
        violations: List[str] = []
        valid_labels: Set[str] = set(schema.node_labels.keys())
        valid_rels: Set[str] = set(schema.rel_types.keys())
        all_valid_props: Set[str] = set()
        for info in schema.node_labels.values():
            all_valid_props.update(info.get("properties", []))
        for info in schema.rel_types.values():
            all_valid_props.update(info.get("properties", []))

        # Extract node labels inside (...) patterns: (var:`Label`) or (:Label)
        found_labels: Set[str] = set()
        for node_pat in re.finditer(r"\(([^()]*)\)", code):
            inner = node_pat.group(1)
            # Strip map literals { ... } inside node pattern before scanning labels
            inner_no_map = re.sub(r"\{[^{}]*\}", "", inner)
            # Labels appear after : (possibly chained :A:B)
            for lbl_m in re.finditer(r":\s*(?:`([^`]+)`|([A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*))", inner_no_map):
                lbl = lbl_m.group(1) or lbl_m.group(2)
                if lbl:
                    found_labels.add(lbl)

        # Extract relationship types inside [...] patterns
        found_rels: Set[str] = set()
        for rel_pat in re.finditer(r"\[([^\[\]]*)\]", code):
            inner = rel_pat.group(1)
            # Skip array indexing like x[0] or list comprehensions [x IN list | ...]
            if ":" not in inner:
                continue
            inner_no_map = re.sub(r"\{[^{}]*\}", "", inner)
            # Take part after first colon up to * or end
            after_colon = inner_no_map.split(":", 1)[1]
            after_colon = after_colon.split("*", 1)[0]
            for part in after_colon.split("|"):
                part = part.strip().lstrip(":")
                m = re.match(r"^(?:`([^`]+)`|([A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*))$", part)
                if m:
                    rt = m.group(1) or m.group(2)
                    if rt:
                        found_rels.add(rt)

        unknown_labels = sorted(found_labels - valid_labels)
        unknown_rels = sorted(found_rels - valid_rels)
        if unknown_labels:
            violations.append(
                f"使用了图谱中不存在的节点标签: {unknown_labels}（合法标签: {sorted(valid_labels)}）"
            )
        if unknown_rels:
            violations.append(
                f"使用了图谱中不存在的关系类型: {unknown_rels}（合法关系: {sorted(valid_rels)}）"
            )

        # Extract property accesses var.`prop` or var.prop (excluding map aliases/numbers)
        found_props: Set[str] = set()
        # Capture variables bound in MATCH/OPTIONAL MATCH/UNWIND
        bound_vars: Set[str] = set()
        for m in re.finditer(r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::|\{|\))", code):
            bound_vars.add(m.group(1))
        for m in re.finditer(r"\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::|\*|\{|\])", code):
            bound_vars.add(m.group(1))

        for prop_m in re.finditer(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\.(?:`([^`]+)`|([A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*))",
            code,
        ):
            var_name = prop_m.group(1)
            prop_name = prop_m.group(2) or prop_m.group(3)
            if var_name in bound_vars and prop_name:
                found_props.add(prop_name)

        unknown_props = sorted(found_props - all_valid_props)
        if unknown_props:
            violations.append(f"引用了图谱中不存在的属性名: {unknown_props}")

        return violations, {
            "checked_labels": sorted(found_labels),
            "checked_rels": sorted(found_rels),
            "checked_props": sorted(found_props),
        }

    def _run_explain_check(
        self, cypher: str, params: Dict[str, Any]
    ) -> Tuple[List[str], Dict[str, Any]]:
        violations: List[str] = []
        summary_info: Dict[str, Any] = {}
        try:
            with neo4j_manager.driver.session(database=settings.neo4j_database) as session:
                def _explain(tx):
                    res = tx.run(f"EXPLAIN {cypher}", params)
                    return res.consume()

                consumed = session.execute_read(_explain)
                plan = consumed.plan
                operators: List[str] = []

                def walk(p):
                    if p is None:
                        return
                    op = getattr(p, "operator_type", None) or (p.get("operatorType") if isinstance(p, dict) else None)
                    if op:
                        operators.append(str(op))
                    children = getattr(p, "children", None) or (p.get("children") if isinstance(p, dict) else [])
                    for ch in children or []:
                        walk(ch)

                walk(plan)
                summary_info["operators"] = operators
                summary_info["query_type"] = getattr(consumed, "query_type", "r")
                gql_objs = getattr(consumed, "gql_status_objects", None) or []
                summary_info["notifications"] = [
                    getattr(n, "status_description", str(n))
                    for n in gql_objs
                    if getattr(n, "gql_status", "") not in ("00000", "00001", "02000")
                ]
                # Catch missing query parameters at EXPLAIN time so bounded repair can fix them before execution!
                for n in gql_objs:
                    if getattr(n, "gql_status", "") == "01N60":
                        violations.append(
                            f"EXPLAIN 检测到未提供查询参数: {getattr(n, 'status_description', 'missing parameter')}（请补全 params 字典或直接使用 WITH 变量）"
                        )

                if summary_info["query_type"] not in ("r", None, ""):
                    violations.append(
                        f"Neo4j EXPLAIN 判定查询类型为 '{summary_info['query_type']}'（非纯只读 'r'），已拦截"
                    )

                for op in operators:
                    base_op = op.split("@")[0]
                    if base_op in WRITE_PLAN_OPERATORS:
                        violations.append(f"EXPLAIN 计划包含写入算子 '{op}'，已拦截")

        except Exception as exc:
            violations.append(f"Neo4j EXPLAIN 预检失败: {exc}")
            summary_info["error"] = str(exc)

        return violations, summary_info


cypher_validator = CypherValidator()
