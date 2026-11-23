from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Strictly enforced course constraint: ONLY DeepSeek, GLM, or Kimi are permitted.
APPROVED_PROVIDERS: Dict[str, Dict[str, object]] = {
    "deepseek": {
        "display_name": "DeepSeek",
        "default_model": "DeepSeek-V4.1-Flash",
        "allowed_models": [
            "DeepSeek-V4.1-Flash",
            "DeepSeek-V4.1-Flash-1017",
            "deepseek-chat",
            "deepseek-reasoner",
            "deepseek-v3",
        ],
        "default_base_url": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "glm": {
        "display_name": "GLM (智谱清言)",
        "default_model": "GLM-5.3",
        "allowed_models": [
            "GLM-5.3",
            "GLM-5.3-Flash",
            "GLM-5.3-FlashX",
            "glm-4-plus",
            "glm-4-air",
            "glm-4-flash",
            "glm-4.5",
            "glm-4.5-air",
            "glm-4.5-flash",
        ],
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "env_key": "GLM_API_KEY",
    },
    "kimi": {
        "display_name": "Kimi (Moonshot)",
        "default_model": "Kimi-K3.1-Pro-HighSpeed",
        "allowed_models": [
            "Kimi-K3.1-Pro-HighSpeed",
            "Kimi-K3.1-Pro",
            "kimi-k2-0711-preview",
            "moonshot-v1-8k",
            "moonshot-v1-32k",
            "moonshot-v1-128k",
        ],
        "default_base_url": "https://api.moonshot.cn/v1",
        "env_key": "KIMI_API_KEY",
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Neo4j
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="")
    neo4j_database: str = Field(default="neo4j")
    neo4j_query_timeout_sec: float = Field(default=5.0)

    # LLM (Strictly limited to deepseek / glm / kimi)
    llm_provider: str = Field(default="deepseek")
    llm_model: str = Field(default="DeepSeek-V4.1-Flash")
    llm_temperature: float = Field(default=0.0)
    llm_timeout_sec: float = Field(default=30.0)

    deepseek_api_key: str = Field(default="")
    deepseek_base_url: str = Field(default="https://api.deepseek.com/v1")
    glm_api_key: str = Field(default="")
    glm_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4")
    kimi_api_key: str = Field(default="")
    kimi_base_url: str = Field(default="https://api.moonshot.cn/v1")

    # Resource & Safety Budgets (Appendix C)
    default_max_hops: int = Field(default=6)
    hard_max_hops: int = Field(default=10)
    max_repair_rounds: int = Field(default=2)
    single_query_budget_sec: float = Field(default=30.0)
    batch_max_size: int = Field(default=50)
    batch_concurrency: int = Field(default=2)
    display_max_nodes: int = Field(default=100)
    display_max_edges: int = Field(default=500)
    max_result_rows: int = Field(default=2000)

    # Paths
    sqlite_db_path: str = Field(default="data/app.db")
    schema_snapshot_path: str = Field(default="data/schema_snapshot.json")
    kb_seed_path: str = Field(default="data/kb30.json")
    golden_set_path: str = Field(default="data/golden30.json")
    skills_dir: str = Field(default="skills")

    @field_validator("llm_provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        low = v.strip().lower()
        if low not in APPROVED_PROVIDERS:
            raise ValueError(
                f"模型提供商 '{v}' 不符合课程限制！仅允许使用: {list(APPROVED_PROVIDERS.keys())}"
            )
        return low

    def resolve_path(self, rel_or_abs: str) -> Path:
        p = Path(rel_or_abs)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    def get_provider_config(
        self,
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
    ) -> Dict[str, str]:
        provider = (provider_override or self.llm_provider).strip().lower()
        if provider not in APPROVED_PROVIDERS:
            raise ValueError(
                f"禁止使用未批准的模型提供商 '{provider}'，仅允许 deepseek / glm / kimi。"
            )
        meta = APPROVED_PROVIDERS[provider]
        model = (model_override or "").strip() or (
            self.llm_model if provider == self.llm_provider else str(meta["default_model"])
        )
        allowed: List[str] = list(meta["allowed_models"])  # type: ignore[arg-type]
        if model not in allowed:
            # Permit versioned model variants under the approved family prefix only
            prefixes = {
                "deepseek": ("deepseek-",),
                "glm": ("glm-",),
                "kimi": ("kimi-", "moonshot-"),
            }[provider]
            if not any(model.lower().startswith(pf) for pf in prefixes):
                raise ValueError(
                    f"模型 '{model}' 不属于已批准的 {provider} 家族（允许前缀 {prefixes}）。"
                )

        if provider == "deepseek":
            key = api_key_override or self.deepseek_api_key
            url = base_url_override or self.deepseek_base_url
        elif provider == "glm":
            key = api_key_override or self.glm_api_key
            url = base_url_override or self.glm_base_url
        else:
            key = api_key_override or self.kimi_api_key
            url = base_url_override or self.kimi_base_url

        return {
            "provider": provider,
            "display_name": str(meta["display_name"]),
            "model": model,
            "api_key": key.strip(),
            "base_url": url.strip(),
        }


settings = Settings()
