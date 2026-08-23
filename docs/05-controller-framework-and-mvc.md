# Design 05 — Contexture 是 Controller Framework

**状态：** 当前架构说明

**日期：** 2026-08-22

**适用范围：** Contexture 在 MVC 中的位置、`ControllerManager` 的命名、
`ControllerManager → Index → Disclosure` 与 `Role / Skill / Tool` 的关系，以及业务
应用应把自己的 Model 放在哪里。

> 本文使用 MVC 建立宏观心智模型，不要求 Contexture 与某个 Web MVC 框架拥有相同的
> 类层次或请求生命周期。具体内核职责仍由 ADR 012、014、016、017 约束。

ADR 012 曾在讨论“声明如何成为能力”时，把 declaration、disclosure 和新引入的
`ControllerManager` 局部类比为 MVC 的 M、V、C。那个类比描述的是 Contexture
内核三个协作对象，不是完整应用跨 Host、Contexture 与业务领域的 MVC 边界。本文
采用后一个系统尺度：declaration 描述的是 Controller，disclosure 生成的是给 View
消费的协议表示；它们仍都位于 Controller 层。ADR 016 的
`ControllerManager / Index / Disclosure` 职责划分保持不变。

## 1. 一句话结论

```text
View         外部 Agent Host / Chat UI / IDE
Controller   Contexture 整体，包括 Role / Skill / Tool 及其组织、编译和路由
Model        业务应用自己的领域对象、业务规则和持久化
```

Contexture 是一个没有自带最终 UI、也不拥有业务状态的 **Controller Framework**。
它让业务开发者声明 Controller，负责把它们构造、编译、寻址，并通过不同 Host
Surface 暴露；Agent 走 MCP 渐进披露，人的看板走显式 REST 路由。真正的 View 在连接
它的 Host 中，真正的 Model 在使用它的业务项目中。

## 2. 容易发生的错误映射

只看返回值的形状，很容易得到下面的映射：

| 错误映射 | 为什么看起来合理 | 为什么不成立 |
| --- | --- | --- |
| `Disclosure` = View | 它产出 agent 会读到的 payload | 它决定 ref 如何解析、打开哪一层、给卡片还是全文；掌握的是路由和控制权 |
| `Tool` = 全部 Controller | 它接收参数并执行代码 | Role 控制责任分支，Skill 控制模型执行方法，三者共同组成 Controller 层 |
| JSON/MCP payload = 应用 View | 它是一次响应的表示 | 最终选择、排列和呈现这些内容的是外部 Host；payload 是 Controller 与 View 之间的协议表示 |

架构归属应按一个对象**决定什么**来判断，而不是按它最终是否输出 JSON 判断。
`Disclosure` 决定如何抵达并打开 Controller，因此是 Controller 层的 router/disclosure
policy，不是最终 View。

## 3. 修正后的 MVC 映射

| Contexture / 业务应用 | MVC 位置 | 责任 |
| --- | --- | --- |
| Claude Code、Codex、Chat UI、IDE | View | 接收用户意图，呈现能力和结果，决定交互体验 |
| MCP Surface / gateway | Agent 输入适配器 | 把协议请求送入固定的 discover/open/invoke 门 |
| REST Surface / Route | Human View 输入适配器 | 把显式 HTTP 路由送入同一个 Tool invocation runtime |
| `Contexture` Application | Controller composition root | 声明应用由哪些根 Controller、Channels 和 host-facing 入口组成 |
| `ControllerManager` | Controller registry + lifecycle | 构造并持有 Controller，供给 Channels，管理生命周期 |
| `Index` | compiled router table | 固化 ref、父子关系、Tool schema 与调用 binding |
| `Disclosure` | Controller router + disclosure policy | 按 ref 路由；决定一次给一层、卡片还是全文 |
| `Role` | composite Controller / controller module | 划分责任并组织下一层 Controller |
| `Skill` | model-executed Controller | 控制模型完成一类工作的步骤和可使用能力 |
| `Tool` | framework-executed Controller action | 接收校验后的参数，执行确定性业务用例 |
| 领域对象与领域服务 | Model | 表达业务状态和单值业务规则 |
| Repository / 数据库 | Model 的持久化边界 | 查询、事务、跨记录规则与存储 |

