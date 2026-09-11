# ADR 015 — server 是一个对象

**Status:** accepted, implemented in v0.5.1
**Date:** 2026-08-21

**不取代任何既有 ADR。** ADR 014 把导航收进内核之后,`core` 已经是一个干净的对象
模型;这份决定只动 `server`,把它从一组互相传参的自由函数,变成一组各管一件事的
对象。ADR 013 的"构造函数即声明"和 ADR 009 的"协议平面不是对象模型"都是它的前提,
不是它要改的东西——009 在实施途中还纠正了这份提案的一个错误落点,见第 2 节。

## Context

`server` 改动前是 1078 行(`app.py` 511 + `binding.py` 567),对外只有两个类和三个
自由函数。**其中只有 505 行是真代码**(用 AST 数的:去掉 docstring、注释、空行)——这个包大约一半是散文,所以"行数"这个尺度
在这里只有按真代码算才有意义。四个症状,都能在代码里指出来。

### 1. 三个时刻被压进一个构造函数

`ContextureApp.__post_init__` 做四件事:建 `Dispatch`、normalise `publish`、建
registry、seal 出 `ContextTree`,然后把 `self.roots` 就地改写成 `tree.roots`。

后果是**构造即封树**。`demo/server.py` 在模块级写 `app = ContextureApp(roots=…)`,
所以 `import contexture.demo.server` 这一句就把整片 KubernetesPlatform 森林(12 个
节点)建了出来并封死。ADR 013 说过:

> **import 不构造任何东西**——类是一个零参工厂,`ControllerManager` 调它那一次,
> 是全包唯一一个节点诞生的时刻。

那句话当时对 `core` 成立,对 `server` 不成立。

### 2. `binding.py` 里传的是参数,不是对象

`project(server, *, tree, dispatch, publish)` 是一个只有副作用的函数。同一批上下文
在下游被手工透传一遍又一遍——

```
project(server, tree, dispatch, publish)
  └─ _project_published(server, tree=, dispatch=, publish=, api=)
       ├─ _resolve(tree, ref, kind)
       ├─ _require_content_tool(tree, dispatch, entry)
       ├─ _reader(tree, ref)
       └─ _command(api, ref)
```

五个签名,四份同样的上下文。一个对象被拆开在函数之间传递,是它本该是对象的信号。

### 3. 三个原语的规则完全不同,却挤在一个 if/else 里

| 平面 | 业务能写吗 | 启动时要检查什么 |
| --- | --- | --- |
| tool | 不能 | 无 |
| prompt | 能 | 重名、`opens` 可解析 |
| resource | 能 | 重名、URI 重复、`opens` 可解析、必须 read-only、必须无参 |

这三套规则挤在 `_project_published` 一个 for 循环的两个分支里,加一个同时管两种
kind 的 `_reject_ambiguous_names`。而 `core/mcp_interface/` 早就是一个原语一个模块。
**投影这一侧没有跟上声明那一侧的形状。**

而且检查是**边查边挂**的:一条坏 resource 声明抛出时,四个 gateway tool 和它前面的
prompt 已经挂在 SDK server 上了。

### 4. `channels` 没有类型,于是生命周期只能靠运行时 refusal 表达

`channels: Any`,框架"从不检视"。这是刻意的,代价却落在别处:`provision` 必须是
"返回 async context manager 的工厂,而不是 context manager 本身",这条约束没有类型
能表达,只能写成 `ControllerManager.__post_init__` 里的 `hasattr(self.provision,
"__aenter__")` 加一段解释,以及 `provisioned()` 里的第二段。**`__post_init__` 里
100% 是这件事**,`provision` 一走它整个消失。

## 参考:brpc

brpc 的 main 之所以一眼能读,不是因为它短——它有 gflags 声明、`ParseCommandLineFlags`、
每一步的 `!= 0 → LOG(ERROR) → return -1`、从 flag 拼 `EndPoint`。**是因为每一个它
做决定的对象都在 main 里有名字。**

