# Design 07 — 独立的 Runtime Application 与 Disclosure-only Application

**Status:** approved for implementation

**Date:** 2026-08-29

**Decision:** ADR 018

## 0. 交付结果

Contexture 提供两条明确、互不共享运行对象的编译路径：

```text
Runtime data/declaration                 Architecture data/declaration
          │                                          │
 compile_application                      compile_disclosure_application
          │                                          │
 bound Runtime Index                      unbound Disclosure Index
 refs · containment · uses              refs · containment · uses
 schemas · bindings · channels          no bindings · no channels
          │                                          │
 DisclosureAPI + ExecutionAPI                     DisclosureAPI
          │                                          │
 Runtime MCP Server                         Architecture MCP Server
 discover/open/invoke                     discover/open + selected prompts
 prompts/resources                         no resources, no execution
```

两个 Application 可以使用同一个 Contexture 包，但它们的数据、节点实例、Manager、Index、Telemetry、
Server、认证和生命周期完全独立。数据一致性通过显式来源与编译验证建立，不通过共享 Index 建立。

## 1. 当前基线与问题

当前只有一条路径：

```text
Contexture declaration
  → ControllerManager
  → Index.of(..., bind=TypeHintBinding)
  → Disclosure
  → SystemAPI
  → Tools + Prompts + Resources
```

`SystemAPI` 同时拥有导航和执行；`Surface.of` 固定安装三扇 Door；`ContextureServer` 固定从一个带 Binding
的 Index 建 Runtime Surface。这使纯披露应用无法表达：即便删除 MCP invoke，Index 中仍有 Binding，
Resource 仍可执行只读 Tool，Tool card 仍会要求 schema。

Architecture 与 Runtime 还有数据语义差异：

- Runtime 数据回答 capability 是否可调用、参数是什么、调用进入哪个 binding；
- Architecture 数据回答产品责任、owner、依赖、实现/provider 与部署事实怎样关联；
- Runtime 变更按业务发布发生，Architecture read model 可按事实编译和校准发生；
- Architecture 不能被当前 Runtime Role 树的错误抽象限制，也不能把源码目录当作数据源。

因此本设计不建立“一个 Index 的两个 projection”，而建立“同一框架的两个 Application kind”。

## 2. 不变量

1. `Role`、`Skill`、`Tool` 仍是闭合的 ContextNode 集合；不新增 Element 或 ArchitectureNode。
2. `CompileLevel` 仍只有 ROUTE/ACTIVE；两种 Application 都使用同一套渐进披露算法。
3. Runtime 与 Architecture 不共享 ContextNode instance、Manager、Index、Binding、Channels、Telemetry 或
   Server lifecycle。
4. Runtime 默认行为 byte-for-byte 兼容：四个 gateway、payload、Prompt、Resource 和错误文本不变。
5. Disclosure-only Index 不创建 Tool Binding，不接受 Channels，不可传给 `ApplicationRuntime`。
6. Architecture Tool card 只表达结构事实，不含 `input_schema`；Architecture MCP 没有 invoke 或 Resource。
7. Prompt、completion、instructions、signpost 和 open 只读取所属 Application 的 Index。
8. Architecture 数据是可重建 read model；每个事实必须来自应用仓库声明的 authority/source，禁止目录扫描
   和手写第二份运行状态。
9. Contexture 只定义结构和服务机制，不定义业务权限、OC provider vocabulary 或 Architecture 内容。
10. 两个 MCP 的认证、endpoint 和进程可以独立升级、失败和回滚。

## 3. 两种编译产物

### 3.1 现有 Runtime 编译保持默认

```python
runtime_app = Contexture(
    name="business-runtime",
    roots=(GoalRole, ProjectRole),
    channels=ApplicationServices,
    prompts=(DoPrompt,),
    resources=(GoalsResource,),
)

compiled = compile_application(runtime_app)
server = compiled.server()
```

它继续产生带 TypeHintBinding 的 Index、ApplicationRuntime、Runtime Disclosure、RuntimeSurface，以及
四个 gateway、Prompt 和 Resource。

### 3.2 新增 disclosure-only 编译

```python
architecture_app = Contexture(
    name="system-architecture",
    roots=(ArchitectureRole,),
    prompts=(BrainPrompt,),
)

compiled = compile_disclosure_application(architecture_app)
server = compiled.server()
```

约束：

