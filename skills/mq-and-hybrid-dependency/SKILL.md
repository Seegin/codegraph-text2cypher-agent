---
name: mq-and-hybrid-dependency
version: "1.0.0"
description: "MQ 三阶段消息链路（发布/路由/消费）与 RPC+MQ 跨服务异构依赖分解联合技能"
applies_to: ["medium", "complex", "mq", "rpc_mq_hybrid"]
schema_version: "master-to-dd92845"
---

# MQ 消息链路与 RPC+MQ 联合分析技能 (v1.0.0)

## MQ 三阶段精确契约
实库中所有 MQ 关系统一为 `[:消息流]`，必须区分 `消息流类型` 并用 **`路由键` 关联**：
1. **发布阶段**：`(pub:`方法`)-[r1:`消息流` {`消息流类型`: '发布至'}]->(ex:`消息交换机`)`
2. **路由阶段**：`(ex:`消息交换机`)-[r2:`消息流` {`消息流类型`: '路由至'}]->(q:`消息队列`)`
   - **强制约束**：`WHERE r1.`路由键` = r2.`路由键``（因为实库共享 `(default)` 交换机，缺省此条件会错误交叉连接 `email` 与 `food_delivery` 队列！）
3. **消费阶段**：`(q:`消息队列`)-[r3:`消息流` {`消息流类型`: '消费自'}]->(sub:`方法`)`

## 完整 MQ 通信链路查询模式
```cypher
MATCH (pub:`方法`)-[r1:`消息流` {`消息流类型`: '发布至'}]->(ex:`消息交换机`)
      -[r2:`消息流` {`消息流类型`: '路由至'}]->(q:`消息队列`)
      -[r3:`消息流` {`消息流类型`: '消费自'}]->(sub:`方法`)
WHERE r1.`路由键` = r2.`路由键`
RETURN pub, r1, ex, r2, q, r3, sub,
       pub.serviceContexts AS producer_services,
       sub.serviceContexts AS consumer_services,
       q.`队列名称` AS queue_name,
       r1.`路由键` AS routing_key
```

## RPC + MQ 异构依赖分解策略
当问题涉及“某服务有哪些跨服务依赖（含同步调用与异步消息）”或端到端混合依赖时，采用**子查询分解 + 关联去重合并**：
- **子查询 1（RPC 同步依赖）**：查询该服务发出的 `(m:`方法`)-[:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[:`目标服务`]->(dst:`微服务`)` 以及被调用的入站 RPC。
- **子查询 2（MQ 异步依赖）**：查询该服务作为发布方或消费方参与的三阶段 `[:消息流]` 完整链路（确保 `r1.`路由键` = r2.`路由键``）。
- **结果合并**：按 `(target_service_nodeId, mechanism)` 去重整合，显式标注每条依赖的通信机制 `RPC` 或 `MQ`。
