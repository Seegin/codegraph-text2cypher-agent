---
name: simple-entity-query
version: "1.0.0"
description: "单节点检索、属性过滤、单跳归属与基础聚合计数技能（适用于简单查询与入口 API 查询）"
applies_to: ["simple", "single_node", "attribute_filter", "entry_api"]
schema_version: "master-to-dd92845"
---

# 简单实体与属性过滤查询技能 (v1.0.0)

## 适用模式
1. **单节点查询**：列出微服务、查找特定类/方法/队列/配置。
2. **属性过滤**：按 `类类型 = 'CONTROLLER'`、`是否接口 = true`、`HTTP方法 = 'POST'`、`是否入口方法 = true` 等精确过滤。
3. **单跳归属与关系**：
   - 方法属于哪个类：`(m:`方法`)-[:`归属于`]->(c:`类`)`
   - 入口 API 归属方法：`(u:`上游API`)-[:`归属于`]->(m:`方法`)`
   - 接口实现：`(impl:`类`)-[:`接口实现`]->(iface:`类`)`
   - 类继承：`(sub:`类`)-[:`继承`]->(sup:`类`)`
   - 配置/队列归属微服务：`(x)-[:`归属于`]->(s:`微服务`)`
4. **基础计数与分组统计**：
   - 统计务必使用 `count(DISTINCT n.nodeId)`，防止路径展开导致重复计数。
   - 统计每个微服务下的类或方法数量时，使用 `UNWIND n.serviceContexts AS svc` 分组计数，避免重复。