这也解释了 `ControllerManager` 的名字：它管理的是 `Role / Skill / Tool` 这些
Controller，不是数据库模型，也不是一个“替 Model 做决定的 Controller”。

## 4. Controller 层内部不是一件东西

把整个 Controller 层继续放大，运行前经历一次编译：

```text
Contexture Application
        │ names factories
        ▼
ControllerManager
  构造 · 持有 · 供给 Channels · 生命周期
        │ compile once
        ▼
Index
  ref · 父子关系 · schema · binding · 全森林校验
        │ read-only view
        ▼
Disclosure
  本次走到哪里 · 给多少 · 卡片还是全文
        │
        ▼
Role / Skill / Tool
  业务 Controller 的三种形态
```

三种 Controller 的区别不是“是否属于 Controller”，而是谁执行、控制哪种行为：

| Controller | 控制的问题 | 谁执行 |
| --- | --- | --- |
| `Role` | 这类请求属于哪个责任边界，下一步有哪些分支或能力？ | 框架组织，模型导航 |
| `Skill` | 模型应按什么步骤完成这类工作，允许使用哪些能力？ | 模型遵循 |
| `Tool` | 哪个结果可以由程序确定执行，输入契约是什么？ | 框架调用业务代码 |

`ControllerManager / Index / Disclosure` 则是这些业务 Controller 的运行机制：存在、
编译、路由与披露。把它们分别看成 MVC 的 Model、View、Controller 会丢失这个整体。

## 5. 两条修正后的请求链路

### 5.1 导航与披露

```text
用户
  ↓
Agent Host / UI                                      View
  ↓ MCP: contexture_discover / contexture_open
MCP Surface                                          输入适配器
  ↓
Disclosure                                           Controller 路由与披露策略
  ↓ resolve/ref/card
Index + Role / Skill / Tool                          Controller
  ↓ payload
MCP Surface
  ↓
Agent Host / UI                                      View 呈现并选择下一步
```

这条链路不进入业务 Model，因为 discover/open 只回答“有哪些 Controller、现在应披露
多少”。它不能打开数据库来决定能力树今天长什么样；同一个编译后的应用对每个会话
保持相同的 Controller 表面。

### 5.2 执行业务动作

```text
用户
  ↓
Agent Host / UI                                      View
  ↓ MCP: contexture_invoke[_read_only]
MCP Surface + SystemAPI                              输入适配与读写门
  ↓ ref
Index binding                                        Controller 定址与参数校验
  ↓
Tool                                                 Controller action
  ↓
Domain Model / Domain Service                        Model
  ↓
Repository / Database                                Model 持久化
  ↑ result
Tool → MCP Surface → Agent Host / UI                 返回并呈现
```

Tool 是 Controller 与业务 Model 的接缝。它的签名控制调用者能提出什么请求；领域
Model 判断一个值是否合法；Repository 判断当前持久化状态下这次变化是否允许，并在
事务中落盘。

## 6. MVC 与 Ports and Adapters 是两种观察尺度

MVC 回答宏观问题：“谁呈现、谁控制、谁拥有业务状态？”

```text
Host               View
Contexture          Controller
Business domain     Model
```

Ports and Adapters 回答同一系统内部的依赖方向：“请求从哪里进，业务规则在哪里，状态
从哪里出？”

```text
Driving adapter     MCP Surface
Application control Role / Skill / Tool
Domain              业务对象与规则
Driven adapter      Repository / SQLite / HTTP client
```

