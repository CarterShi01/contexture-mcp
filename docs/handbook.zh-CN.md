# Contexture 中文手册

[English](handbook.md) · [项目首页](../README.zh-CN.md)

这是 Contexture 应用的完整入门路径。第一次使用时建议按顺序完成：

```text
安装 → 生成 → 理解 → 检查 → 查看 → 调用 → 扩展 → 连接 Host
```

完成这条路径不需要先理解 `ControllerManager`、`Index` 或 MCP 线协议，也不
需要自己编写 `main()`。

## 1. 四个核心概念

| 概念 | 回答的问题 | 首次出现的位置 |
| --- | --- | --- |
| `Contexture` / `app` | 这个应用由什么组成？ | 包的 `__init__.py` |
| `Role` | Agent 应进入哪个稳定职责？ | `role.py` |
| `Skill` | Agent 应遵循什么流程？ | `skills.py` |
| `Tool` | 哪项确定性工作应由代码执行？ | `tools.py` |

Role、Skill 与 Tool 都是一等概念。Role 是稳定的职责边界；Skill 是模型遵循
的流程，Contexture 不执行它；Tool 是由 Contexture 执行的带类型 Python
代码。进程、任务、连接器实例等运行时记录应是 Tool 返回的数据，而不是 Role。

所有节点共用一套渐进式 Disclosure。打开 Role 只展示下一层成员卡片；显式
`uses` 展示依赖，但不会自动泄露未进入分支的反向依赖。实时健康状况属于只读
Tool。调用次数、错误次数和最后使用时间进入 Telemetry 旁路，不进入 Agent
上下文。

## 2. 安装并生成项目

前置条件：Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)。

```bash
uv tool install contexture-mcp
contexture --version
contexture new hello-context
cd hello-context
uv sync
```

生成的项目有意保持很小：

```text
hello-context/
├── pyproject.toml
├── README.md
└── hello_context/
    ├── __init__.py       # 导出唯一的 app
    ├── role.py           # 职责边界
    ├── skills.py         # Agent 遵循的流程
    └── tools.py          # 可执行能力
```

它是由 Contexture 托管的应用，不是必须发布到 PyPI 的库，因此不需要自己的
构建系统、控制台命令或 `main.py`。

## 3. 理解生成的应用

`hello_context/__init__.py` 是唯一组合根：

```python
app = Contexture(name="hello-context", roots=(HelloContextAssistant,))
```

该声明是惰性的：导入时不会创建节点、打开连接或启动服务器。命令真正运行时，
Contexture 才消费它。

生成的 Role 同时持有 Skill 和 Tool：

```python
skills=[CheckTarget()],
tools=[Ping()],
```

Skill 通过 `uses=("hello-context-assistant/ping",)` 声明所需 Tool。
`Ping.invoke(target: str)` 是业务代码；其参数名和类型也用于生成 MCP 输入
Schema，无需再维护第二份 Schema。

## 4. 完成本地开发闭环

连接 MCP Host 前，先在项目根目录执行：

```bash
uv run contexture check
uv run contexture list
uv run contexture inspect hello-context-assistant/check-target
uv run contexture call hello-context-assistant/ping --input '{"target":"example.test"}'
```

- `check` 构建并校验应用，但不打开外部连接。
- `list` 列出每个 Role、Skill、Tool 及其规范 ref。
- `inspect` 展示 Agent 打开节点时真正收到的指令。
- `call` 通过与正式服务相同的 Binding 调用 Tool，但不启动 MCP。

每个命令只回答一个问题：

| 问题 | 命令 |
| --- | --- |
| 应用能否编译？ | `contexture check` |
| 存在哪些 ref？ | `contexture list` |
| Agent 会看到什么？ | `contexture inspect` |
| 一个 Tool 返回什么？ | `contexture call REF --input JSON` |

不要把 `serve` 当成调试 Tool 的第一步。

## 5. 完成第一次修改

把 `hello_context/tools.py` 中的占位实现改为：

```python
async def invoke(self, target: str) -> str:
    return f"{target}: connected"
```

然后执行最快的两个检查：