- `channels` 必须为空；传入即构造期失败；
- `resources` 必须为空；传入即构造期失败；
- Index 编译不调用 Binding factory，也不派生 Tool schema；
- 编译产物没有 `.runtime()`；
- Server 只安装 discover/open 与已声明 Prompt；
- Tool 可以作为 Role 成员或 uses 目标存在，但仅编译结构 card。

建议新增独立产物类型，避免 capability 只由布尔值区分：

```python
CompiledApplication                 # 现有 Runtime，保持名字与兼容
CompiledDisclosureApplication       # 新增，只披露
```

`compile_application` 不增加 `mode=` 参数；调用哪个 compiler 就决定产物能做什么，使非法组合在类型和构造
阶段失败。

## 4. Index 的 bound/unbound 边界

第一版采用兼容性较高的方案：保留 `Index` 类型，允许编译出显式 unbound Index，但不让“有没有 binding”
成为运行时猜测。

建议公共构造由两个具名 factory 表达：

```python
Index.bound(manager, bind=TypeHintBinding)
Index.unbound(manager)
```

兼容的 `Index.of(..., bind=...)` 暂时代理到 `bound`。unbound Index：

- 持有 roots、ref、parent/children、uses/dependents 和 kind 索引；
- binding table 永远为空且状态明确为 unbound；
- `binding_of/schema_of` 统一抛构造/编程错误，不返回空 schema 假装可调用；
- `ApplicationRuntime` 构造时要求 bound Index，收到 unbound Index 立即失败；
- 不 provision Channels。

如果实现证明 optional binding 让 Index 分支过多，再以独立 ADR 抽出 `BindingTable`；本轮不先做
`StructuralIndex/RuntimeIndex` 类型重写，以控制兼容面。

## 5. 核心 API

### 5.1 DisclosureAPI

```python
@dataclass(frozen=True, slots=True)
class DisclosureAPI:
    tree: Disclosure
    reserved: frozenset[str] = frozenset()
    telemetry: Telemetry = ...

    async def discover(self) -> CompiledContext: ...
    async def open(self, ref: str) -> CompiledContext: ...
    async def open_for_person(self, ref: str) -> CompiledContext: ...
```

它负责渐进披露、person/model door、lookup refusal 和披露 telemetry，不导入或构造
`ApplicationRuntime`。`open_for_person` 取代当前 `open_for_a_person`；旧名在一个 minor release 内代理。

### 5.2 ExecutionAPI

```python
@dataclass(frozen=True, slots=True)
class ExecutionAPI:
    runtime: ApplicationRuntime

    async def invoke_read_only(
        self, ref: str, arguments: dict | None = None, *, context=None, principal=None
    ) -> Any: ...

    async def invoke(
        self, ref: str, arguments: dict | None = None, *, context=None, principal=None
    ) -> Any: ...

    async def read_for_host(self, ref: str, *, principal=None) -> Any: ...
```

它负责 runtime 调用、wrong-door/lookup refusal；不负责 discover/open 或 Prompt reserved。

### 5.3 SystemAPI 兼容策略

`SystemAPI` 在 0.9 兼容窗口内保留为 Runtime facade：

```text
SystemAPI
  disclosure: DisclosureAPI
  execution: ExecutionAPI
```

现有方法继续工作，Contexture 新 Surface 不再依赖它。删除必须等待 1.0 或独立 breaking ADR。

## 6. Disclosure 与 Tool card

两种 Application 使用同一个 `Disclosure` 算法，但 View 知道自己面对 bound 还是 unbound Index。
为避免 Tool 自己假设“一旦看见就一定可调用”，把执行 facet 交给 View：

```python
class View(Protocol):
    def ref_of(self, node: ContextNode) -> str: ...
    def card_of(self, node: ContextNode) -> CompiledContext: ...
    def cards_of(self, nodes: Iterable[ContextNode]) -> CompiledContext: ...
    def cards_for(self, refs: Iterable[str]) -> list[CompiledContext]: ...
    def execution_of(self, tool: Tool) -> CompiledContext: ...
```

调整：

- `Role._compile_active` 使用 `view.cards_of(self.members())`；
- Role/Skill/Tool 的 uses 使用 `view.cards_for(self.uses)`；
- `Disclosure.skeleton` 使用同一个 cards_of；
- `Tool.card` 合并 `view.execution_of(tool)`；
- bound Runtime Disclosure 返回 `read_only + input_schema`；
- unbound Disclosure 返回空执行 facet。

Architecture 因而仍能表达“这是一个 Tool，它属于哪个 Role、描述什么能力、依赖什么”，但 Tool 不带调用
合同。若 Architecture 数据根本不需要某个 Runtime Tool，它不必出现在 Architecture declaration 中；
两边没有逐节点镜像要求。

