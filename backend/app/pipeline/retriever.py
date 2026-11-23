from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.models.schemas import KBEntry
from app.pipeline.llm_client import llm_client


class KBRetriever:
    """
    Two-stage Knowledge Base Retriever (Appendix F / Slide 9):
    Stage A: Category/type soft-filter + TF-IDF char n-gram (2,4) cosine similarity (no external embedding model).
    Stage B: Approved foundation model (DeepSeek/GLM/Kimi) semantic rerank to Top-3 by intent/direction/pattern.
    Explicitly marks retrieval_mode as 'tfidf+llm_rerank' or 'tfidf_only (degraded)'.
    """

    def retrieve(
        self,
        question: str,
        entries: List[KBEntry],
        category_hint: Optional[str] = None,
        intent_hint: str = "",
        top_k: int = 3,
        use_llm_rerank: bool = True,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], str]:
        approved = [e for e in entries if e.approved]
        if not approved:
            return [], "empty_kb"

        docs = [
            f"{e.question} {e.category} {e.intent} {e.relation_pattern} {e.notes}"
            for e in approved
        ]
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        matrix = vec.fit_transform(docs + [question])
        sims = cosine_similarity(matrix[-1], matrix[:-1]).flatten()

        scored: List[Tuple[float, KBEntry]] = []
        for score, entry in zip(sims, approved):
            boost = 0.08 if (category_hint and entry.category == category_hint) else 0.0
            scored.append((float(score) + boost, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        shortlist_n = min(8, len(scored))
        shortlist = [
            {
                "id": e.id,
                "question": e.question,
                "cypher": e.cypher,
                "params": e.params,
                "category": e.category,
                "level": e.level.value,
                "intent": e.intent,
                "direction": e.direction,
                "relation_pattern": e.relation_pattern,
                "tfidf_score": round(sc, 4),
                "rerank_mode": "tfidf",
            }
            for sc, e in scored[:shortlist_n]
        ]

        if use_llm_rerank and llm_client.has_live_credentials(provider, model, api_key, base_url):
            reranked, used_llm = llm_client.rerank_kb_examples(
                question=question,
                intent=intent_hint or (category_hint or ""),
                candidates=shortlist,
                top_k=top_k,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
            if used_llm:
                return reranked, "tfidf+llm_rerank"

        return shortlist[:top_k], "tfidf_only"


kb_retriever = KBRetriever()