两种比较并不冲突。MVC 给第一次接触 Contexture 的人一个准确的大轮廓；Ports and
Adapters 用来约束业务项目内部不能让 Tool、领域模型和数据库实现重新粘成一层。

## 7. oc-goal 的具体映射

`docs/case-studies/oc-goal` 把这个边界落实成三层：

```text
Controller
  Contexture app
  └── GoalDomain(Role)
      ├── ReviewAttention(Skill)
      └── query / command / content Tools

Model
  Area / Goal / Focus / ContextConfig

Persistence
  GoalRepository
  └── SQLite
```

从 MVC 的宏观尺度看，Repository 和 SQLite 仍属于 Model 一侧：它们保存业务事实。
从业务代码的内部尺度看，把领域值与持久化机械件分开仍然必要，所以 oc-goal 保留
`models.py` 与 `repository.py` 两个明确边界。

例如一次 `upsert-goal`：

1. Contexture 根据 ref 找到 `UpsertGoal` Controller，并按类型签名校验输入。
2. Tool 的签名根本不接受人类专属的 `status` 字段。
3. `Goal` Model 校验 slug、horizon、success 和 ContextConfig 的值形状。
4. `GoalRepository` 检查 Area 当前是否 active，并在事务内执行 CAS。
5. Host 接收结果并决定怎样向用户呈现。

没有一层需要复制另一层的工作。

## 8. 三个名字相同但含义不同的 Model

讨论这套架构时必须区分：

| “Model” | 指什么 |
| --- | --- |
| MVC Model | 业务状态、规则与持久化，例如 oc-goal 的 Area/Goal/Repository |
| `contexture.core.model` | Contexture 用来描述 Controller 的对象模型；包名表示“框架对象模型”，不表示 MVC Model |
| AI model | 连接 Host 中负责理解、选择、遵循 Skill 的语言模型 |

`Role / Skill / Tool` 位于 `contexture.core.model`，不意味着它们属于 MVC 的 Model
层；它们是被 Contexture 对象模型描述的 Controller。

## 9. 由此得到的设计规则

1. **不要把业务数据实例变成 Role。** 数据规模不能扩大 Controller 路由表。
2. **不要把 Disclosure 当成最终 View。** 它拥有的是 Controller 路由与披露策略；
   最终呈现属于 Host。
3. **不要把 Tool 当成全部 Controller。** Role、Skill、Tool 是三种协作的业务
   Controller。
4. **不要让 Contexture 拥有业务状态。** 状态、领域规则和事务留在业务 Model。
5. **不要在业务项目复制路由框架。** Manager、reflector、registration chain 和
   schema dispatch 已由 Contexture 回答。
6. **让 Tool 成为清楚的接缝。** 参数签名控制用例入口，调用领域 Model/Repository，
   返回业务结果；不在 Tool 内再造 ORM、事务或披露机制。
7. **View 可以更换而 Controller 与 Model 不变。** Claude Code、Codex、Cockpit
   或未来 Host 都连接同一个 Contexture Application；这正是该框架存在的理由。

## 10. 最终心智模型

```text
┌──────────────────────────────────────────────────────────┐
│ View                                                     │
│ Claude Code · Codex · Chat UI · IDE                      │
└──────────────────────────┬───────────────────────────────┘
                           │ MCP
┌──────────────────────────▼───────────────────────────────┐
│ Controller — Contexture                                  │
│ Surface → Disclosure / Index → Role / Skill / Tool       │
│                 ▲                                        │
│          ControllerManager                               │
└──────────────────────────┬───────────────────────────────┘
                           │ business call
┌──────────────────────────▼───────────────────────────────┐
│ Model — business application                             │
│ Domain values / rules → Repository → Database / services │
└──────────────────────────────────────────────────────────┘
```

Contexture 的边界因而可以准确地说成：

> **它是业务 Controller 的声明、组合、编译、路由和渐进披露框架；它不拥有最终
> View，也不拥有业务 Model。**
