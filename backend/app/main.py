from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.core.config import PROJECT_ROOT
from app.pipeline.schema_fetcher import neo4j_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    neo4j_manager.close()


app = FastAPI(
    title="CodeGraph Text2Cypher Agent",
    description="面向代码图谱的 Text2Cypher 查询智能体（六阶段流水线 + 五层只读安全防线 + T3 复杂分解 + KB30）",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# Serve built Vue 3 frontend if frontend/dist exists
dist_dir: Path = PROJECT_ROOT / "frontend" / "dist"
if dist_dir.exists():
    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="frontend")