```bash
uv run contexture check
uv run contexture call hello-context-assistant/ping --input '{"target":"api.internal"}'
```

如果增加或修改 Tool 参数，只修改 `invoke()` 签名并重跑 `check`；Binding 与
Schema 会从这一份声明重新生成。

## 6. 把能力放在正确位置

- 代码能够确定性完成并返回结果：添加 **Tool**，并把实例放入所属 Role 的
  `tools=[...]`。
- Agent 需要流程、顺序、证据规则或判断：添加 **Skill**，放入
  `skills=[...]`，用 `uses` 指向所需 Tool。
- Agent 必须在不同业务职责间选择：才添加子 **Role**，放入
  `children=[...]`。
- Role 需要一棵独立、延迟展开的准备或收尾能力子树：添加
  **PreProcess** 或 **PostProcess**，放入 `pre_process=...` 或
  `post_process=...`；它们不是可选业务分支。

修改图之后执行：

```bash
uv run contexture check
uv run contexture list
uv run contexture inspect --all --summary
```

ref 应来自 `list` 或 `inspect` 返回的卡片，不要靠字符串猜测。

### 可选过程成员（Contexture 1.0）

PreProcess 与 PostProcess 都是 Role 的特化，不是新节点类型，也不是会自动执行的
回调。业务构造函数照常提供 `instructions`、Skill、Tool 与 `uses`：

```python
from contexture import PostProcess, Role


class PreserveResults(PostProcess):
  def __init__(self) -> None:
    super().__init__(
      name="preserve-results",
      description="Preserve supported findings and their receipt.",
      instructions="Store only supported findings and report the actual receipt.",
      tools=[SaveFindings()],
    )


class TaskWorker(Role):
  def __init__(self) -> None:
    super().__init__(
      name="task-worker",
      description="Perform one task from supplied context.",
      instructions="Work from the supplied task context and evidence.",
      post_process=PreserveResults(),
    )
```

打开 owner 时，PostProcess 仍只是一张普通 Role 卡片，并以 `post_process` 字符串
标明真实 ref。框架会在业务 instructions 之后追加固定头尾标记，要求 Agent 在结束
前打开该 ref；打开只披露流程，并不执行 Tool 或证明成功。PreProcess 对称地出现在
业务 instructions 之前，要求开始前打开，并在准备完成后回到 owner instructions。

不声明过程成员时，ROUTE/ACTIVE 输出逐字保持不变。过程成员不进入 `branches()`，
不被后代隐式继承，也不提供开始/结束事件或执行保证。PreProcess 应装载“Agent 要执行
的准备流程”，不能替代必须成立的不变量；能由 Tool 检查的保证必须在 Tool 内拒绝违规
调用。

业务可用框架导出的 `binding_instruction(source, body, action=None)` 标记确实无法用
代码强制的硬规则。`source` 必须诚实标明业务权威，不能冒充 `contexture`。详情见
[ADR 023](adr/023-process-members-and-instruction-emphasis.md)。

## 7. 只在需要时连接外部系统

当 Tool 需要数据库、HTTP 客户端或集群连接时，增加 `Channels` 子类，并在
同一个应用声明中指定：

```python
app = Contexture(
    name="operations",
    roots=(Operations,),
    channels=OperationsChannels,
)
```

`Channels.__init__()` 只保存便宜的配置；`open()` 建立连接；`close()` 释放
连接。`check` 不调用 `open()`，`call` 与 `serve` 使用完整生产生命周期。
建立 Channels 后，先用一个只读 `call` 检验连接及清理路径。

## 8. 添加可选入口

大多数应用只需要 Role、Skill 与 Tool。只有在对应协议入口确实有价值时才添加：

- `Prompt`：由用户主动触发的入口。
- `Resource`：Host 可按稳定 URI 读取的内容。

二者都指向图中已有节点，不复制 Tool 或 Skill 的业务逻辑：

```python
app = Contexture(
    name="operations",
    roots=(Operations,),
    prompts=(RollbackRelease,),
    resources=(OperationsRunbook,),
)
```

