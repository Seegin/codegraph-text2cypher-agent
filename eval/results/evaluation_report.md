# 独立黄金 30 题评测报告

- **评测配置**: `full_system_live_llm` | **运行模式**: `llm:deepseek/DeepSeek-V4.1-Flash`
- **图谱版本**: `master-to-dd92845` (`schema_hash=22b9370aac98dd67`)
- **评测时间**: `2026-11-23T08:45:56.231274Z`
- **总成绩**: **30 / 30 (100.0%)** → 评级 **A 档**
- **分层成绩**: 简单 S1–S10 `10/10` | 中等 M1–M12 `12/12` | 困难 H1–H8 `8/8`
- **端到端时延**: P50 = `6393.97 ms` | P95 = `14320.03 ms`（预算目标 ≤ 20,000 ms）| 触发受限修复 `0` 次

| 题号 | 难度 | 类别 | 结果 | 记录数 | 修复 | 耗时 (ms) | 评测依据 |
|---|---|---|---|---:|---:|---:|---|
| S01 | simple | 单节点计数 | ✅ PASS | 1 | 0 | 3902.72 | expected=41, got=41 |
| S02 | simple | 单节点查询 | ✅ PASS | 65 | 0 | 5533.1 | exact set match (65 items) |
| S03 | simple | 属性过滤 | ✅ PASS | 1 | 0 | 5856.54 | exact set match (1 items) |
| S04 | simple | 单节点查询 | ✅ PASS | 1 | 0 | 4881.48 | exact set match via col 'nodeId' (1 items) |
| S05 | simple | 属性过滤 | ✅ PASS | 26 | 0 | 5197.46 | exact set match (26 items) |
| S06 | simple | 入口API发现 | ✅ PASS | 9 | 0 | 5323.07 | exact set match (9 items) |
| S07 | simple | 单跳归属 | ✅ PASS | 9 | 0 | 5768.72 | exact set match (9 items) |
| S08 | simple | 单跳关系 | ✅ PASS | 1 | 0 | 5905.8 | exact set match (1 items) |
| S09 | simple | 单跳关系 | ✅ PASS | 2 | 0 | 5625.52 | exact set match (2 items) |
| S10 | simple | 基础计数 | ✅ PASS | 1 | 0 | 4674.55 | expected=33, got=33 |
| M01 | medium | 下游服务发现 | ✅ PASS | 14 | 0 | 6945.85 | exact set match (8 items) |
| M02 | medium | 下游服务发现 | ✅ PASS | 5 | 0 | 7065.31 | exact set match (5 items) |
| M03 | medium | 下游服务发现 | ✅ PASS | 3 | 0 | 6809.44 | exact set match (3 items) |
| M04 | medium | 下游服务发现 | ✅ PASS | 5 | 0 | 7365.26 | exact set match (3 items) |
| M05 | medium | 下游服务发现 | ✅ PASS | 3 | 0 | 7217.71 | exact set match (3 items) |
| M06 | medium | 下游服务发现 | ✅ PASS | 4 | 0 | 6958.66 | exact set match (2 items) |
| M07 | medium | 反向依赖 | ✅ PASS | 18 | 0 | 6145.55 | exact set match (8 items) |
| M08 | medium | 反向依赖 | ✅ PASS | 13 | 0 | 6043.56 | exact set match (8 items) |
| M09 | medium | 反向依赖 | ✅ PASS | 7 | 0 | 6260.2 | exact set match via col 'caller_service_name' (4 items) |
| M10 | medium | 聚合统计 | ✅ PASS | 8 | 0 | 6393.97 | map match (8 entries) |
| M11 | medium | 聚合统计 | ✅ PASS | 10 | 0 | 5038.46 | map match (10 entries) |
| M12 | medium | 组合属性过滤 | ✅ PASS | 4 | 0 | 5277.42 | exact set match (4 items) |
| H01 | complex | MQ三阶段链路 | ✅ PASS | 3 | 0 | 6764.31 | exact set match (3 items) |
| H02 | complex | 跨服务MQ通信 | ✅ PASS | 1 | 0 | 14320.03 | exact set match (1 items) |
| H03 | complex | 多跳跨服务RPC | ✅ PASS | 11 | 0 | 8315.59 | exact set match (7 items) |
| H04 | complex | 多跳跨服务RPC | ✅ PASS | 11 | 0 | 13933.66 | exact set match (9 items) |
| H05 | complex | 跨服务变更影响分析 | ✅ PASS | 5 | 0 | 11158.88 | exact set match (5 items) |
| H06 | complex | 跨服务变更影响分析 | ✅ PASS | 3 | 0 | 10315.18 | exact set match (3 items) |
| H07 | complex | 服务级反向影响穿透 | ✅ PASS | 25 | 0 | 12270.15 | exact set match via col 'upstream_api_id' (15 items) |
| H08 | complex | RPC+MQ双向联合依赖 | ✅ PASS | 6 | 0 | 16586.92 | exact pair set match (6 pairs) |
