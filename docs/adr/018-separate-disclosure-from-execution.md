# ADR 018 — 披露应用与执行应用使用独立数据和 Index

**Status:** accepted, not implemented

**Date:** 2026-08-29

## Context

Contexture 目前只有一种编译/服务路径：业务声明经 `ControllerManager → Index` 形成带 Tool Binding 的
运行图，`SystemAPI` 同时承载 `discover/open/open_for_a_person` 与
`invoke_read_only/invoke/read_for_a_host`，`Surface.of` 再把 Tool、Prompt、Resource 三扇 Door 全部安装到
一个 Server。

One Creator 需要另一种同样渐进、允许 Agent 阅读并参与架构决策、但不能执行业务 Tool 的
Architecture/Brain MCP。它不是 Runtime Index 的第二个视图：运行面回答“当前可调用什么”，运营面回答
“系统由什么构成、责任和实现如何关联”。二者用途、更新节奏、数据来源和安全属性不同，共享一个 Index
会把运行 binding、Channels 和错误的目录拓扑带进 Architecture，也会让隔离退化成对同一对象的过滤。

只从第二个 MCP 的 `tools/list` 删除 invoke 也不足以形成闭合边界：Resource 会通过
`read_for_a_host` 执行只读 Tool，Prompt 和 server instructions 仍可能读到错误的数据集，`Tool.card` 也总是
携带调用 schema。

## Decision

Runtime 与 Architecture 使用两个独立的 `Contexture` Application declaration、两份独立数据、两个独立
Index、两个独立 Server 和两个独立 MCP。它们不共享节点实例、Index、Binding、Channels、Telemetry 或
MCP session。

Contexture 新增不绑定 Tool 的 disclosure-only 编译路径。Runtime Index 保持当前带 Binding 的编译；
Architecture Index 只保存 Role/Skill/Tool 的结构事实和引用，不生成 schema/binding，也不能构造
`ApplicationRuntime`。

同时将现有 `SystemAPI` 的两个职责拆为两个可独立持有的对象：

```text
DisclosureAPI                         ExecutionAPI
  discover                              invoke_read_only
  open                                  invoke
  open_for_person                       read_for_host
```

Runtime MCP 同时组合两者；Architecture MCP 只组合 `DisclosureAPI`。Prompt 可以存在于 Architecture MCP，
但必须读取 Architecture Index；Resource 因其语义是执行一个无参数只读 Tool，只存在于 Runtime MCP。

Architecture 仍使用既有 Role、Skill、Tool 和 ROUTE/ACTIVE 渐进披露，不增加 Element、ArchitectureNode、
surface flag 或第二套 ContextNode compile 生命周期。Architecture 中的 Tool 是独立数据集里的结构事实，
其 card 不包含 `input_schema`，也没有 Binding 或 invoke 门。

两个数据集的一致性不靠共享内存对象，而靠应用仓库的显式 source contract、确定性编译和差异测试：
Runtime 数据是可执行能力声明；Architecture 数据是由 owner/module/provider/deployment 权威事实编译出的
可重建 read model，不是第二业务真相源，也不得通过源码目录扫描生成。

## Consequences

- Runtime 的四网关 wire、payload、Prompt、Resource 和执行语义保持兼容。
- Architecture 可以有不同于 Runtime 的结构、根节点和披露顺序，不需要 root subset 或共享 Index policy。
- Architecture 进程从构造开始就没有运行 Binding 和 Channels；隔离不依赖“恰好没注册 invoke”。
- 两个 MCP 可使用同一 Contexture 包和同一 OC 镜像，但必须是独立 Server/endpoint/auth；生产上推荐独立
  进程，使故障域和凭证边界与数据边界一致。
- `SystemAPI` 在兼容窗口内保留为 Runtime 组合 facade，现有调用者可分批迁移。
- Contexture 不定义 OC 的 Architecture 内容、Brain 方法、provider vocabulary 或事实同步方式；它提供
  disclosure-only Application 的通用编译和服务机制。
- 完整设计、迁移和验证矩阵见 `docs/07-independent-disclosure-application-plan.md`。

