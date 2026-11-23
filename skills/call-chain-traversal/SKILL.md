---
name: call-chain-traversal
version: "1.0.0"
description: "有界多跳调用链、下游服务发现与跨服务反向变更影响分析技能（适用于中等与复杂查询）"
applies_to: ["medium", "complex", "downstream", "reverse_impact"]
schema_version: "master-to-dd92845"
---

# 调用链遍历与变更影响分析技能 (v1.0.0)

## 方向与有界路径控制
- **正向（下游服务发现）**：从起点方法出发沿出边遍历 `(start:`方法`)-[r:`调用`*0..6]->(m:`方法`)`，严禁反向或无向遍历。
- **反向（变更影响分析）**：从目标方法/服务沿入边向上追溯 `(caller:`方法`)-[r:`调用`*0..6]->(target:`方法`)`。
- **路径跳数预算**：默认最多 `6` 跳，硬上限 `10` 跳；**绝对禁止无界 `*`**。
- **中间节点纯净性**：因为 `[:调用]` 同时连向 `配置`、`上游API`、`下游API`，纯方法调用路径必须附加：
  `WHERE ALL(rel IN relationships(p) WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p) WHERE x:`方法`)`
- **去重口径**：Cypher 路径模式天然满足局部边不重复（prevent cycle），聚合下游服务或受影响 API 时必须使用 `DISTINCT` 按 `nodeId` 去重，不得按路径条数误计实体数。

## 标准模板
### 下游服务发现
```cypher
MATCH (start:`方法` {nodeId: $node_id})
MATCH p=(start)-[r:`调用`*0..6]->(m:`方法`)
WHERE ALL(rel IN r WHERE rel.`关系语义` IN ['方法调用', '方法实现'])
  AND ALL(x IN nodes(p) WHERE x:`方法`)
MATCH (m)-[rc:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[rt:`目标服务`]->(svc:`微服务`)
RETURN DISTINCT svc.nodeId AS service_id, svc.`服务名称` AS service_name, d.caption AS api, p, d, svc
```

### 跨服务反向变更影响（定位受影响上游入口 API）
1. **本服务受影响入口 API**：
```cypher
MATCH (target:`方法` {nodeId: $node_id})
MATCH p=(entry:`方法`)-[rs:`调用`*0..6]->(target)
WHERE ALL(rel IN rs WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p) WHERE x:`方法`)
MATCH (u:`上游API`)-[ru:`归属于`]->(entry)
RETURN DISTINCT 'IMPACT' AS mechanism, u.nodeId AS api_id, u.caption AS affected_api, u.serviceContexts[0] AS affected_service, u, ru, p
```
2. **跨服务上游入口 API（必须穿透到上游微服务的 `上游API` 节点！）**：
```cypher
MATCH (target:`方法` {nodeId: $node_id})
MATCH p1=(local_entry:`方法`)-[rs1:`调用`*0..4]->(target)
WHERE ALL(rel IN rs1 WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p1) WHERE x:`方法`)
MATCH (u:`上游API`)-[ru:`归属于`]->(local_entry)
MATCH (up_caller:`方法`)-[rc:`调用` {`关系语义`: '通信边界'}]->(d:`下游API`)-[rh:`调用` {`关系语义`: '跨服务HTTP调用'}]->(u)
MATCH p2=(up_entry:`方法`)-[rs2:`调用`*0..5]->(up_caller)
WHERE ALL(rel IN rs2 WHERE rel.`关系语义` IN ['方法调用', '方法实现']) AND ALL(x IN nodes(p2) WHERE x:`方法`)
MATCH (up_api:`上游API`)-[ru2:`归属于`]->(up_entry)
RETURN DISTINCT 'RPC' AS mechanism, up_api.nodeId AS api_id, up_api.caption AS affected_api, up_api.serviceContexts[0] AS affected_service, up_api, ru2, p2, rc, d, rh, u, ru, p1
```