## 7. Surface 与 MCP Door

### 7.1 RuntimeSurface

```text
DisclosureAPI(bound Disclosure)
ExecutionAPI(ApplicationRuntime)
RuntimeTools: discover/open/invoke_read_only/invoke
RuntimePrompts
RuntimeResources
```

`Surface` 在兼容期可作为 RuntimeSurface alias；默认 `CompiledApplication.server()` 不变。

### 7.2 DisclosureSurface

通用框架名称使用 `DisclosureSurface`，不把 One Creator 的业务词 Architecture 写死进底层：

```text
DisclosureAPI(unbound Disclosure)
NavigationTools: discover/open
Selected Prompts
no ExecutionAPI
no Resources
```

其 MCP `tools/list` 仍包含 discover/open，因为动态渐进导航需要参数化 query；它们是框架查询入口，不是
业务 Tool，且都带 `readOnlyHint=true`。构造 `DisclosureSurface` 时传入 Resource 必须失败，不能静默忽略。
Prompt 的 `opens`、goto completion、signpost 和 server instructions 均只使用所属独立 Index。

Gateway 常量拆为：

```text
NAVIGATION_GATEWAY = discover, open
EXECUTION_GATEWAY  = invoke_read_only, invoke
RUNTIME_GATEWAY    = NAVIGATION_GATEWAY + EXECUTION_GATEWAY
```

旧 `GATEWAY/GATEWAY_TOOLS` 继续等于 Runtime gateway。

## 8. Prompt、Resource 与旁路

Prompt 和 MCP tools 是不同 protocol primitive，但在内核共享 DisclosureAPI：

- Prompt Door 只接收 DisclosureAPI；
- Prompt declaration 必须解析到所属 Index；
- `goto` completion、signpost、instructions 不读取另一个 Application 或全局 registry；
- Runtime Prompt 读取 Runtime Index；Architecture/Brain Prompt 读取 Architecture Index；
- Prompt 本身不执行 Tool，可以存在于 DisclosureSurface。

Resource 是无参数 read-only Tool 的第二地址，因此 Resource Door 只接收 ExecutionAPI 和 bound Index；
CompiledDisclosureApplication 不接受 resources，DisclosureSurface 没有 resources/list handler 或间接调用路径。

## 9. Server、生命周期与进程

两个编译产物各自建立 `ContextureServer`，不共享 lifecycle：

```text
Runtime process
  Runtime Index + Channels + Runtime MCP SessionManager

Architecture process
  Architecture Index + no Channels + Architecture MCP SessionManager
```

Contexture 只要求不同 Server；部署可以放在同一 Python 进程，但生产推荐独立进程。独立进程让 Runtime
故障不影响 Architecture、Architecture 构建失败不阻止业务启动，并允许两套 token/audience、监听地址和
资源限制独立配置。本轮不再需要共享 Index 的双 SessionManager 或 Channels 单次 provision 设计。

## 10. 公共 API

Runtime 用户保持：

```python
compiled = compile_application(runtime_app)
compiled.server()
compiled.runtime()
```

Disclosure-only 用户新增：

```python
compiled = compile_disclosure_application(architecture_app)
compiled.server()
compiled.disclosure
```

`CompiledDisclosureApplication` 不暴露 `.runtime()`。Contexture 不提供把 Runtime Index 克隆成 Architecture
Index 的 helper，因为那会重新引入错误耦合。

## 11. 代码落点

```text
contexture/core/model/
  index.py                  bound/unbound named factories
  disclosure.py             one progressive algorithm
  disclosure_api.py         DisclosureAPI
  execution_api.py          ExecutionAPI
  system_api.py             Runtime compatibility facade + refusal wording
  node.py                    expanded View contract
  role.py / skill.py / tool.py

contexture/server/surface/
  __init__.py                common protocol / compatibility export
  runtime.py                 RuntimeSurface
  disclosure.py              DisclosureSurface
  tools.py                   RuntimeTools
  navigation.py              discover/open Door
  prompts.py                 DisclosureAPI-only
  resources.py               ExecutionAPI-only

contexture/server/
  application.py             two compiler/result types
  server.py                  accepts prepared Surface
  instructions.py            reads current Disclosure only
```

文件名可微调，但 layering test 必须证明 disclosure-only 路径不 import runtime、binding、resources 或
Channels provisioning。

## 12. 执行阶段

### C0 — 固定 Runtime 基线