| brpc | 改动前 | 现在 |
| --- | --- | --- |
| `Service` | Role / Skill / Tool | 不动 |
| `Server::AddService` | 构造参数 `roots=` | `ControllerManager.register_*`(已有) |
| `Server` | `ContextureApp` + `project()` | `ContextureServer` |
| `ServerOptions` | `ContextureOptions`(与 App 同居 app.py) | `ContextureOptions`,`options.py` |
| `Start` | `run()` | `start()` / `start_async()` |
| `Channel`(出站) | `channels: Any` + `provision` | `Channels` 基类 |

**没有借的:** 装饰器注册(ADR 013 拒绝过:类体扫描在 Go 和 TypeScript 里都做不到)、
middleware 链(横切点是 `SystemAPI.execute`,那里已经有身份绑定这一个横切)、
按连接变表面(协议禁止,`stateless_http` 已被钉成 `True`)。

## Decision

### 1. main 是装配点,不是四行糖

```python
def main() -> None:
    channels = ClusterChannels(kubeconfig=Path(os.environ[ENV_KUBECONFIG]))

    manager = ControllerManager(channels=channels)
    manager.register_role(KubernetesPlatform)

    dispatch = Dispatch()
    tree     = manager.sealed(schema_of=dispatch.schema)
    assembly = Assembly.of(tree, execute=dispatch.execute, published=PUBLISHED)

    server = ContextureServer(assembly, name="operations")
    server.start(build_options(args))
```

七个具名对象,每一个都是业务能做决定的地方。

**`dispatch` 出现两次是有意的。** 它同时喂 `schema_of` 和 `execute`,所以 main 里
看得见"卡片上写的 schema 和校验用的那把尺是同一个东西"——这条不变量此前只写在
docstring 里。

**明确不做的:`ContextureServer` 没有 `add_role`。** 注册在 `ControllerManager` 上,
它本来就有 `register_role/register_skill/register_tool`。在 server 上再开一组是同一
扇门的第二个把手。

### 2. `Assembly`:密封的产物,住在 `server`

```python
@dataclass(slots=True, frozen=True)
class Assembly:
    tree:      ContextTree
    api:       SystemAPI
    prompts:   tuple[Prompt, ...]
    resources: tuple[Resource, ...]
```

`Assembly.of` 做四件事:规范化发布清单(类→值)、检查每个 `opens` 指到真节点、按种类
分成两个具名字段、从 prompt 推出 `reserved` 并建 `SystemAPI`。

**它本来被放进 `core/model/`,`tests/test_layering.py` 把它退了回来。** 提案的理由是
"这些规则在 MCP 被换掉之后仍然成立",听起来像内核活。但架构说了别的,而架构是对的:

> `"core.mcp_interface": {"core.__base__"}` —— 这一条最要紧的是它**省略了什么**:
> `core.model`。协议平面不能认识对象模型 —— 它只持有名字和引用**字符串**,所以它
> 伸不进森林,森林也伸不回来。

两个平面是**互不依赖的兄弟**(ADR 009),它们之间流通的货币是 `frozenset[str]` ——
这正是 `SystemAPI.reserved` 的类型。而密封按定义就是这两个兄弟的**接合**:它读
`Prompt` / `Resource` 对象,并把它们对着一棵树解析。接合处属于两者**之上**,而
之上的第一层就是 `server`。

这条记在这里,因为提案在这一点上错了两次(第二次是想把重名检查沉进
`mcp_interface`),而层级测试两次都在第一次运行时抓到。

### 3. 三个平面,一个原语一个模块,构造即检查

```
server/projection/
├── __init__.py    published_name, translated
├── gateway.py     Gateway    —— 四个入口,从 GATEWAY 一条元组注册
├── prompts.py     Prompts    —— 每条命令 + goto + completion
└── resources.py   Resources  —— 每份文档
```

