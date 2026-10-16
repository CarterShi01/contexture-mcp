# 事故 Demo

[English](README.md) · [文档导航](../../docs/README.zh-CN.md) · [项目首页](../../README.zh-CN.md)

这是一个使用确定性 Kubernetes 事故数据的 Contexture MCP 服务。
Claude Code 与 Codex 可以启动同一个服务、访问同一组能力；不依赖生成的
`CLAUDE.md`、`AGENTS.md` 或 `SKILL.md`。无需集群或服务凭据。

## 声明了什么

```text
kubernetes-platform                              role
├── incident-response                            role
│   ├── diagnose-crash-loop-backoff               skill
│   ├── get_pod_status                            tool，只读
│   ├── get_pod_logs                              tool，只读
│   ├── get_pod_events                            tool，只读
│   └── crash_loop_runbook                        tool，只读；也有 Resource URI
└── deployment-ops                               role
    ├── roll-back-a-failed-release                skill
    ├── get_rollout_status                       tool，只读
    ├── roll_back_deployment                     tool，写操作
    └── rollback_policy                          tool，只读；也有 Resource URI
```

两个子 Role 区分诊断与部署工作，让 Agent 可以比较分支，并只打开相关分支的
指导和 Tool Schema。这个小示例展示机制，不证明网关对所有应用都更高效。

## Host 实际看到什么

MCP 顶层提供五个固定入口，而不是为每个 Kubernetes 动作分别列出工具：

```text
contexture_discover
contexture_inspect
contexture_open
contexture_invoke_read_only
contexture_invoke
```

Discover 和 Inspect 返回导航卡片。打开 Role 才获得局部指导和下一层成员卡片，
包括其 Tool 的输入 Schema；打开 Skill 获得过程和引用。诊断不需要打开部署分支的
详细指导。服务器初始化指令中的目录也会说明可用职责，帮助模型找到起点。

## 读写两个入口

`roll_back_deployment` 是 Demo 中的写操作，经 `contexture_invoke` 调用，
Host 可据此安排人工审批。把它发给 `contexture_invoke_read_only` 会被拒绝。
分类与披露不是业务授权，也不保证 Host 一定配置了人工审批。

固定场景中，`prod` 命名空间的 `payments-api-7d9c` 处于 `CrashLoopBackOff`，
重启 14 次；日志显示缺少 `DB_URL`，事件记录退出码 1。数据不来自真实集群。
固定数据免去了集群准备，但 Host 认证、配置和模型行为仍可能各自失败。
预期诊断需要引用工具返回的证据，而不是只从症状猜测原因。

## 运行

[安装 CLI](../../README.zh-CN.md#安装)后，不连接 Host 也能查看能力图：

```bash
contexture check contexture.demo.role:KubernetesPlatform
contexture list contexture.demo.role:KubernetesPlatform
contexture inspect --target contexture.demo.role:KubernetesPlatform kubernetes-platform/incident-response
```

`check` 应报告三个 Role、两个 Skill、七个 Tool。`contexture demo` 启动 MCP stdio
服务并等待客户端，不会打印诊断后立即退出。连接 Host 时让 Host 启动该进程，
不必手动再运行一份。从源码仓库运行时，先 `uv sync`，再给上述命令加 `uv run` 前缀。

## 连接一个 Host

以下命令注册前面安装的全局 CLI，选择一个 Host 即可。Claude Code 使用项目范围；
Codex 命令修改用户配置。结束后只移除 `contexture-demo` 这一项。

```bash
# Claude Code：在随后启动 Claude Code 的项目目录运行
claude mcp add --scope project contexture-demo -- contexture demo
claude mcp list

# 或 Codex
codex mcp add contexture-demo -- contexture demo
codex mcp list
```

确认连接后，可以提出以下请求：

> 使用 contexture-demo MCP 服务诊断 prod 命名空间中 payments-api-7d9c 持续重启
> 的原因。从 Contexture 披露的 Role 和 Skill 上下文开始，使用 MCP 返回的证据，
> 不读取仓库源码。说明根因以及下一步修复建议。

## 预期的调用路径

以下是示意路径，不是本轮新录制的模型运行。Host 可以先 Discover 找到根节点、
Open 进入根节点，或者 Inspect 比较分支卡片而不加载其指令；模型的实际选择可能不同。

```text
contexture_open(kubernetes-platform/incident-response)
    → 指导、Skill、Tool Schema 和 runbook 卡片
contexture_open(…/diagnose-crash-loop-backoff)
    → 完整过程与声明引用
contexture_invoke_read_only(…/get_pod_status)
    → CrashLoopBackOff，restart_count 14
contexture_invoke_read_only(…/get_pod_logs)
    → ConfigurationError: DB_URL is missing
contexture_invoke_read_only(…/get_pod_events)
    → 退出码 1
contexture_invoke_read_only(…/crash_loop_runbook)
    → 与原因表第一项对应
```

[历史 Host 记录](../../docs/verification/hosts.md)只说明所列早期版本的表现。
也可以显式指定 Demo，离线查看披露内容：

```bash
contexture inspect --target contexture.demo.role:KubernetesPlatform --all
contexture inspect --target contexture.demo.role:KubernetesPlatform --all --summary
```

摘要估计生成上下文的大小，不是 LLM 会话 token 用量、延迟、账单或成功率。
检查披露不等于执行 Tool；完成诊断仍需要实际的只读调用。CLI 的 `contexture inspect`
会重放披露，包括打开指定 ref；它与只返回导航卡片的 MCP 工具 `contexture_inspect` 不同。

该固定场景的结论是启动配置缺少 `DB_URL`；退出码 1 而非 137 支持区分它与
内存不足被杀死的情况。修复建议是补齐配置并重新发布，而不是盲目重启 Pod。

已知 ref 可以不经 open 直接调用。网关不强制 Skill 过程，也不保证模型遵循指导。
五个入口承载导航和调用；分支 Schema 与“不要先重启”的指导通过披露内容传递。
披露控制返回什么，不替代业务授权。

[两分钟介绍](../../docs/overview.zh-CN.md)中的服务运维动画解释同一框架，
但不是本 Kubernetes 场景或真实模型运行的录屏。
