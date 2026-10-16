# Contexture

[English](README.md) · [文档导航](docs/README.zh-CN.md) · [中文手册](docs/handbook.zh-CN.md) · [语言无关规范](spec/README.md) · [变更记录](CHANGELOG.md)

语言实现：Python（当前仓库）·
[TypeScript](https://github.com/CarterShi01/contexture-mcp-typescript) ·
[Go](https://github.com/CarterShi01/contexture-mcp-go)

**一套模型，连接应用结构与 Agent 交互。**

Contexture（`contexture-mcp`）是用于构建 MCP 服务与 AI 原生应用的应用层框架。
用 **Role** 组织职责、**Skill** 表达任务指导、**Tool** 连接可执行动作。
同一套声明既组织应用，也生成面向 Agent 的接口。

![Role 内包含指导、Skill、Tool 和子 Role；应用能力图递归组合这些职责边界。](docs/media/contexture-overview.png)

**[观看 1 分 59 秒介绍](docs/overview.zh-CN.md)** ·
**[体验内置 Demo](contexture/demo/README.zh-CN.md)** ·
**[创建自己的应用](#五分钟创建应用)**

## 为什么使用 Contexture？

暴露函数只是构建面向 Agent 的应用的一部分。开发者还需要组织职责、说明能力
如何配合，并在应用变化时让这些指导保持对应。

- **围绕职责组合能力。** 将局部指导、过程、带类型动作和子 Role 放在一起；
    引用已有能力，而不是重复实现。
- **让结构与交互使用同一模型。** Contexture 检查声明关系，从编译后的图生成
    导航卡片、指导和 Tool Schema。Agent 可以探索相关分支、按需获取细节，
    而不是一开始接收全部内容。
- **沿用现有服务。** Tool 适配已有业务代码；显式 REST 路由可以与 MCP
    共用执行和验证运行时。

目标是让不断增长的应用更容易理解、扩展和维护，而不只是缩短初始工具列表。
Contexture 属于 Controller 层：不运行 Agent 循环、不调用模型、不替代业务服务，
也不强制执行 Skill 中描述的过程。

FastMCP、mcp-use 等框架让 MCP 服务开发更方便。**Contexture 做出了一个明确的架构选择：**
职责导向的能力组合与 Agent 交互共用一个应用模型。
详见[设计、其他方案与取舍](docs/design.zh-CN.md)。

### 是否适合你的应用？

当应用有多个职责、可复用过程，以及需要共同演进的能力时，可以考虑 Contexture。
少量相互独立的工具通常直接暴露更简单。导航会增加调用，通用网关也不会将每个
业务 Tool 暴露为 MCP 顶层原生工具。业务授权仍由应用与 Host 负责。

### Python 实现

- 支持 Python 3.11–3.14
- 支持 MCP stdio 与 Streamable HTTP
- 提供类型标注并包含 `py.typed`
- Apache-2.0 许可证
- 稳定的 1.0 公共 API，遵循语义化版本

## 安装

使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv tool install contexture-mcp
contexture --version
```

也可以加入现有 Python 项目：

```bash
uv add contexture-mcp
# 或：python -m pip install contexture-mcp
```

可通过 `contexture-mcp==1.0.0` 固定安装本版本。

### 先体验，再构建

安装后的 CLI 内置确定性的 Kubernetes 事故 Demo，不需要集群或服务凭据：

```bash
contexture list contexture.demo.role:KubernetesPlatform
contexture inspect --target contexture.demo.role:KubernetesPlatform kubernetes-platform/incident-response
```

这些命令在不使用 LLM 的情况下查看声明。按照[Demo 指南](contexture/demo/README.zh-CN.md)
连接 MCP Host、读取固定示例数据，并了解披露与执行的区别。

## 五分钟创建应用

```bash
contexture new hello-context
cd hello-context
uv sync
uv run contexture check
```

脚手架生成的项目可以直接运行；[中文手册](docs/handbook.zh-CN.md)逐步解释生成的
`hello-context-assistant` Role。下面的独立示例使用 `operations` Role 展示同一个
简洁编写模型，不会自动替换生成的文件。

```python
from contexture import Contexture, Role, Skill, Tool


class CheckStatus(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="check-status",
            description="返回一个服务的状态。",
            read_only=True,
        )

    async def invoke(self, service: str) -> dict[str, str]:
        return {"service": service, "status": "ready"}


class Diagnose(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="diagnose",
            description="诊断异常服务。",
            instructions="先读取状态，再根据证据进行解释。",
            uses=("operations/check-status",),
        )


class Operations(Role):
    def __init__(self) -> None:
        super().__init__(
            name="operations",
            description="处理服务运维任务。",
            instructions="提出变更前必须先检查现状。",
            skills=[Diagnose()],
            tools=[CheckStatus()],
        )


app = Contexture(name="service-operations", roots=(Operations,))
```

Contexture 不会从类名或 docstring 推导公开名称与描述。Tool 的 `invoke()`
类型标注同时用于生成输入 Schema 和验证实际调用，因此 Schema 与运行逻辑
不会由两套定义分别维护。

## 本地开发闭环

```bash
uv run contexture check
uv run contexture list
uv run contexture inspect hello-context-assistant/check-target
uv run contexture call hello-context-assistant/ping --input '{"target":"example.test"}'
uv run contexture serve
```

`check` 只编译和校验，不建立外部连接；`list` 显示规范 ref；`inspect`
重放 Agent 实际会看到的内容；`call` 通过生产 Binding 调用本地只读 Tool。
写操作 Tool 必须额外明确传入 `--allow-write`。

这些命令针对未修改的 `hello-context` 脚手架。自己的应用应通过 `list` 获取 ref，
不要将独立示例的 `operations` 路径直接用于另一个项目。

## 一套声明词汇

| 概念 | 编写方式 | 含义 |
| --- | --- | --- |
| `Contexture` | 一个应用值 | 惰性的组合根 |
| `Role` | 子类与构造函数 | 职责和包含关系边界 |
| `PreProcess` / `PostProcess` | Role 特化与构造函数 | 可选的准备与收尾流程及专属能力 |
| `Skill` | 子类与构造函数 | 模型遵循的流程知识 |
| `Tool` | 子类与带类型的 `invoke()` | Contexture 执行的确定性代码 |
| `Prompt` | 子类与构造函数 | 用户主动触发、指向既有节点的入口 |
| `Resource` | 子类与构造函数 | Host 可读、由既有只读 Tool 支持的 URI |
| `Channels` | 可选子类 | 共享外部依赖及其生命周期 |

Role、Skill 与 Tool 组成能力图。Prompt 和 Resource 不复制节点，而是为图中
已有 ref 提供另一种协议入口。只允许由用户控制的 Prompt 平面进入的完整树，
应放在 `prompt_roots` 中。

稳定的 1.0 API 允许 Role 声明可选的 `pre_process` 与 `post_process`。ACTIVE
在业务 `Role.instructions` 前后拼接固定、可识别的框架指令块，要求 Agent 在开始或
结束前打开真实 ref；过程自身的说明与能力仍保持延迟公开，打开不会执行任何 Tool。
两个字段均缺省时，既有输出与义务不变。业务还可用 `binding_instruction` 以自己的
权威名称标记无法用代码强制的硬规则。参见
[编写示例](docs/handbook.zh-CN.md#可选过程成员contexture-10)与
[ADR 023](docs/adr/023-process-members-and-instruction-emphasis.md)。

## 渐进式公开

标准运行时 MCP 服务提供五个由模型控制的固定工具：

```text
contexture_discover
contexture_inspect
contexture_open
contexture_invoke_read_only
contexture_invoke
```

`discover` 返回根节点卡片；`inspect` 在不激活 instructions 的前提下比较候选。
打开 Role 后，仅返回该 Role 的指令和下一层
Role、Skill、Tool 卡片。Tool 卡片包含调用所需的 ref、输入 Schema 和只读
分类。业务 Tool 不会膨胀 MCP 顶层工具列表。

两个 invoke 入口分开，是为了让 Host 能依据可见的 MCP `readOnlyHint`
执行审批策略。走错入口会被拒绝。渐进式公开解决“模型知道什么”，并不等于
授权策略。

可选的纯披露服务只提供发现、检查和打开，不提供调用；详见[手册](docs/handbook.zh-CN.md)。

## 连接 MCP Host

本地检查通过后，让 Host 管理 stdio 子进程：

```bash
claude mcp add --scope project hello-context -- uv run contexture serve
codex mcp add                 hello-context -- uv run contexture serve
```

使用 Streamable HTTP：

```bash
uv run contexture serve --transport streamable-http --port 8080
```

绑定非回环地址时，必须显式选择认证或匿名访问策略，并设置允许的 Host 与
Origin。代码中通过 `ContextureOptions` 配置，详见[中文手册](docs/handbook.zh-CN.md)。

HTTP 部署可以用 `Contexture-Select` 请求头与 `HeaderSurfaceSelector` 把一次
请求缩小到若干完整子树。`team/notebook-editor` 精确选择该子树，`team/*`
选择 `team` 的各个直接成员子树，且 `*` 永远不会跨越 `/`。旧的
`Contexture-Roots` 与 `HeaderRootSelector` 名称继续兼容。应用还可以根据已
验证的 `Principal` 设置更严格的上限。选择是能力表面边界，不替代业务权限
判断。

```python
from contexture.server import HeaderSurfaceSelector, compile_application

server = compile_application(app).server(
    surface_selector=HeaderSurfaceSelector(),
)
```

## 面向人的 REST 路由

Agent 需要逐步导航，而 Dashboard 的页面和按钮已经完成导航决策，因此 REST
侧采用显式 Tool 白名单：

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

GET/HEAD 只能指向只读 Tool；写请求只能指向写 Tool；未列出的 ref 无法经
REST 到达。

## 深入了解

- [文档导航](docs/README.zh-CN.md)：概念、教程、参考与历史。
- [设计与取舍](docs/design.zh-CN.md)：为什么使用同一模型，以及何时不需要它。
- [图解介绍与文字稿](docs/overview.zh-CN.md)：已实现的交互模型。
- [路线图](docs/roadmap.zh-CN.md)：计划中的可视化与能力级评估。

## 公共 API

业务声明从 `contexture` 导入，高级托管能力从 `contexture.server` 导入。
两者的导出集合都有回归快照。`contexture.core` 及具体 server 子模块属于实现
细节，即使 Python 语法上仍可导入，也不承诺兼容。

## 参与开发

```bash
git clone https://github.com/CarterShi01/contexture-mcp.git
cd contexture-mcp
uv sync --extra dev
uv run --extra dev pytest -q
uv run --extra dev pyright
uv run --extra dev ruff check contexture tests
uv build
uv run --extra dev twine check --strict dist/*
```

修改契约前请阅读[贡献指南](CONTRIBUTING.md)；开发应用请读
[中文手册](docs/handbook.zh-CN.md)；实现其他语言 Binding 时请读
[语言无关规范](spec/README.md)。安全问题请按 [SECURITY.md](SECURITY.md)
私下报告。

## 许可证

Apache-2.0，详见 [LICENSE](LICENSE)。
