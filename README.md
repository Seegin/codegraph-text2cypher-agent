# 面向代码图谱的 Text2Cypher 查询智能体（CodeGraph Text2Cypher Agent）

高级软件工程课程大作业完整工程实现。面向基于 **Train Ticket** 微服务系统构建的 **Neo4j** 代码知识图谱，提供自然语言到只读 Cypher 的自动化生成、五层只读安全防线与 EXPLAIN 预检、同名代码实体交互消歧、T3 复杂查询（RPC + MQ 异构依赖与跨服务反向变更影响）分解合并、Cytoscape.js 拓扑图可视化、过程追踪与 Markdown 报告导出、批量并发隔离执行、30 组实库验证知识库（KB30）管理及独立黄金 30 题评测体系。

---

## 核心架构与功能一览

1. **实库 Schema 冻结契约（W1 门禁）**：直连 Neo4j 实库（`2026.06.0` / 图谱版本 `master-to-dd92845` / `schema_hash: 22b9370aac98dd67`）勘察并冻结 8 种真实中文节点标签与 8 种关系类型，详见 [docs/schema_contract.md](docs/schema_contract.md)。
2. **端到端六阶段流水线 + 编排层（T1）**：
   - `Stage 1` **Schema 获取器** (`backend/app/pipeline/schema_fetcher.py`)：实时获取并哈希校验图谱 Schema，支持离线快照显式降级。
   - `Pre-Stage` **输入安全拦截 + 同名实体消歧** (`backend/app/services/entity_disambiguator.py`)：检索实库真实实体候选，展示所属类/微服务/源码路径证据卡并支持点击确认绑定稳定 `nodeId`。
   - `Stage 2` **技能化 Prompt 构建与双阶段 RAG 检索** (`skills/*/SKILL.md`, `retriever.py`, `prompt_builder.py`)：4 个遵循 `SKILL.md` 规范的可版本化领域技能按问题类型动态组合；KB 检索先经类别过滤与字符 n-gram TF-IDF 粗排，再调用已批准大模型按意图/方向/关系模式重排 Top-3。
   - `Stage 3` **获准大模型客户端** (`llm_client.py`)：严格按课程限制仅允许 **DeepSeek**、**GLM**、**Kimi**，配置与运行期均强制拦截未批准厂商；未配置 API Key 时可透明使用 Schema 感知结构化规划引擎（`hybrid_planner_offline`）离线体验全套功能。
   - `Stage 4` **五层只读安全与 EXPLAIN 预检器** (`validator.py`)：单语句与跳数界限检查（默认 6 跳 / 硬上限 10 跳）→ 词法剥离后的只读结构白名单（禁止写入、DDL 及未授权 `CALL dbms/apoc`）→ 实库 Schema 标签/关系/属性合法性校验 → Neo4j `EXPLAIN` 计划算子验证（确认 `query_type == 'r'` 且零写入算子）。
   - `Stage 5` **受控只读执行、Layer 5 语义检查与受限修复** (`executor.py`, `semantic_checker.py`)：强制 `session.execute_read`、5s 事务超时、展示截断（100 节点 / 500 边）与完整评测口径分离；空结果诊断、MQ `路由键` 联结防错、最多 2 轮修复且每轮修复必重过全量校验。
   - `Stage 6` **证据格式化与可追溯报告** (`formatter.py`)：严格忠于证据的自然语言总结、Cytoscape.js 拓扑数据、完整 `QueryTrace`（关联 `trace_id`/`schema_hash`/`skill_versions`/`model_version`/`kb_version`）与一键 Markdown 报告导出。
3. **30 组实库验证知识库 KB30（T2）**：覆盖全部 7 类查询模式（[data/kb30.json](data/kb30.json)），30/30 实库非空执行通过，前端支持增删改查、分类/关键词检索与一键「实库试运行」。
4. **T3 复杂查询分解与独立黄金 30 题评测**：
   - RPC + MQ 异构依赖与跨服务反向变更影响自动分解为多个独立子查询，分别校验执行后按稳定 `nodeId` 与通信机制去重合并。
   - 独立冻结黄金 30 题（[data/golden30.json](data/golden30.json)，S1–S10 / M1–M12 / H1–H8，与 KB30 零重合）。
5. **现代图形化 Web UI**：基于 **Vue 3 + TypeScript + Cytoscape.js**，涵盖单条三档查询、同名实体澄清、交互拓扑图、批量上传（≤50 题 / 并发 2 / 失败隔离 / 取消）、KB30 管理、评测看板与五层安全演练。

---

## 快速启动

### 1. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env，填入 Neo4j 连接信息及（可选）DEEPSEEK_API_KEY / GLM_API_KEY / KIMI_API_KEY
```

### 2. 启动后端（同时托管已构建的前端静态页面）
```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```
打开浏览器访问 `http://127.0.0.1:8000` 即可使用完整图形化平台！

### 3. 前端开发模式（可选）
```bash
cd frontend
npm install
npm run dev
```

### 4. 使用 Docker Compose 一键部署
```bash
docker compose up --build
```

---

## 运行验证与评测

```bash
# 1. 验证全部 30 组 KB30 在实库通过五层校验且结果非空
.venv/bin/python backend/scripts/seed_and_verify_kb30.py

# 2. 运行安全专项、实体消歧、T3 分解与批量隔离 pytest 套件
.venv/bin/pytest -v

# 3. 运行独立黄金 30 题评测（离线结构化规划器）
.venv/bin/python eval/run_eval.py

# 4. 配置批准模型 API Key 后运行实时大模型黄金 30 题评测
.venv/bin/python eval/run_eval.py --provider deepseek --model deepseek-chat --api-key $DEEPSEEK_API_KEY
```
