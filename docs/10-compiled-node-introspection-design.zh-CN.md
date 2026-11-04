# 设计 10 — 编译节点内省与 Node Catalog

**状态：** 已接受；Python v1 与 One Creator 集成已实现

**日期：** 2026-11-04

**权威文本：** [English design](10-compiled-node-introspection-design.md)

**读者：** 框架维护者、语言 binding 作者、应用集成者、开发工具作者

## 0. 裁决

Contexture 提供一份面向已编译 Application 的、带版本且只读的内省合同，名称为
**Node Catalog**。程序消费者通过同一套稳定接口枚举和描述 Role、Skill、Tool，不再需要重放渐进披露，
也不需要直接读取 Python 实现对象。

Node Catalog 是图级投影，不是 `Role.to_dict()`，也不是新的 disclosure level：

```text
ContextNode        单个声明自身固有的事实
Index              ref、父子、包含、依赖、binding
Disclosure         surface 可见性、presentation、active payload
CompiledApplication       Application 类型和 publication
        │
        └── NodeCatalog   稳定、只读的集成合同
                ├── 进程内查询 API
                ├── 确定性 JSON snapshot
                ├── 可选 HTTP/debug service
                └── 面向人的开发者 Inspector
```

框架不规定声明数据存在哪里，也不让已编译节点可编辑。Python constructor、YAML、Markdown、生成 class
或数据库都可以继续作为 Application 自己拥有的声明来源。编辑和发布仍是应用责任。

## 1. 问题

Contexture 已经拥有解释一个已编译 Application 所需的全部事实，但没有一个稳定集成合同把它们组装起来：

- `ContextNode` 知道 kind、name、路由 description、自有字段和 `uses`；
- `Index` 知道 canonical ref、parent、children、reverse dependency 和 Tool binding；
- `Disclosure` 知道 model visibility、本地化 presentation 和准确 active payload；
- compiled application 知道 execution 是否 bound、model/Prompt roots 和 publications；
- `contexture.inspection` 能回放 agent 看到的内容，但它有意表达披露轨迹，而不是完整图谱。

缺少公共 Catalog 时，任何需要人类架构视图、能力搜索、CI graph diff 或开发者 Inspector 的应用都必须
自行遍历上述对象。各项目 exporter 会对 process member、visibility、本地化文案、instructions、Tool
schema、顺序和 digest 作出不同选择，最终形成可能偏离真实服务图的平行合同。

## 2. 目标

设计必须：

1. 用一套 typed API 暴露所有已编译 Role、Skill、Tool；
2. 保持 canonical ref 和声明顺序；
3. 区分应用原始 instruction 与框架组合后的 active instruction；
4. 只在存在 binding 时暴露 Tool execution metadata；
5. 分别表达 containment 和 `uses`；
6. 支持完整 Application、model-visible、selected-surface 三种视图；
7. 与 MCP server 使用同一 locale/presentation 解析规则；
8. 生成带跨语言 digest 的确定性 JSON；
9. 同时支持 runtime 和 disclosure-only Application；
10. 允许应用附加带命名空间且 JSON-safe 的扩展信息，不改变 Contexture 闭合的 node kind；
11. 让 CLI、CI、HTTP、developer UI 共用同一个投影；
12. 不增加 business Tool、Channels lifecycle、invoke 或 model context 成本。

## 3. 非目标

Node Catalog 不：

- 定义 authoring 文件格式；
- 改写 Python、YAML、Markdown 或数据库；
- 修改已编译 `Index`；
- 取代 `contexture_discover`、`contexture_inspect`、`contexture_open`；
- 增加第六个 model-controlled gateway Tool；
- 暴露 Tool handler 源码、Channels、凭据或调用结果；
- 充当权限或 capability grant；
- 把独立 Runtime/Architecture Application 合成一棵图；
- 定义 owner、provider、deployment 等应用词汇；
- 声称图结构合法就能保证模型行为正确。

## 4. 为什么完整合同不属于 `ContextNode`

内省是底层通用能力，但完整节点 meta 不是 node-local 能力。

节点只能诚实地报告自身固有事实。注册前它不知道 canonical ref、parent、sibling order、reverse
dependents、model/Prompt plane、SurfaceSelection、locale presentation、Tool binding 或 binding schema。

若把 `meta()` 或 `to_dict()` 放到 `Role` 上，要么只能得到残缺对象，要么迫使节点反向依赖
`Index`、`Disclosure` 和 server 层，都会破坏现有声明/编译/披露边界。

