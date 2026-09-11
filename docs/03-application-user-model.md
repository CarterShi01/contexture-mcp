# Design 03 — 从安装到上线的一条 Application 用户路径

**状态：** 已接受，已实施

**日期：** 2026-08-21

**适用范围：** Contexture Python 基础库的用户模型、公共 API、脚手架、CLI、打包与未来多语言接口

> 本文回答的是“业务开发者如何使用 Contexture”，不是重新设计导航内核。
> `ControllerManager → Index → Disclosure`、固定的 MCP gateway、地址规则和
> progressive disclosure 继续由既有 ADR 约束。

## 1. 决策摘要

Contexture 对业务开发者只讲一条主线：

```text
我声明一个 Contexture Application
        ↓
用 Role 划分业务责任
        ↓
在 Role 中编写同级的 Skill 和 Tool
        ↓
本地 check / inspect / call
        ↓
默认用 contexture serve 启动
        ↓
需要嵌入或定制部署时再写 main()
```

本设计作出以下决定：

1. 新增惰性的 `Contexture` Application 声明，作为业务应用唯一组合根。
2. `Role`、`Skill`、`Tool` 从第一课开始同时出现；Skill 不是高级附加能力。
3. `[tool.contexture]` 最终只指向一个 `app`，不再分别重述 roots、channels、publish。
4. 默认用户不写 `main()`；自定义 `main()` 仍是一等能力，并与 CLI 消费同一个 app。
5. 新增 `contexture check` 和 `contexture call`，补齐不连接 MCP Host 的本地开发闭环。
6. 保持当前平铺的 `contexture/` 源码结构，不引入 `src/`，不重排现有内核目录。
7. Python 使用顶层 facade、`.pyi` 和 `py.typed` 提供集中、可读的公共接口视图；不新增
   `contexture.interfaces` 运行时导入路径。
8. 使用支持平铺模块根的 `uv_build` 构建 wheel，并在仓库外隔离安装后验证，杜绝旧
   `build/lib` 混入已删除源码。
9. 跨语言的权威来源是公共模型规范、行为规范与 conformance fixtures；`.pyi` 只是
   Python binding，不是跨语言标准。

## 2. 为什么需要这一层

现有内核的职责已经清楚：

```text
ControllerManager   存在什么、能接触什么、何时打开和关闭
Index               地址、类型、schema 与调用绑定等编译事实
Disclosure          本次调用披露多少、以卡片还是全文呈现
MCP Surface         把固定 gateway 和可选 prompt/resource 放到协议上
```

但业务用户安装后面对的是另一组问题：

1. 第一份文件写在哪里？
2. 什么是整个应用的入口？
3. Skill 和 Tool 分别什么时候写？
4. 不配置 Claude Code、Codex 或其他 Host，怎样直接验证一次调用？
5. 默认命令启动和手写 `main()` 是否是两套应用？
6. 安装的 wheel 中哪些名字是稳定接口，哪些只是框架内部实现？

当前 `[tool.contexture]` 同时承担应用声明和启动组装：它分别列出 `name`、`roots`、
`channels` 和混合的 `publish`。手写入口则直接组装 Manager、Index、Server。两条路径
最终运行同一内核，却没有共享一个业务层对象。

缺失的不是第四个内核对象，而是一个位于内核之前的 **Application 声明**。

## 3. 范围与非目标

### 3.1 本设计包含

- Application 公共模型及其惰性语义。
- 新手从安装、生成项目、Hello World、本地调试到连接 Host 的完整路径。
- 默认启动和手写入口的统一方式。
- CLI 命令职责及安全边界。
- 脚手架和 handbook 的目标结构。
- 公共接口在 Python wheel 中的交付方式。
- 保持平铺布局的可靠构建方式。
- TypeScript、Go、PHP 的未来映射原则。
- 对现有项目配置和低层入口的兼容策略。

### 3.2 本设计明确不包含

