# Contexture

[English](README.md) · [中文手册](docs/handbook.zh-CN.md) · [语言无关规范](spec/README.md) · [变更记录](CHANGELOG.md)

语言实现：Python（当前仓库）·
[TypeScript](https://github.com/CarterShi01/contexture-mcp-typescript) ·
[Go](https://github.com/CarterShi01/contexture-mcp-go)

Contexture 是一个 Python 框架，用来把大型应用的能力图暴露给 Agent，
同时避免一次性把所有工具与指令塞进模型上下文。开发者声明 Role、Skill
与 Tool；Contexture 将它们编译为不可变图，并通过一个很小、固定的 MCP
网关，只逐步公开 Agent 选择的分支。

同一份应用运行时也可以支持显式白名单式 REST 路由，供面向人的界面使用。
Contexture 属于 Controller 层：它不实现 Agent 循环、不调用模型，也不替代
你的业务服务。

- 支持 Python 3.11–3.14
- 支持 MCP stdio 与 Streamable HTTP
- 提供类型标注并包含 `py.typed`
- Apache-2.0 许可证
- 稳定的 1.0 公共 API，后续兼容性遵循语义化版本规范

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

## 五分钟创建应用

```bash
contexture new hello-context
cd hello-context
uv sync
uv run contexture check
```

核心编写模型很小：

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
uv run contexture inspect operations/diagnose
uv run contexture call operations/check-status --input '{"service":"api"}'
uv run contexture serve
```

`check` 只编译和校验，不建立外部连接；`list` 显示规范 ref；`inspect`
重放 Agent 实际会看到的内容；`call` 通过生产 Binding 调用本地只读 Tool。
写操作 Tool 必须额外明确传入 `--allow-write`。

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
[编写示例](docs/handbook.zh-CN.md#可选过程成员python-0160)与
[ADR 023](docs/adr/023-process-members-and-instruction-emphasis.md)。

## 渐进式公开

MCP Host 始终只看到五个由模型控制的固定工具：

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