具体 node class 可以有小型内部贡献方法，提供 kind-specific intrinsic facts，避免 assembler 中出现大段
type switch；但公共且权威的 descriptor 必须由 `NodeCatalog` 针对一份已编译图统一组装。

## 5. 核心词汇

### 5.1 NodeDescriptor

`NodeDescriptor` 是一个节点在一个 catalog view 中的稳定完整描述。它不是 routing card，也不是 active
payload。

### 5.2 CatalogView

- `application`：Compiled Index 内的全部节点，包括 Prompt roots；
- `model`：request selection 之前，model plane 可见的节点；
- `surface`：应用显式 SurfaceSelection 后可见的节点。

调用者必须明确选择。开发工具通常选择 `application`；声称展示“agent 可导航内容”的产品 UI 通常选择
`model` 或 `surface`。

### 5.3 Detail level

- `summary`：identity、routing copy、containment、dependency、presentation；
- `definition`：在 summary 之上增加 Role/Skill instructions、process relation 和 Tool execution
  contract。

Snapshot 必须记录自己的 detail。消费者不得把 definition 字段缺失解释成空值。

### 5.4 NodeCatalogSnapshot

确定性可序列化值，包含 Application manifest、roots、counts 和扁平有序 descriptor 列表，可存成 build
artifact，也可在 CI 中比较。

## 6. Python 进程内 API

```python
from contexture.introspection import CatalogDetail, CatalogView, NodeCatalog

catalog = NodeCatalog.of(
    compiled,
    view=CatalogView.MODEL,
    detail=CatalogDetail.DEFINITION,
    locale="zh-CN",
)

roots = catalog.roots
architect = catalog.get("team/architect")
children = catalog.children("team")
matches = catalog.list(kind="skill", query="architecture")
snapshot = catalog.snapshot()
wire_json = snapshot.to_json()
```

两种 compiled Application 都提供 convenience method：

```python
catalog = compiled.catalog(
    view="model", detail="definition", locale="zh-CN"
)
```

API 同步、无副作用。构建 Catalog 不得打开 Channels、进入 application lifecycle、运行 Tool、读取
Resource 或修改 Telemetry。

## 7. 标准 JSON 合同

第一版 wire form 是 `contexture.node-catalog/v1`：

```json
{
  "schema": "contexture.node-catalog/v1",
  "content_digest": "sha256:...",
  "application": {
    "name": "one-creator",
    "mode": "runtime",
    "view": "model",
    "detail": "definition",
    "locale": "zh-CN",
    "execution_bound": true
  },
  "roots": ["project", "work", "team"],
  "counts": {"role": 80, "skill": 63, "tool": 368},
  "nodes": []
}
```

`nodes` 使用扁平形式，不使用递归树。canonical ref 已经编码 containment，扁平结构能做到每个节点只出现
一次、直接表达 `uses`、支持查询/分页/搜索/图渲染、产生局部 diff、避开 recursion limit，并通过
`parent_ref` 和有序 `members` 恢复树。

数组顺序使用 `Index.walk()` 的声明顺序，对象 key 顺序无语义。

## 8. Node descriptor

公共字段：

| 字段 | 含义 |
| --- | --- |
| `ref` | compiled Index 给出的 canonical openable ref。 |
| `kind` | 闭合值：`role`、`skill`、`tool`。 |
| `name` | 不受 presentation 影响的机器名称。 |
| `description` | 原始路由 description。 |
| `presentation` | 已解析的人类 title 和可选本地化路由 description。 |
| `plane` | `model` 或 `prompt`。 |
| `parent_ref` | containment parent，root 为 `null`。 |
| `ordinal` | 在 parent 完整 member list 或 root list 中的零基位置。 |
| `members` | 直接成员，携带 ref、kind、slot。 |
| `uses` | 按声明顺序排列的依赖 ref。 |
| `dependents` | 按编译顺序排列的反向依赖 ref。 |
| `extensions` | 带命名空间的应用扩展；默认空。 |

`members` 示例：

```json
{"ref": "team/architect/review", "kind": "skill", "slot": "skill"}
```

Role slot 为 `pre_process`、`child`、`post_process`、`skill`、`tool`，保留裸 child-ref 列表会丢失的
process 语义。

### 8.1 Role definition

```json
{
  "role": {
    "authored_instructions": "Review boundaries against declared evidence.",
    "effective_instructions": "<framework process contract plus authored text>",
    "pre_process_ref": null,
    "post_process_ref": null
  }
}
```