- 不恢复或引入 `src/` 目录。
- 不重写 `ControllerManager → Index → Disclosure`。
- 不改变四个固定 gateway tool 的协议行为。
- 不改变 ref、disclosure payload 或现有 golden fixtures，除非实施时发现独立缺陷并另立 ADR。
- 不在本阶段实现 TypeScript、Go 或 PHP 版本。
- 不复制一套 `Role/Skill/Tool` 到所谓 interfaces 包中。
- 不把 Agent loop、模型选择或 tool selection 收进 Contexture；这些仍属于连接它的 Host。

## 4. 核心业务模型

### 4.1 用户必须掌握的四个概念

```text
Contexture Application
└── Role
    ├── Role
    ├── Skill
    └── Tool
```

`Skill` 和 `Tool` 是与 `Role` 同等重要的一级建模概念。上图表达的是常见的所有权，
不是学习顺序或重要性排序。内核继续允许独立 Skill 或 Tool 成为 root；脚手架的默认
路径使用 Role 作为 root，因为真实业务通常需要一个责任边界。

| 概念 | 用户要回答的问题 | 用户如何编写 | 谁执行 |
| --- | --- | --- | --- |
| `Contexture` | 这是哪个应用，入口和依赖是什么？ | 构造一个惰性声明对象 | runner 消费 |
| `Role` | 谁负责这一类请求？ | 继承并声明 children/skills/tools | 模型导航，框架组织 |
| `Skill` | 这类工作应该怎样完成？ | 继承并写 instructions/uses | 模型遵循 |
| `Tool` | 哪个结果可以由程序确定执行？ | 继承并实现 typed `invoke()` | 框架调用 |

最关键的放置规则只有两句：

> 需要模型判断并遵循步骤的工作是 Skill。
>
> 能由程序确定执行并返回结果的能力是 Tool。

### 4.2 逐步出现的扩展概念

| 概念 | 何时出现 | 用户动作 |
| --- | --- | --- |
| `Channels` | Tool 需要数据库、HTTP client、集群连接等外部能力时 | 继承并实现生命周期 |
| `Prompt` | 人需要显式触发一个高价值或高风险入口时 | 声明一个指向既有节点的类 |
| `Resource` | Host 需要用稳定 URI 读取既有内容时 | 声明一个指向既有节点的类 |
| `Principal` | Tool 需要知道本次调用者身份时 | 接收框架注入的值，不自行构建 |
| server/options/auth | 自定义 transport、认证或嵌入已有进程时 | 从 `contexture.server` 使用 |

这些概念不会全部塞进 Hello World。它们在用户确实遇到相应问题时再出现。

### 4.3 与 brpc 和 MVC 的对应关系

这里的 MVC 对应是整个 Controller 层，而不只是 `Tool`：`Role / Skill / Tool` 是
三种业务 Controller，`ControllerManager / Index / Disclosure` 负责它们的持有、
编译和路由。`Disclosure` 产出 payload，但它掌握的是路由与披露决策，不是最终
View；最终 View 是连接 Contexture 的 Agent Host。完整请求链路和两个分析尺度见
[Design 05](05-controller-framework-and-mvc.md)。

| Contexture | brpc / MVC 中近似的角色 | 边界说明 |
| --- | --- | --- |
| `Contexture` | brpc `Server` 的业务装配 / MVC `Application` | 声明应用，不是运行中的 socket/server |
| `Role` | Service / composite Controller / bounded context | 组织业务责任和其他 Controller，不直接成为 MCP method |
| `Tool` | RPC method / Controller action | 框架执行的确定性 Controller |
| `Skill` | model-executed Controller / workflow / playbook | 控制模型执行的方法，不是普通函数调用 |
| `Channels` | brpc Channel / DB client / DI container | 应用拥有的外部连接与句柄 |
| `Principal` | RPC Controller / HTTP Request 中的调用者事实 | 框架按请求创建或绑定 |
| `Prompt/Resource` | 额外发布的 command/route | 指向既有能力，不复制业务定义 |
| MCP gateway | 框架固定的路由与 dispatch | 业务不能增加第五个系统 tool |