如果一棵完整树只有在用户选择 Prompt 后才有意义，把它声明在
`prompt_roots=(Commands,)`。它仍可由 Prompt 与 `goto` 到达，但不会出现在
模型控制的发现网关中。

## 9. 连接 MCP Host

本地闭环通过后，让 Host 启动同一个命令：

```bash
claude mcp add --scope project hello-context -- uv run contexture serve
codex mcp add                 hello-context -- uv run contexture serve
```

stdio 模式下不要提前在另一终端常驻 `contexture serve`；Host 会为会话启动并
管理该进程。Agent 先得到 Role 路由卡，再逐步打开所需分支。业务 Tool 不会
被摊平到 MCP 顶层工具列表。

## 10. 本地安全与故障处理

`call` 默认只执行只读 Tool。要执行可能改变外部系统的 Tool，必须显式决定：

```bash
uv run contexture call REF --input '{...}' --allow-write
```

`call` 不会伪造远端调用者身份。认证、`Principal` 与 Host 特定行为必须通过
真实服务器集成来测试。

| 现象 | 下一步 |
| --- | --- |
| `check` 失败 | 修正错误中点名的声明或 ref，再运行 `check` |
| ref 未知 | 运行 `contexture list`，查看所属 Role |
| 把 Role/Skill 传给 `call` | 改用 `contexture inspect REF` |
| 写 Tool 被拒绝 | 审核影响后增加 `--allow-write` |
| 外部调用失败 | 检查 Channels 配置，先测试一个只读 Tool |

诊断写到 stderr，Tool 结果写到 stdout，因此 `call` 输出仍可供脚本消费。

## 11. 部署或嵌入

优先从 stdio 开始。Streamable HTTP 不改变声明：

```bash
uv run contexture serve --transport streamable-http --port 8080
```

公开地址必须通过 `ContextureOptions` 显式配置 Host/Origin 及认证或匿名策略。
HTTP 请求可通过 `HeaderSurfaceSelector` 和 `Contexture-Select` 缩小到完整
子树。它支持精确 ref（如 `team/notebook-editor`）和末段直接子级模式（如
`team/*`）；每个匹配项都会成为 surface root，且 `*` 不跨越 `/`。旧的
`HeaderRootSelector` 与 `Contexture-Roots` 继续兼容。应用可根据已验证的
`Principal` 提供 ceiling 进一步收窄，但选择本身不授予权限。

```python
from contexture.server import HeaderSurfaceSelector, compile_application

server = compile_application(app).server(
    surface_selector=HeaderSurfaceSelector(),
)
```

只有嵌入已有进程、已有事件循环或需要代码化托管选项时才写 `main()`：

```python
from hello_context import app
from contexture.server import ContextureOptions, serve


def main() -> None:
    serve(app, ContextureOptions(transport="stdio"))
```

已有事件循环可用 `build_server(app)` 并 await 异步启动方法。普通应用不应手工
组装 `ControllerManager`、`Index` 或 `ContextureServer`。

同一运行时如需支持人类界面，用 `RestSurface` 和显式 `Route` 白名单。GET/
HEAD 只能指向只读 Tool，写方法只能指向写 Tool。如果需要完全独立、不可执行
的架构视图，应另写声明并使用 `compile_disclosure_application`，不要过滤运行时
Index。

## 12. 完成清单

- [ ] `contexture new` 已生成项目。
- [ ] `uv run contexture check` 成功。
- [ ] `contexture call` 已返回自己的 Tool 结果。
- [ ] 能区分 Role、Skill 与 Tool。
- [ ] 知道新能力应注册到哪个 Role。
- [ ] 已用 `list` 和 `inspect` 检查 ref 与 Agent 可见文本。
- [ ] 本地写 Tool 只在显式 `--allow-write` 后执行。
- [ ] Host 能成功启动 `uv run contexture serve`。

## 内部实现与设计决策

运行路径是：

```text
Contexture → ControllerManager → Index → Disclosure → ContextureServer
```

这些是框架内部。只有在修改 Contexture 本身时才需要阅读 [ADR](adr/)；编写
业务应用时应坚持使用 `contexture` 与 `contexture.server` 的公共入口。