`authored_instructions` 是应用拥有的 Role 字段；`effective_instructions` 是与
`contexture_open` 同编译路径产生的 active instruction。process composition 会让二者不同，不能混为一谈。

### 8.2 Skill definition

Skill 同样保留 `authored_instructions` 与 `effective_instructions`。目前通常相等，但字段语义不应因未来
框架组合逻辑而改变。

### 8.3 Tool definition

Runtime Application：

```json
{
  "tool": {
    "execution_available": true,
    "read_only": true,
    "input_schema": {"type": "object", "properties": {}}
  }
}
```

Disclosure-only Application：

```json
{"tool": {"execution_available": false}}
```

不存在 binding 时省略 `read_only` 和 `input_schema`，不得伪造。Catalog 永远不序列化 callable、handler
源码、Channels、凭据或调用结果。

## 9. 声明、Presentation 与最终披露

Catalog 分开回答：

1. 应用写了什么：name、description、authored instructions、containment、`uses`；
2. 编译派生了什么：ref、parent、dependents、process slot、binding、schema；
3. 这个 surface 展示什么：本地化 title/description、visibility、effective instructions。

`surface` view 必须复用 server 的 selection resolution 和单调收窄规则，不得重新实现 wildcard、ancestor
promotion 或 visibility。

## 10. 应用扩展信息

Contexture 标准字段保持闭合。owner、source authority、deployment classification、Team runtime label 等
是应用 read-model 事实，不进入框架词汇。

Catalog 接受显式 contributor：

```python
class NodeMetadataContributor(Protocol):
    namespace: str

    def describe(
        self, ref: str, node: ContextNode, graph: CompiledGraph
    ) -> JsonObject | None: ...
```

```python
catalog = NodeCatalog.of(
    compiled,
    contributors=(ArchitectureSources(), TeamLabels()),
)
```

输出：

```json
{
  "extensions": {
    "one-creator/source": {
      "owner": "account",
      "classification": "authority",
      "source": "oc_app/modules/account"
    }
  }
}
```

Namespace 必须带 owner prefix 和 `/`；重复 namespace 拒绝；值必须为 JSON object；contributor 必须纯函数
且拿不到 Channels/invocation API；输出进入 digest；framework disclosure 不自动把扩展发送给 model；HTTP
Host 决定允许暴露哪些 namespace。

这比给 `ContextNode` 增加任意 `metadata` map 更合适：节点模型继续可移植，业务词汇不必进入所有声明
constructor，独立 Architecture Application 也可按 ref 附加 authority facts。

## 11. 确定性、Digest 与版本

v1 canonicalization：UTF-8 JSON；object key 按 Unicode code point 递归排序；array order 保持；不包含
timestamp/object id/memory address/自动探测路径；整数值 float 规范化为 integer；digest 输入使用 compact
separators；对除 `content_digest` 外的完整 document 做 SHA-256；输出小写 `sha256:<hex>`。

标准 envelope 字段增删或语义变化必须升级 schema。应用 extension 增加不要求升级框架 schema。语言中立 spec
拥有 JSON Schema、fixtures、digest vector 和语义规则；Python dataclass 只是实现。

## 12. CLI

```bash
contexture catalog --target package.module:app --view model --detail summary
contexture catalog --target package.module:app --view application \
  --detail definition --json
contexture catalog team/architect --detail definition --json
```

`contexture inspect` 回答“agent 每一步会收到什么、成本多少”；`contexture catalog` 回答“Application
编译出了哪些节点和关系”。二者都不调用 business Tool，`catalog` 不提供 `--read`。

## 13. 开发者服务

Python binding 已提供独立开发者服务：

```bash
contexture debug --target package.module:app \
  --view application --host 127.0.0.1 --port 8787
```

```text
GET /                         内置 Inspector
GET /app.css
GET /app.js
GET /healthz
GET /api/catalog
GET /api/nodes/{url-encoded-ref}
HEAD /api/catalog
```

Catalog 响应使用 digest 作为 `ETag` 并处理 `If-None-Match`。内置 UI 展示完整 containment forest，搜索
完整 descriptor，并显示定义、process member、依赖、extension 与 Tool schema；没有修改或调用路由。

Developer service 不挂到 Application MCP endpoint，也不改变固定 gateway。内置 adapter 只允许 loopback，
校验 Host/Origin，并发送严格 CSP 和浏览器安全 header。远端 Catalog 必须由应用另建带认证的 adapter；不能
把 debug server 切换成公网模式。