这个类比只用于帮助理解职责，不要求四套框架拥有相同类继承结构。

## 5. Application 声明

### 5.1 用户写法

```python
from contexture import Contexture

from .role import Hello

app = Contexture(
    name="hello-context",
    roots=(Hello,),
)
```

当应用增长时，仍然只扩展同一个声明：

```python
app = Contexture(
    name="operations",
    roots=(KubernetesPlatform,),
    channels=ClusterChannels,
    prompts=(RollBackARelease,),
    resources=(CrashLoopRunbookDocument,),
)
```

字段保存的是类或零参数 factory，不是已经构建的 Role、Skill、Tool、Channels、Prompt
或 Resource 实例。

### 5.2 概念接口

Python binding 的目标形状是：

```python
RootFactory = type[Role] | type[Skill] | type[Tool]

class Contexture:
    def __init__(
        self,
        *,
        name: str,
        roots: Sequence[RootFactory],
        channels: type[Channels] | None = None,
        prompts: Sequence[type[Prompt]] = (),
        resources: Sequence[type[Resource]] = (),
    ) -> None: ...
```

具体实现应使用冻结、带 slots 的值对象，并将传入序列正规化为 tuple。公开名称只使用
`Contexture`，不同时增加 `Application`、`App` 或 `ContextureApp` 别名。

### 5.3 惰性不变量

新的 `Contexture` 与历史上已删除的 `ContextureApp` 有本质区别：它是声明，不是
Server facade。必须由测试固定以下行为：

1. import 声明模块时不实例化任何 ContextNode。
2. import 声明模块时不实例化 Channels，不打开连接。
3. import `contexture` 时不导入 MCP SDK。
4. `Contexture(...)` 不创建 Manager、Index、Disclosure 或 Server。
5. 每次 build 都从 factory 创建独立森林，不复用上一次节点实例。
6. 只有 runner/build 阶段才注册 root、编译 Index 并创建协议 surface。
7. app 被消费后仍不可追加 root 或发布项；变化通过创建新 app 声明完成。

Application 构造期只校验不需要实例化的事实，例如 name 非空、roots 非空、容器不可变
以及 factory 的基本形状。全森林校验继续发生在 `Index.of(...)` 的编译阶段。

### 5.4 Application 到内核的翻译

```text
load app target
      ↓
Contexture declaration                    用户层
      ↓ build（每次独立）
instantiate Channels（便宜、无 I/O）
      ↓
ControllerManager + register roots        持有与供给
      ↓
Index.of(manager, bind=TypeHintBinding)    编译、校验、派生 schema
      ↓
Disclosure(index)                         披露策略
      ↓
ContextureServer + MCP Surface            协议与 transport
```

该翻译器是薄适配层，不吸收 Manager、Index 或 Disclosure 的职责。

## 6. 一份声明，两种启动方式

### 6.1 默认：用户不写 main

项目只在 `pyproject.toml` 中命名 app：

```toml
[tool.contexture]
app = "hello_context:app"
```

启动：

```bash
uv run contexture serve
```

CLI 解析 target、加载 app、构建并启动 Server。TOML 不再分别重述 name、roots、
channels、prompt 和 resource，因此不存在 Python 声明与配置表互相漂移的问题。

### 6.2 高级：用户自己写 main

嵌入已有进程、自定义 transport 或认证时，用户显式写入口：

```python
from contexture.server import ContextureOptions, serve
from hello_context import app


def main() -> None:
    serve(app, ContextureOptions(transport="stdio"))


if __name__ == "__main__":
    main()
```

`contexture serve` 内部调用同一个 `serve(app, options)`。两种启动方式不允许分别实现
一套构建顺序。

### 6.3 专家级低层入口

当前 demo 使用的手工组装仍可保留，用于框架测试和极少数需要替换 binding/surface 的
场景：