和 `core/mcp_interface/` 一一对应:后者声明"这个原语上放什么",前者做"把它放上去"。

**检查在构造函数里,写入在 `project()` 里**,而 `ContextureServer.build()` 先构造
三个平面、再逐个投影:

```python
planes = (Gateway(a), Prompts(a), Resources(a))   # 此时 MCPServer 还不存在
surface = self._surface(auth)
for plane in planes:
    plane.project(surface)
```

所以一条坏声明抛出时,表面**根本还没被创建**,不存在"挂了一半"这回事。用的是这个
包自己的惯例(ADR 013:构造函数是声明被检查的地方),不是一个新造的钩子。

**提案里"一次报全所有声明错误"这个目标撤掉了。** `check` 第一条就抛,三个平面串起来
仍是一次一条;要真报全就得改成收集再拼接,而那会动到这批 refusal 文案——它们是这个包
最值钱的东西之一。真正的收益是"不留半份表面",那个是免费的。

**留在这里而不下沉的两条规则:**「一个 resource 必须只读、必须无参」的理由是"MCP 的
resource 是被 fetch 的";「两条同名 entry 不行」的理由是"MCP 的列表是平的"。换掉
MCP 两条都不成立,所以它们和说这门协议的代码住在一起。

### 4. `ContextureServer`:相位由结构强制

它接一个已经密封的 `Assembly`,**没有任何注册方法**。想往一台正在服务的 server 里塞
节点——没有那个方法可以调。这比一个运行时标志位强:标志位只能在事后抱怨。

`build()` 是**幂等**的:`start()` 会调它,一个已经 build 过再 start 的调用者否则会
serve 第二台。第二次传不同的 `auth` 是拒绝而不是忽略,因为两个答案里只有一个能在线上。

`build()` 保持**同步**,生命周期包住的是 *serving* 而不是 *construction* ——
`app.py` 已经论证过,不改。

补上 `start_async()`,填掉"把一台 Contexture 跑进别人已有 event loop"这个缺口。
它**不**解决"挂进别人已有的 Starlette 应用"——HTTP 分支跑的是 SDK 自己的 uvicorn;
要 mount 的人应当取 `build().streamable_http_app()`。

### 5. `Channels`:生命周期终于有了类型

```python
class Channels:
    _stack: AsyncExitStack | None = None   # 类级默认,子类不必写 super().__init__()

    async def open(self) -> None: ...      # 第一个请求之前
    async def close(self) -> None: ...     # 最后一个请求之后
    async def enter(self, cm): ...         # 只在 open() 里有效,交给栈托管

    @asynccontextmanager
    async def lifespan(self): ...          # 框架调这个
```

`provision=` 随之退役,连同它约 45 行运行时 refusal。

**`enter` 是这里有基类而不是协议的理由。** `provision` 免费提供过一样东西:
`async with a, b` 的逆序退栈,以及"开 A 成功、开 B 失败时 A 被关掉"。让业务手写
`open`/`close` 会把这个拿走,换来一类更难看见的 bug:半开状态没人关。基类持一个
`AsyncExitStack`,业务写

```python
async def open(self) -> None:
    self.gw = await self.enter(gateway_session(URL))
    self.db = await self.enter(create_pool(DSN))
```

语义全部回来,而且**栈是在 `lifespan` 里建的,不是在构造函数里**,所以"能被 open
两次"是结构性的——那条只能写成运行时 refusal 的规则("必须传工厂,因为 context
manager 被 enter 一次就用光了")现在由类型表达。

**`close` 只与成功返回的 `open` 配对**,和 `__exit__` 只与 `__enter__` 配对一样。
`open` 中途失败时,它已经 enter 的东西由栈逆序退掉,`close` 永远不必被写成能容忍
半开对象。这一条是实施中由一个失败的测试逼出来的,提案里没有。

**不做 ABC。** 两个方法都有默认实现,强制子类写 `async def open(self): pass` 没有
意义;没有生命周期的句柄本来就该走 `channels=普通对象` 那条路,根本不该继承。

