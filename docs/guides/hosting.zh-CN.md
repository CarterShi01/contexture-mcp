# MCP 与 REST 托管

[English](hosting.md) · [文档导航](../README.zh-CN.md) · [应用声明](authoring.zh-CN.md)

## 连接 MCP Host

完成[快速开始](../../README.zh-CN.md#快速开始)后，在生成的项目目录中选择
**一个**注册命令。stdio 进程由 Host 启动并管理。

```bash
# Claude Code：项目范围配置
claude mcp add --scope project hello-context -- uv run contexture serve

# 或 Codex：修改用户配置
codex mcp add hello-context -- uv run contexture serve
```

从该项目启动所选 Host，检查 MCP 连接状态，再请它使用 `hello-context`。
以后移除连接时只删除该名称对应的配置，保留其他服务器。详见
[连接教程](../handbook.zh-CN.md#9-连接-mcp-host)。

## 网关与披露

标准运行时服务提供五个固定、由模型控制的工具：

```text
contexture_discover
contexture_inspect
contexture_open
contexture_invoke_read_only
contexture_invoke
```

Discover 返回起点，Inspect 比较导航卡片而不加载指令或 Tool Schema。
打开 Role 会返回局部指导和下一层成员卡片，包括其 Tool 的 Schema。
两个 invoke 入口区分只读与写调用；走错入口会被拒绝。业务授权仍由应用和 Host 负责。

可选的纯披露服务只提供发现、检查和打开，不提供调用。见
[部署或嵌入](../handbook.zh-CN.md#11-部署或嵌入)。

## Streamable HTTP

```bash
uv run contexture serve --transport streamable-http --port 8080
```

非回环地址需要明确选择认证或匿名访问，并设置允许的 Host／Origin；通过
`ContextureOptions` 配置。参见[本地安全](../handbook.zh-CN.md#10-本地安全与故障处理)。

HTTP 部署可通过 `Contexture-Select` 和 `HeaderSurfaceSelector` 选择完整子树。
精确 ref 选择对应子树，`team/*` 选择每个直接成员子树，`*` 不跨越 `/`。
旧名称 `Contexture-Roots`、`HeaderRootSelector` 仍兼容。基于已验证 `Principal`
的应用上限可以进一步收窄范围，但选择不替代权限策略。

以下片段假定 `app` 是你的[应用声明](authoring.zh-CN.md)：

```python
from contexture.server import HeaderSurfaceSelector, compile_application

server = compile_application(app).server(
    surface_selector=HeaderSurfaceSelector(),
)
```

## 面向人的 REST 路由

Agent 需要导航，而 Dashboard 的页面和按钮已经确定路径，因此在同一运行时上
使用显式 Tool 白名单。下例的 `my_context` 代表导出 `operations` 示例 `app` 的包：

```python
from contexture.server import compile_application
from contexture.web import RestSurface, Route
from my_context import app

compiled = compile_application(app)
rest = RestSurface(
    compiled.runtime(),
    routes=(Route("GET", "/v1/status", "operations/check-status"),),
)
asgi_app = rest.asgi_app()
```

GET/HEAD 只能指向只读 Tool；写请求只能指向写 Tool；未列出的 ref 无法经 REST
访问。两种接口都不复制业务逻辑，也不替代服务自身的授权检查。