```text
ControllerManager → Index → ContextureServer
```

它从 handbook 主路径移到“Embedding and internals”，不再要求普通业务用户理解。
本阶段不删除这些类型，也不承诺它们和 authoring API 具有相同稳定等级。

## 7. 六段渐进式用户旅程

### Journey 1 — 安装并完成 Hello World

安装 CLI：

```bash
uv tool install contexture-mcp
contexture --version
contexture new hello-context
cd hello-context
uv sync
```

安装 CLI 本身不向当前目录写业务文件；`contexture new` 才创建项目。`uv sync` 随后
生成 `.venv/` 和 `uv.lock`。

目标脚手架：

```text
hello-context/
├── .gitignore
├── pyproject.toml
├── README.md
└── hello_context/
    ├── __init__.py       app = Contexture(...)
    ├── role.py           Hello Role
    ├── skills.py         GreetUser Skill
    └── tools.py          SayHello Tool
```

第一课同时编写 Role、Skill 和 Tool：

```python
class GreetUser(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="greet_user",
            description="Greet one user by name.",
            instructions="Ask for the name, then use hello/say_hello.",
            uses=("hello/say_hello",),
        )
```

```python
class SayHello(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="say_hello",
            description="Return a greeting for one name.",
            read_only=True,
        )

    async def invoke(self, name: str = "world") -> str:
        return f"Hello, {name}!"
```

```python
class Hello(Role):
    def __init__(self) -> None:
        super().__init__(
            name="hello",
            description="Greet a user.",
            instructions="Use the greeting procedure for greeting requests.",
            skills=[GreetUser()],
            tools=[SayHello()],
        )
```

### Journey 2 — 不连接 Host 的本地开发与调试

```bash
uv run contexture check
uv run contexture list
uv run contexture inspect hello/greet_user
uv run contexture call hello/say_hello --input '{"name":"Alice"}'
```

用户应能在终端看到 `Hello, Alice!`，不需要先理解 MCP 配置或启动一个长期运行进程。

### Journey 3 — 把真实业务拆成 Role

当一个请求领域出现彼此替代的分支时才增加 child Role。一个任务需要同时使用的 Skill
和 Tool 留在同一 Role，避免为了目录整齐增加额外导航 round trip。

这一步学习：children、完整 ref、Skill `uses` 和 `contexture inspect --all`。

### Journey 4 — 连接外部系统

当 Tool 需要数据库、HTTP 服务或集群连接时增加 `Channels`：

```python
app = Contexture(
    name="operations",
    roots=(Operations,),
    channels=OperationsChannels,
)
```

`Channels.__init__` 只读取地址和静态配置，不做 I/O；`open()` 建立连接；`close()` 释放。
`contexture check` 会实例化并编译应用但不会调用 `open()`；`call` 和 `serve` 进入完整
provisioned lifecycle。

### Journey 5 — 增加面向人或 Host 的入口

Prompt 和 Resource 只指向树中已经存在的节点：

```python
app = Contexture(
    name="operations",
    roots=(Operations,),
    prompts=(RollBackARelease,),
    resources=(CrashLoopRunbookDocument,),
)
```

app 使用两个 typed collection，不再使用混装 `publish` 列表后由运行时分拣。

### Journey 6 — 连接 Host、部署或嵌入

最简单的 Host 配置只执行：

```bash
uv run contexture serve
```

之后才引入 stdio 与 streamable HTTP、认证、日志、自定义 `main()` 和进程生命周期。
这些内容不能出现在 Hello World 之前。

## 8. CLI 契约

| 命令 | 回答的问题 | 是否打开 Channels | 是否启动 MCP |
| --- | --- | --- | --- |
| `new` | 第一份项目写到哪里？ | 否 | 否 |
| `check` | 这个 app 能否被构建、校验和编译？ | 否 | 否 |
| `list` | 最终有哪些 Role/Skill/Tool 和 ref？ | 否 | 否 |
| `inspect` | 模型逐步会看到什么？ | 默认否 | 否 |
| `call` | 这个 Tool 在真实 binding/lifecycle 下返回什么？ | 是 | 否 |
| `serve` | 怎样通过 MCP 对外提供它？ | 是 | 是 |
| `demo` | 完整参考应用如何工作？ | 依 demo | 是 |

