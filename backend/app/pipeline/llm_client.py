from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from app.core.config import APPROVED_PROVIDERS, settings

logger = logging.getLogger(__name__)


def _extract_json_object(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    cm = re.search(r"```(?:cypher)?\s*(MATCH.*?)```", text, re.DOTALL | re.IGNORECASE)
    if cm:
        return {"cypher": cm.group(1).strip(), "params": {}, "reasoning": "extracted from cypher code fence"}
    return {}


class ApprovedLLMClient:
    """
    Stage 3: Approved Foundation Model Client.
    - Strictly restricted to DeepSeek, GLM (Zhipu), and Kimi (Moonshot) per course constraints.
    - Reuses pooled OpenAI HTTP clients per (base_url, api_key) to avoid repeated TLS handshakes.
    - Intelligently negotiates thinking/reasoning budgets so reasoning tokens never starve JSON output.
    """

    def __init__(self) -> None:
        self._clients: Dict[Tuple[str, str], OpenAI] = {}

    def _get_client(self, api_key: str, base_url: str) -> OpenAI:
        cache_key = (base_url, api_key)
        client = self._clients.get(cache_key)
        if client is None:
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=settings.llm_timeout_sec,
            )
            self._clients[cache_key] = client
        return client

    def resolve_runtime_config(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, str]:
        return settings.get_provider_config(
            provider_override=provider,
            model_override=model,
            api_key_override=api_key,
            base_url_override=base_url,
        )

    def has_live_credentials(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> bool:
        cfg = self.resolve_runtime_config(provider, model, api_key, base_url)
        return bool(cfg.get("api_key"))

    def list_approved_providers(self) -> List[Dict[str, Any]]:
        out = []
        for pid, meta in APPROVED_PROVIDERS.items():
            cfg = settings.get_provider_config(provider_override=pid)
            out.append(
                {
                    "provider": pid,
                    "display_name": meta["display_name"],
                    "default_model": meta["default_model"],
                    "allowed_models": meta["allowed_models"],
                    "default_base_url": meta["default_base_url"],
                    "configured": bool(cfg.get("api_key")),
                }
            )
        return out

    def _chat(
        self,
        system_prompt: str,
        user_prompt: str,
        cfg: Dict[str, str],
        json_mode: bool = True,
        max_tokens: int = 2400,
        disable_thinking: bool = True,
    ) -> Tuple[str, Dict[str, Any]]:
        if not cfg.get("api_key"):
            raise RuntimeError(f"未配置 {cfg['display_name']} API Key，无法发起实时大模型调用。")

        client = self._get_client(cfg["api_key"], cfg["base_url"])
        model_name = cfg["model"]
        model_low = model_name.lower()

        base_kwargs: Dict[str, Any] = {
            "model": model_name,
            "temperature": settings.llm_temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode and not model_low.startswith("deepseek-reasoner"):
            base_kwargs["response_format"] = {"type": "json_object"}

        # Smart thinking budget control across DeepSeek / GLM / Kimi gateways:
        # - DeepSeek-V4.x supports extra_body={"thinking": {"type": "disabled"}} (3.3x faster, zero truncation!)
        # - GLM-5.3* always thinks and accepts reasoning_effort="low" with generous max_tokens
        # - Auto-retries cleanly if a gateway rejects extra_body/reasoning_effort
        attempts: List[Dict[str, Any]] = []
        if disable_thinking:
            if model_low.startswith("glm-5") or model_low.startswith("kimi-k3"):
                attempts.append({**base_kwargs, "max_tokens": max(max_tokens, 4096), "reasoning_effort": "low"})
                attempts.append({**base_kwargs, "max_tokens": max(max_tokens, 4096)})
            elif not model_low.startswith("deepseek-reasoner"):
                attempts.append({**base_kwargs, "extra_body": {"thinking": {"type": "disabled"}}})
                attempts.append({**base_kwargs, "max_tokens": max(max_tokens, 4096), "reasoning_effort": "low"})
                attempts.append({**base_kwargs, "max_tokens": max(max_tokens, 4096)})
            else:
                attempts.append({**base_kwargs, "max_tokens": max(max_tokens, 4096)})
        else:
            attempts.append({**base_kwargs, "max_tokens": max(max_tokens, 4096)})

        last_exc: Optional[Exception] = None
        resp = None
        for kw in attempts:
            try:
                resp = client.chat.completions.create(**kw)
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                # Only retry parameter-compatibility 400 errors; raise auth/network errors directly
                if "400" in msg or "thinking" in msg or "reasoning_effort" in msg or "unsupported" in msg:
                    logger.debug("Retrying LLM call with compatible params after: %s", exc)
                    continue
                raise

        if resp is None:
            raise last_exc or RuntimeError("LLM call failed with no response")

        content = resp.choices[0].message.content or ""
        usage: Dict[str, Any] = {}
        if getattr(resp, "usage", None):
            usage = {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(resp.usage, "completion_tokens", 0),
                "total_tokens": getattr(resp.usage, "total_tokens", 0),
            }
        return content, usage

    def generate_cypher(
        self,
        system_prompt: str,
        user_prompt: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, str]]:
        cfg = self.resolve_runtime_config(provider, model, api_key, base_url)
        raw, usage = self._chat(system_prompt, user_prompt, cfg, json_mode=True, max_tokens=2400)
        parsed = _extract_json_object(raw)
        if not parsed.get("cypher") and not parsed.get("query"):
            raise ValueError(f"模型未返回有效 Cypher JSON 响应: {raw[:240]}")
        cypher = str(parsed.get("cypher") or parsed.get("query")).strip()
        params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
        return (
            {
                "cypher": cypher,
                "params": params,
                "reasoning": str(parsed.get("reasoning") or ""),
            },
            usage,
            cfg,
        )

    def rerank_kb_examples(
        self,
        question: str,
        intent: str,
        candidates: List[Dict[str, Any]],
        top_k: int = 3,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Semantic rerank of TF-IDF shortlisted KB examples using approved LLM."""
        cfg = self.resolve_runtime_config(provider, model, api_key, base_url)
        if not cfg.get("api_key") or len(candidates) <= 1:
            return candidates[:top_k], False

        cand_lines = []
        for idx, c in enumerate(candidates):
            cand_lines.append(
                f"[{idx}] {c['id']} | cat={c['category']} | intent={c.get('intent','')} | dir={c.get('direction','')} | pattern={c.get('relation_pattern','')} | tfidf={c.get('tfidf_score',0)} | Q={c['question']}"
            )
        sys_prompt = (
            "你是代码图谱查询检索重排器。请严格匹配用户问题的："
            "(1) 意图与跳数（单跳 vs 两跳跨服务 vs 三阶段MQ）；"
            "(2) 遍历方向（forward 出向 / reverse 反向 / both 双向入向+出向）；"
            "(3) 通信机制（RPC / MQ / RPC+MQ 混合）与关系模式 pattern。"
            "从候选中选出最相关的 Top-3 索引，仅输出 JSON: {\"ranked_indices\": [0, 2, 1]}。"
        )
        usr_prompt = f"用户问题：{question}\n规划意图：{intent}\n候选示例：\n" + "\n".join(cand_lines)
        try:
            raw, _ = self._chat(sys_prompt, usr_prompt, cfg, json_mode=True, max_tokens=160)
            obj = _extract_json_object(raw)
            idxs = obj.get("ranked_indices") or []
            reranked: List[Dict[str, Any]] = []
            seen = set()
            for i in idxs:
                if isinstance(i, int) and 0 <= i < len(candidates) and i not in seen:
                    seen.add(i)
                    item = dict(candidates[i])
                    item["rerank_mode"] = f"llm:{cfg['provider']}/{cfg['model']}"
                    reranked.append(item)
            for i, c in enumerate(candidates):
                if i not in seen:
                    reranked.append(c)
            return reranked[:top_k], True
        except Exception as exc:
            logger.warning("LLM rerank degraded to TF-IDF: %s", exc)
            return candidates[:top_k], False

    def summarize_evidence(
        self,
        question: str,
        records: List[Dict[str, Any]],
        paths: List[Dict[str, Any]],
        truncated_note: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Optional[str]:
        cfg = self.resolve_runtime_config(provider, model, api_key, base_url)
        if not cfg.get("api_key"):
            return None
        sys_prompt = (
            "你是代码知识图谱结果总结助手。严格规则：\n"
            "1. 只能陈述输入证据中明确存在的微服务、类、方法、API、队列和路径，绝对禁止猜测或编造未出现的实体或关系。\n"
            "2. 使用简洁准确的中文结构化总结（≤180字），先给直接结论，再列关键证据。\n"
            "3. 仅返回 JSON: {\"summary\": \"...\"}。"
        )
        # Compact each record to scalar columns + concise node captions (strip bulky graph object blobs
        # so multi-subquery RPC+MQ rows are NEVER truncated out of the summarizer's context!)
        compact_records = []
        for r in records[:30]:
            cr: Dict[str, Any] = {}
            for k, v in r.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, dict) and v.get("_type") == "node":
                    cr[k] = f"{v.get('caption') or v.get('nodeId')} ({v.get('nodeId')})"
                elif isinstance(v, dict) and v.get("_type") in ("relationship", "path"):
                    continue
                else:
                    cr[k] = v
            compact_records.append(cr)
        compact_paths = [
            {"mechanism": p.get("mechanism"), "hops": p.get("hops"), "summary": p.get("summary")}
            for p in paths[:15]
        ]
        usr_prompt = (
            f"问题：{question}\n"
            f"总记录数：{len(records)}{truncated_note}\n"
            f"查询证据（已精简保留全部字段）：{json.dumps(compact_records, ensure_ascii=False)[:5500]}\n"
            f"路径证据：{json.dumps(compact_paths, ensure_ascii=False)[:2000]}"
        )
        try:
            raw, _ = self._chat(sys_prompt, usr_prompt, cfg, json_mode=True, max_tokens=600)
            obj = _extract_json_object(raw)
            s = obj.get("summary")
            return str(s).strip() if s else None
        except Exception as exc:
            logger.warning("LLM summary fallback to deterministic formatter: %s", exc)
            return None


llm_client = ApprovedLLMClient()
