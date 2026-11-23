---
name: code-graph-schema
version: "1.0.0"
description: "Train Ticket 代码知识图谱核心中文 Schema 契约、实体映射规则与只读安全约束（必选基础技能）"
applies_to: ["simple", "medium", "complex", "all"]
schema_version: "master-to-dd92845"
---

# 代码图谱核心 Schema 技能 (v1.0.0)

## 强制标签与关系白名单
- **唯一允许的 8 种节点标签**：`微服务`、`类`、`方法`、`上游API`、`下游API`、`配置`、`消息队列`、`消息交换机`。
- **唯一允许的 8 种关系类型**：`调用`、`归属于`、`导入`、`链中下一节点`、`目标服务`、`接口实现`、`消息流`、`继承`。
- **绝对禁止**使用不存在的标签或英文关系名（如 `API端点`、`配置文件节点`、`测试用例`、`CALLS`、`BELONGS_TO`）。

## 口语到实库 Schema 语义映射
- “服务 / 微服务 / 模块” → 节点 `微服务` (`服务名称`)，或节点属性 `'ts-xxx' IN n.serviceContexts` / `n.moduleId`
- “类 / 控制器 / Service / 接口类” → 节点 `类` (`简名`, `全限定名`, `类类型` ∈ `['CONTROLLER','SERVICE_INTERFACE','SERVICE_IMPL','OTHER']`, `是否接口`)
- “方法 / 函数 / 处理方法” → 节点 `方法` (`方法名`, `全限定名`, `所属类名`, `方法签名`, `是否入口方法`)
- “接口 / 入口 API / REST 端点 / URL” → 暴露端用 `上游API` (`HTTP方法`, `API路径`, `caption`, `控制器方法`)；对外远程调用用 `下游API` (`HTTP方法`, `API路径`, `完整URL`, `目标服务名`)
- “配置文件 / 配置项 / Bean” → 节点 `配置` (`configurationKind`, `key`, `logicalResourcePath`, `beanName`)
- “MQ / 消息队列 / 交换机 / RabbitMQ” → 节点 `消息队列` (`队列名称`)、`消息交换机` (`交换机名称`) 与关系 `[:消息流]`

## 关键结构规则
1. `类` 与 `方法` **没有**指向 `微服务` 的 `归属于` 关系，归属微服务必须通过 `$service IN n.serviceContexts` 或 `n.moduleId = $service` 判断。
2. 所有实体字面量尽量通过参数化（`$param`）或已绑定的 `nodeId` 注入，避免字符串拼接注入。
3. 严禁任何写入或管理操作（CREATE/MERGE/DELETE/SET/REMOVE/DROP/CALL dbms/apoc），仅输出单条只读 Cypher。
4. 为保障前端拓扑可视化与评测对齐，尽量同时返回节点/路径变量或稳定 `nodeId` 与展示名称。