### 8.1 `contexture check`

`check` 必须走真实 Application loader、root registration、Index 编译和 schema 派生，
但不得建立外部连接或启动 MCP Server。它检查：

- app target 可导入且只解析到一个 `Contexture`。
- app name、root factory、Channels factory、Prompt/Resource factory 形状正确。
- Role 成员类型、重名、环、ref、Skill uses 和发布目标合法。
- Tool signature 可以派生出输入 schema。
- 输出按源位置或 ref 给出可操作诊断；成功时只打印一行摘要。

### 8.2 `contexture call`

`call` 必须复用生产 binding 和 Channels lifecycle，不写第二套“测试执行器”。

```bash
contexture call REF --input JSON
contexture call REF --input-file request.json
```

规则：

- 只允许 Tool ref；对 Role/Skill 给出“使用 inspect”的明确提示。
- 默认只调用 `read_only=True` 的 Tool。
- 写操作必须显式加 `--allow-write`。
- 参数使用与 MCP 调用相同的 schema 校验与错误翻译。
- stdout 只写结果，诊断写 stderr，便于脚本消费。
- 它是本地开发入口，不伪装远端 Principal；需要认证行为时使用集成测试或真实 server。

### 8.3 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 命令成功 |
| `1` | 应用构建、校验、调用或运行失败 |
| `2` | CLI 参数或项目配置使用错误 |

已有命令应逐步统一到这个约定。

## 9. Python 公共接口的交付

### 9.1 源码结构保持不变

不移动现有 `core/model`、`core/mcp_interface` 或 `server` 实现。只增加公共发布视图：

```text
contexture/
├── __init__.py             运行时 facade，转发到现有真实类型
├── __init__.pyi            authoring API 的静态接口视图
├── py.typed                PEP 561 标记
├── core/                   当前实现，不移动
├── server/
│   ├── __init__.py         高级运行时 facade
│   └── __init__.pyi        hosting API 的静态接口视图
└── ...
```

普通用户只有两个规范导入入口：

```python
from contexture import Contexture, Role, Skill, Tool
from contexture.server import serve, ContextureOptions
```

不创建 `contexture.interfaces` 或 `contexture.api` 第三条运行时路径。

### 9.2 公共类型不是复制品

`contexture.Tool` 必须继续是当前真实 Tool 的同一对象，而不是 wrapper、subclass 或
Protocol 替身。`.pyi` 只描述它，不参与运行。

只有存在必须覆盖的行为时才使用 ABC：

- `Tool.invoke()` 适合成为 abstract method。
- `Role` 和 `Skill` 是声明基类，不人为增加抽象方法。
- `Contexture` 是组合用值对象，不供继承。
- `Channels` 保留可选生命周期默认值，使无外部连接的应用无需空实现。

### 9.3 接口说明的内容顺序

公开类的 docstring 和 stub 注释统一回答：

1. 什么时候使用。
2. 最小可运行示例。
3. 必须声明或覆盖什么。
4. 框架何时构造和调用。
5. 生命周期、并发和安全约束。
6. 常见错误。
7. 与相邻概念的区别。

历史演进和内部算法移到 ADR。`help(Tool)`、IDE hover 和 handbook 应讲同一个故事。

## 10. 打包与安装模型

### 10.1 保持 flat layout

仓库继续使用：

```text
Contexture/
├── pyproject.toml
└── contexture/
```

不增加 `src/`。源码清晰性选择与 wheel 安全由不同机制解决。

### 10.2 构建后端

Contexture 是 pure Python 包，目标采用 `uv_build`，并显式声明唯一模块和空 module root：

