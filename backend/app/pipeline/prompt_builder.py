from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from app.models.schemas import QueryPlan, SchemaSnapshot
from app.pipeline.schema_fetcher import schema_fetcher
from app.pipeline.skill_loader import SkillUnit, skill_loader


class PromptBuilder:
    """Stage 2: Skill-composed Prompt Builder injecting live Schema, Skills, bound entities, and Top-3 KB examples."""

    def build_generation_prompt(
        self,
        question: str,
        schema: SchemaSnapshot,
        plan: QueryPlan,
        kb_examples: List[Dict[str, Any]],
        subquery_instruction: Optional[str] = None,
    ) -> Tuple[str, str, Dict[str, str]]:
        skills: List[SkillUnit] = skill_loader.select_skills(plan.level.value, plan.category)
        skill_versions = {s.name: s.version for s in skills}
        schema_text = schema_fetcher.format_for_prompt(schema)
        skills_text = skill_loader.compose_skill_prompt(skills)

        system_prompt = (
            "你是面向微服务代码知识图谱（Neo4j / Train Ticket）的 Text2Cypher 专家智能体。\n"
            "你的任务是根据冻结实库 Schema、加载的领域技能（Skill）、已绑定的真实实体 ID 及检索到的高质量示例，"
            "生成**一条**语法合法、严格只读、语义精准的 Cypher 查询语句。\n\n"
            f"{schema_text}\n\n"
            f"{skills_text}\n\n"
            "### 输出格式（必须严格返回 JSON 对象，不要输出额外文本）\n"
            "{\n"
            '  "cypher": "单条合法只读 Cypher 语句",\n'
            '  "params": {"参数名": "参数值"},\n'
            '  "reasoning": "简述选用的节点标签、关系方向、跳数界限与去重口径"\n'
            "}"
        )

        # Format bound entities
        entity_lines = []
        for ent in plan.entities:
            if ent.status == "bound" and ent.bound:
                b = ent.bound
                entity_lines.append(
                    f"- 提及 `{ent.mention}` → 已绑定 `{b.label}`: nodeId=`{b.node_id}`, name=`{b.name}`, "
                    f"class=`{b.class_name or ''}`, services={b.service_contexts}, file=`{b.source_file or ''}`"
                )
        entity_block = "\n".join(entity_lines) if entity_lines else "（无预绑定实体，请按精确属性匹配）"

        # Format retrieved Top-3 KB examples
        kb_lines = []
        for idx, ex in enumerate(kb_examples, 1):
            kb_lines.append(
                f"示例 {idx} [{ex.get('category')}/{ex.get('direction')}]：\n"
                f"  问题：{ex.get('question')}\n"
                f"  Cypher：{ex.get('cypher')}\n"
                f"  参数：{json.dumps(ex.get('params') or {}, ensure_ascii=False)}"
            )
        kb_block = "\n\n".join(kb_lines) if kb_lines else "（当前未启用知识库示例增强）"

        user_parts = [
            f"### 用户自然语言问题\n{question}",
            f"### 查询计划提示\n- 意图：{plan.intent}\n- 类别：{plan.category}\n- 方向：{plan.direction}\n- 最大跳数：{plan.max_hops}\n- 期望结果形态：{plan.expected_shape}",
            f"### 已消歧绑定的真实图谱实体（优先使用 nodeId 参数化匹配）\n{entity_block}",
            f"### 相似知识库参考示例（Top-{len(kb_examples)}）\n{kb_block}",
        ]
        if subquery_instruction:
            user_parts.append(f"### 当前子查询任务指令\n{subquery_instruction}")
        user_parts.append(
            "请尽量在 RETURN 中同时返回节点/路径对象以及关键字段（如 nodeId、名称）以支持前端拓扑图可视化与证据追溯。"
        )

        return system_prompt, "\n\n".join(user_parts), skill_versions

    def build_repair_prompt(
        self,
        question: str,
        failed_cypher: str,
        failed_params: Dict[str, Any],
        error_feedback: str,
        schema: SchemaSnapshot,
        plan: QueryPlan,
    ) -> Tuple[str, str]:
        schema_text = schema_fetcher.format_for_prompt(schema)
        system_prompt = (
            "你是代码图谱 Cypher 诊断与修复专家。上一次生成的 Cypher 未通过静态校验、EXPLAIN 预检或结果语义检查。\n"
            "请严格对照实库 Schema 修正错误，仅输出单条只读 Cypher 的 JSON 对象：{\"cypher\": \"...\", \"params\": {...}, \"reasoning\": \"...\"}。\n\n"
            f"{schema_text}"
        )
        user_prompt = (
            f"原始问题：{question}\n"
            f"查询方向：{plan.direction}，最大跳数：{plan.max_hops}，意图：{plan.intent}\n"
            f"失败的 Cypher：\n```cypher\n{failed_cypher}\n```\n"
            f"原参数：{json.dumps(failed_params, ensure_ascii=False)}\n"
            f"诊断反馈与违规详情：\n{error_feedback}\n\n"
            "请修正上述问题并返回新的只读 Cypher JSON。"
        )
        return system_prompt, user_prompt


prompt_builder = PromptBuilder()