## 14. 安全边界

Instructions 和 Tool schema 即使没有凭据，也可能包含内部运维细节。因此进程内 API 是 trusted
application code，不是远程授权边界；JSON exporter 只写显式目标；debug server 默认 loopback、只读；
不得意外远程暴露完整 `application` view；production Host 需要独立鉴权；extension 不得带 secret；Tool
callable、Channels、环境和 invocation result 永不进入 Catalog；Catalog 读取不增加业务 telemetry。

## 15. 延后实现的 Diagnostics

Diagnostics 是派生观察，不改变 Catalog 合法性。本次 v1 和首版 debug adapter 有意不包含它。未来独立
diagnostic report 可包括 routing description 预算、routing text 重复成员、本地化 fallback、process member
导致 effective instruction 改变、extension 非 JSON、view 排除 `uses` target、Prompt root/selection
导致视图差异。

Diagnostic 使用稳定 code、node ref、severity、message，默认不进入 catalog digest，新增 advisory 不应被
解释为图本身变化。

## 16. 与现有能力的关系

| 现有能力 | 目的 | 与 Node Catalog 的关系 |
| --- | --- | --- |
| `Index` | 冻结编译图事实 | Catalog 的结构事实来源，继续保持低层。 |
| `Disclosure` | 渐进 model context | 提供 visibility、presentation、active payload。 |
| `contexture_inspect` | 非激活候选比较 | 不变，不演进为 bulk catalog。 |
| `contexture.inspection` | 回放 agent disclosure 与成本 | 被 debug trace 复用，不被取代。 |
| disclosure-only Application | 独立业务/架构图 | 有自己的 Catalog，不是 Runtime debug view。 |
| Node Catalog | 稳定的人/系统集成 read model | 新增图级投影。 |

## 17. 失败语义

以下情况 Catalog 原子失败：请求 ref 不属于 view；contributor namespace 非法或重复；扩展非 JSON；
presentation/selection 不能通过现有 resolver 解析；bound Tool 没有有效 schema；出现规范 JSON 之外的值。

API 不静默丢弃 node、schema、extension 或 relation。未知 ref 复用 Index 的结构化 lookup facts，但抛出面向
integration 的错误，不渲染 agent recovery sentence。

## 18. Conformance 与测试

规范至少包含 JSON Schema；runtime/disclosure-only fixtures；Prompt-root fixture；localized presentation；
selected surface；展示 authored/effective 差异的 process fixture；extension contributor fixture；canonical JSON
和 digest golden。

Python 必须证明每个 `Index.walk()` 节点只出现一次；ref/parent/ordinal/slot/uses/dependents 精确；
model/surface filter 复用已有行为；bound Tool schema 与 active card JSON 等价；unbound Tool 不伪造 execution；
不打开 Channels、不调用 Tool；重复 build byte-identical；summary 缺失字段不等于空定义；contributor 原子失败；
CLI JSON 无终端说明；既有 discover/inspect/open golden 不变。

协调语言 binding 必须通过同一 fixture/digest vector 后才能声明 product parity。

## 19. 被拒绝的方案

- **给每个 Node 加 `to_dict()`：** node 不知道图位置、reverse dependency、selection、presentation、schema。
- **把递归 roots 当标准合同：** graph relation 重复、深层 diff 噪声大、查询和依赖 overlay 困难。
- **增加完整树 MCP Tool：** 绕过渐进披露，增加 model context，混淆开发者集成和 agent 导航。
- **直接把 `inspect --all --json` 当 Catalog：** trace 表达 agent 步骤和成本，字段有意不完整且会重复上下文。
- **强制文本与代码分离：** authoring storage 与 compiled introspection 正交。
- **在 `ContextNode` 放任意 metadata map：** 业务词汇渗入所有声明并削弱跨语言类型；显式 contributor 更合适。

## 20. 交付记录

Python／One Creator slice 已完成：中立 schema、fixture selector、canonical golden 与 digest；不可变
descriptor/snapshot、图语义校验及 runtime/disclosure-only assembler；两类 compiled Application 的
`.catalog()`；`contexture catalog`、一致性测试与 typing；One Creator exporter 与 source contributor 迁移；
loopback-only HTTP service 和内置 Inspector。

合同本身语言中立，但 TypeScript 与 Go binding 的实现仍是独立 opt-in extension。现有 MCP release binding
门禁继续通过，但目前不声称这两个 binding 已实现 Node Catalog parity。