```toml
[build-system]
requires = ["uv_build>=0.12.5,<0.13"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "contexture"
module-root = ""
```

实施时仍须在项目支持的 Python/uv 版本上验证该范围。官方文档要求为 `uv_build`
设置 minor 上界，明确支持 `module-root = ""` 的 flat layout，并且 wheel 只从显式
module root 收集唯一模块。CLI templates、`.pyi` 和 `py.typed` 都位于 `contexture/`
下，因此随模块一起进入 wheel。

### 10.3 发布前的事实来源是 wheel

从仓库根目录直接 `import contexture` 只能证明 checkout 可导入，不能证明用户安装物
正确。发布门禁必须执行：

1. `uv build --no-sources`，同时生成 sdist 和 wheel。
2. 检查 wheel manifest，只包含当前 `contexture/` 和 distribution metadata。
3. 在仓库目录之外创建隔离环境，只安装刚生成的 wheel。
4. 运行 `contexture --help`、`contexture --version` 和 Python import smoke test。
5. 在隔离临时目录运行 `contexture new`，对生成项目执行 sync/check/call。
6. 验证 `.pyi`、`py.typed` 和所有 scaffold template 在 wheel 中。
7. 从 sdist 再构建 wheel，并比较两个 wheel 的文件清单。

这样解决 stale `build/lib` 曾把已删除模块重新带入 wheel 的问题，而不依赖 `src/`
遮蔽当前目录。

## 11. 多语言设计

### 11.1 不把 Python 继承写进规范

跨语言权威契约由三部分组成：

```text
公共模型规范   Role / Skill / Tool 等字段和语义
行为规范       构建、校验、生命周期、并发、错误
协议一致性     ref、schema、disclosure、gateway 与 golden payload
```

各语言可以采用符合自身生态的声明方式：

| 语言 | 业务接口交付 | 内部边界 |
| --- | --- | --- |
| Python | 顶层 facade、`.pyi`、`py.typed`、基类 | 内部模块不作为规范导入 |
| TypeScript | package exports、`.d.ts`、abstract class/interface | exports map 阻止 deep import |
| Go | exported struct/interface、`go doc` | 编译器强制的 `internal/` |
| PHP | namespace、interface/abstract class、PHPDoc/PHPStan | Composer 映射和 Internal namespace |

不同语言不要求类图完全相同。例如 Python 可从 `invoke()` type hint 派生 schema，Go
可以从 typed input struct 构建，TypeScript 可以用显式 schema 与泛型关联。最终必须
一致的是到达 Index/MCP 的 schema 和行为。

### 11.2 conformance 方向

现有 `spec/golden/` 已固定 Python demo 的协议输出。未来增加：

```text
spec/
├── model.md              语言无关的公共概念与不变量
├── conformance.md        每个 binding 必须通过的行为
├── fixtures/             语言无关的声明输入
└── golden/               精确输出，继续保留
```

TypeScript、Go、PHP port 使用同一 fixture 建出同一森林，并逐字节或规范化后比较输出。
`.pyi`、`.d.ts`、Go interface 和 PHP contract 都是 binding，不互相生成，也不成为
权威真相源。

## 12. 配置和兼容策略

### 12.1 新旧配置

新配置：

```toml
[tool.contexture]
app = "hello_context:app"
```

旧配置：

```toml
[tool.contexture]
name = "hello-context"
roots = ["hello_context:Hello"]
channels = "hello_context.channels:HelloChannels"
publish = ["hello_context.publish:PUBLISHED"]
```

迁移规则：

1. 新脚手架、README 和所有示例只生成 `app`。
2. 一个过渡 release 同时接受两种形式。
3. 同一 table 同时写 `app` 和旧字段时直接报错，不猜优先级。
4. 旧形式输出一次明确 deprecation warning，并给出目标 `app` 示例。
5. 旧 loader 内部适配为临时 Application 声明，之后走同一个 build/serve 路径。
6. 删除旧形式必须单独发布迁移说明，不在本次实现中静默完成。