**一处行为变化:** 盖章的和打开的现在是同一个实例,所以进场的 `rebind_channels`
消失了,退场时框架也不再把节点上的句柄清成 `None`。"关服后到达的调用报得清楚"这条
性质变成业务 `close()` 的事——它把自己的字段置空,那本来也是它会写的一行。框架不能
替它做,因为框架不检视它持有什么。

### 6. 目录

```
contexture/server/                       总行  真代码
├── __init__.py      facade,懒解析        101     —
├── server.py        ContextureServer      231    98   主干
├── assembly.py      Assembly              143    41   主干
├── projection/      四个模块              468   186   主干
├── options.py       ContextureOptions     307   157   叶子
├── dispatch.py      Dispatch              166    55   叶子
├── identity.py / instructions.py / messages.py / launch.py   不动

contexture/core/model/
└── channels.py      Channels              124    17   新
```

**主干 325 行真代码。** `options.py` 那 157 行是一个读流程的人永远不必打开的叶子:
它跟树、跟 core、跟整条流程都无关,只是一个自足的值对象加一堆 refusal 文案。

**总量不降,略涨:505 → 537 行真代码(+32)。** 涨的几乎全在 `Assembly` 那 41 行——
一个此前不存在、被拆成四个参数在五个签名之间传的东西,现在有了名字。这次买的不是
行数,是"一个模块一件事":改动前最大的文件同时管 schema 派生、四个网关入口、prompt
规则、resource 规则和 JSON Schema 遍历。

## 不做什么

- **不给 `ContextureServer` 加 middleware 链。** 横切点是 `Dispatch.execute`。
- **不做装饰器 / 元类注册。** ADR 013。
- **不做 `Source` 协议来统一"根从哪来"。** 提案第 5 节写过,砍掉了:`cli/main.py`
  已经自己 `load_roots(...)` 解析完并交出**对象**,而业务的 `main()` 手里本来就有类。
  今天没有第二个调用者,加它就是 ADR 013 在别处拒绝过的那种预留抽象。重构之后 CLI
  和业务 main 走的是同一条路,而那是 `register_root` 带来的,不是 `Source`。
- **不让 `ContextureServer` 继承 `MCPServer`。** 运行时拥有角色与披露,SDK 拥有线。
- **不动 `messages` / `instructions` / `identity` / `launch`。**
- **反射的剂量不变。** `cli/project.py` 里那一处 `importlib` + `getattr` 现在被三个
  key 共用(`roots` / `publish` / `channels`),但它做的事没有变多:把一个字符串变成
  一个模块属性,到此为止。把属性变成节点的仍然只有 `ControllerManager._build`,把
  它变成句柄的是同一条"类即零参工厂"的规则。

## 一个顺带被解决的开放问题

HANDOFF 条目 A 点名过:`ControllerManager(channels=…)` 收的是**活对象**,而
`[tool.contexture]` 只能写字符串,所以一个需要连接的项目用不了 `contexture serve`,
得自己写 entry point——而 README 开篇承诺过它不必写。

**这份 ADR 把它变成了一个 key,而不是一套新机制。** 因为 `provision` 是一个*函数*
(返回 async context manager),而这个包里没有任何规则能把一个被命名的函数变成活对象;
`Channels` 是一个**类**,而"类是零参工厂"是 `roots` 从 ADR 013 起就在用的规则。

```toml
[tool.contexture]
roots    = ["assistant:MyContextAssistant"]
channels = "assistant.channels:ClusterChannels"
```

零参构造函数记地址(不做 I/O),`open()` 建连接。于是 `contexture serve` 和一个手写
的 `main()` 走**同样的五步、同样的顺序**,差别只剩每个对象是被一张表命名还是被一句
import 命名。

这不是这次重构的目标,是它的副产品——把 `channels` 从"一个必须传工厂的参数"改成
"一个类",顺手让它落进了一条已经存在的规则里。