- 保存 tools/list、discover/open、Prompt、Resource、stdio/http golden；
- 建含 Role/Skill/Tool/uses/Prompt/Resource/Channels 的统一 fixture；
- 清点外部 `SystemAPI`、`Surface`、`GATEWAY`、`Index.of` 导入。

退出门：只增测试，现有 suite 全绿。

### C1 — API 拆分，不改变 Runtime

- 新增 DisclosureAPI、ExecutionAPI；
- SystemAPI 改为组合 facade；
- 迁错误翻译和 telemetry 测试；
- Runtime Surface 仍输出完全相同 wire。

退出门：Runtime golden 逐字节不变。

### C2 — Unbound Index 与 disclosure-only compiler

- 增加 `Index.bound/Index.unbound`；
- 增加 `CompiledDisclosureApplication` 与 `compile_disclosure_application`；
- unbound 拒绝 Channels、Resources、binding/schema/runtime；
- 增加 bound/unbound 类型与失败测试。

退出门：一个含 Tool 的 disclosure-only app 可编译，但无法构造 ApplicationRuntime 或取得 binding。

### C3 — View 执行 facet

- 增加 cards_of/cards_for/execution_of；
- 迁 Role/Skill/Tool/Disclosure；
- 先证明 bound Runtime payload 等价，再验证 unbound Tool card 无 schema；
- Prompt completion、signpost、instructions 收口到所属 Disclosure。

退出门：Runtime 等价；Architecture payload 结构可读、不可调用。

### C4 — DisclosureSurface 与 Server

- 拆 navigation/execution gateway；
- 增加 DisclosureSurface；
- Prompt 只依赖 DisclosureAPI，Resource 只依赖 ExecutionAPI；
- Server 接受构造好的 Surface；默认仍为 RuntimeSurface；
- 更新 facade、typing、README、handbook、MVC 和 inspection。

退出门：Disclosure Surface tools/list 只有 discover/open，resources/list 为空，invoke MCP method 不存在。

### C5 — 发布

- 版本提升到 0.9.0；
- wheel/sdist 和干净 venv 验证；
- 发布 Contexture commit/tag；
- 提供兼容窗口和下游迁移说明。

退出门：One Creator 仅升级依赖、不启用 Architecture 时 Runtime 全绿。

## 13. 验证矩阵

| 维度 | Runtime Application | Disclosure-only Application |
|---|---|---|
| 数据/节点/Index | 独立，bound | 独立，unbound |
| Channels | 可有，正常 provision | 构造期拒绝 |
| tools/list | 现有四个 | discover/open |
| Tool card | read_only + schema | 结构 card，无 schema |
| Prompt | 当前 commands/goto | 选定 Architecture/Brain Prompt |
| Resource | 当前行为 | 构造期拒绝、list 为空 |
| invoke | 当前行为 | 无 method、无内部对象 |
| direct ref | 只解析 Runtime Index | 只解析 Architecture Index |
| lifecycle/telemetry | 独立 | 独立 |

必跑现有套件：

```text
tests/test_disclosure.py
tests/test_system_api.py
tests/test_runtime.py
tests/test_surface.py
tests/test_server.py
tests/test_stdio_server.py
tests/test_http_server.py
tests/test_application.py
tests/test_telemetry.py
tests/test_layering.py
tests/test_golden.py
```

新增 unbound Index、disclosure-only compiler、no-binding、no-resource、structural Tool card、独立 Index、
Prompt ownership、gateway surface 和 compatibility facade 测试。

## 14. 发布与回滚

- C1–C4 每阶段一个可回滚提交，先保 Runtime 等价再加新路径；
- 0.9.0 保留 0.8 默认入口与导出；
- Disclosure-only 是新增能力，无业务数据迁移；
- 下游 Architecture 失败时停止其独立进程即可，Runtime 不回退、不重启；
- 任何 Runtime golden 漂移、unbound Index 可取 binding、DisclosureSurface 出现 Resource/invoke 都阻止发布。

## 15. 非目标

- 不设计 One Creator 的 Architecture 数据内容或 Brain 方法；
- 不共享、克隆或在线读取 Runtime Index；
- 不从源码/部署目录扫描生成 Architecture Role；
- 不加入通用权限 DSL、per-node surface flag 或新 ContextNode 类型；
- 不在本轮加入 reverse relation query、分页图查询或自动 ADR 写入；
- 不声称 Architecture Agent 没有宿主原生 shell/filesystem 权限，该边界由部署 profile 保证。