### 12.2 Python import 兼容

- 新增 `Contexture` 和 `serve`，不更换现有 Role/Skill/Tool 类型身份。
- 当前顶层已经导出的 `ControllerManager`、`ContextNode` 等不在本阶段突然删除。
- handbook 将它们归入 advanced/internals；是否收紧导出由后续兼容 ADR 决定。
- demo 改为声明一个 app，同时保留一个展示自定义 `main()` 的短入口。

## 13. Handbook 信息架构

README 只承担安装、五分钟成功和链接，不再先讲完整架构。完整用户 handbook 按实际
任务排序：

1. **Build Hello World** — 安装、生成、Role/Skill/Tool、check/call。
2. **Connect an Agent** — serve 和 Host 配置。
3. **Model a Real Domain** — child Role、uses、地址和 disclosure。
4. **Connect External Systems** — Channels 生命周期和测试替身。
5. **Publish Entrances** — Prompt、Resource 与已有节点的关系。
6. **Test and Debug** — check/list/inspect/call、错误定位和写操作保护。
7. **Deploy** — HTTP、认证、日志和配置。
8. **Embed** — 自定义 main、async 进程和低层 Server API。
9. **Internals** — Manager、Index、Disclosure、gateway 与 ADR 链接。

每章只引入完成该任务必须知道的新概念，并从上一章的同一项目继续演进。

## 14. 被拒绝的方案

### 14.1 恢复 `src/`

拒绝。当前问题是 wheel 收集和隔离验证失效，不是源码目录少了一层。通过显式
module root、可靠 backend 和 wheel-first 测试解决。

### 14.2 新建 `contexture.interfaces` 运行时包

拒绝。重新定义会制造第二套类型；wrapper 增加继承层；简单 re-export 会产生第三条
规范导入路径。顶层 facade 加 `.pyi` 已能提供接口视图。

### 14.3 让 Application 在 import 时构建 Server

拒绝。它会恢复历史 `ContextureApp` 的问题：导入产生节点和外部状态、测试共享实例、
声明与运行生命周期混合。

### 14.4 只写 handbook，不改变用户路径

拒绝。当前缺少 Application 组合根和本地 call/check，文档无法诚实地写出一条从安装
到执行的连续路径。

### 14.5 只支持 CLI，不支持自定义 main

拒绝。基础库必须能嵌入已有服务和测试进程；CLI 与手写入口共享 app 和 runner 即可，
不需要牺牲任一方式。

## 15. 完成标准

实现完成后，一个第一次接触 Contexture 的 Python 用户应该能够：

1. 用一条安装命令获得 `contexture` CLI。
2. 生成后立即看懂本地每个文件负责什么。
3. 在第一课同时写一个 Skill 和一个 Tool，并说清二者差异。
4. 不连接 Host 就完成 check、inspect 和真实 Tool 调用。
5. 只在一个 `app` 对象中增加 Role、Channels、Prompt 或 Resource。
6. 默认不写 main；需要时用同一个 app 写出短 main。
7. 在 IDE 中从 `contexture` 顶层获得完整类型、签名和用户契约。
8. 从干净 wheel 安装得到与源码 checkout 相同的 CLI、模板和行为。

框架维护者应该能够证明：

1. Application 没有改变 Manager/Index/Disclosure 的职责或 wire golden。
2. import application 不构图、不连接、不导入 MCP SDK。
3. CLI 和自定义 main 使用同一构建函数。
4. wheel 不包含任何未跟踪或已删除的历史源码。
5. Python 公共接口是语言无关模型的一种 binding，而不是未来 port 的实现模板。

## 16. 外部依据

- [uv build backend：backend 选择、版本范围、flat module root 与文件收集](https://docs.astral.sh/uv/concepts/build-backend/)
- [uv 构建和发布：`--no-sources` 与隔离安装验证](https://docs.astral.sh/uv/guides/package/)
