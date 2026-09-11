# Execution Plan — Application 用户路径落地

**状态：** 已完成

**依据：** [Design 03](03-application-user-model.md)

**日期：** 2026-08-21

> 本文是实施顺序，不是新的架构决策。任何导致 wire golden、内核职责或公开模型偏离
> Design 03 的发现，都必须先更新设计或增加 ADR，再继续实现。

## 1. 执行原则

1. **先让安装物可信，再扩展用户 API。** 当前 checkout import 成功不能代表 wheel 正确。
2. **先写契约测试，再增加门面。** Application 必须证明自己保持懒构建。
3. **一条 build path。** check/list/inspect/call/serve 和自定义 main 只能共享构建原语，
   不能各自复制 Manager/Index 组装。
4. **新手路径先落地，兼容门随后适配。** 新 scaffold 只讲 app，旧 table 通过 adapter 存活。
5. **不顺手整理目录。** 除方案明确新增的文件外，不借机移动 core/server 或恢复 src。
6. **每阶段可独立回退。** 每个阶段保持测试绿、golden 可比、工作树变化可审查。

## 2. 目标依赖图

```text
P0 基线与构建安全
        ↓
P1 Application 契约与模型
        ↓
P2 统一 loader / builder / serve
        ├──────────────┐
        ↓              ↓
P3 check/call      P4 新 scaffold
        └──────┬───────┘
               ↓
P5 公共接口与 handbook
               ↓
P6 多语言规范与 conformance
               ↓
P7 全链路发布门禁
```

P0–P2 是后续工作的硬依赖。P3 与 P4 可以在 P2 后并行开发，但合入时先 CLI、后模板，
保证新模板生成后已有命令可用。

## 3. 全局不变量

每个阶段都必须保持：

- `spec/golden/` 默认无 diff。
- `import contexture` 不加载 `mcp`。
- declaration module import 不产生 ContextNode 实例。
- 一次 Application build 产生一棵独立森林。
- Channels 的 I/O 只发生在 provisioned lifecycle 中。
- `read_only=False` 的 Tool 不可被本地 call 隐式执行。
- 现有源码保持 flat layout。

## 4. P0 — 固化基线并修复构建安全

### 目标

保证后续所有用户体验都是从用户实际安装的 wheel 验证，而不是由仓库根目录遮蔽错误。

### 任务

- [ ] 记录实施开始时的 commit、Python/uv 版本和完整测试结果。
- [ ] 为当前已知的 layering 失败建立最小回归测试：顶层 `contexture_mcp.egg-info`
      不得被当作 package。
- [ ] 将 build backend 从 setuptools 切换为 `uv_build`。
- [ ] 显式配置 `module-name = "contexture"`、`module-root = ""`。
- [ ] 按实施时支持版本固定 `uv_build` 同一 minor 的上下界。
- [ ] 删除 setuptools 专属 package discovery/package-data 配置；确认 templates 仍进入 wheel。
- [ ] 增加 wheel manifest allowlist 测试。
- [ ] 增加仓库外隔离安装 smoke test，确保 cwd 不在 checkout 内。
- [ ] 增加从 sdist 构建 wheel 的路径，并比较 manifest。
- [ ] 在构建目录故意放置一个历史 `.py` 后重建，证明它不会进入 wheel。

### 建议配置目标

```toml
[build-system]
requires = ["uv_build>=0.12.5,<0.13"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "contexture"
module-root = ""
```

### 验收

- wheel 中不存在 checkout 已删除的历史 module。
- wheel 包含 CLI templates。
- 后续加入的 `__init__.pyi` 和 `py.typed` 能由同一规则自动包含。
- 隔离环境中的 `contexture --help` 和 `python -c 'import contexture'` 成功。
- 当前测试除已事先记录、与本阶段无关的问题外全部通过；已知 layering 失败被关闭。

### 风险与回退

- 如果 `uv_build` 无法覆盖现有纯 Python/package-data 需求，停止在本阶段并记录证据，
  再评估 hatchling；不得用复制文件到 build 目录的脚本规避。
- 回退只涉及 `pyproject.toml` 和构建测试，不触碰业务模型。

## 5. P1 — 定义 Application 契约

### 目标

新增 `Contexture` 惰性值对象，不构建任何运行时对象。

### 任务

- [ ] 新增一份 ADR，记录为什么 Application 是声明而不是 Server facade，并明确它与
      历史 `ContextureApp` 的区别。
- [ ] 先写 `tests/test_application.py` 的契约测试。
- [ ] 实现冻结、slots、keyword-only 的 `Contexture`。
- [ ] 正规化 roots/prompts/resources 为 tuple。
- [ ] 校验 name、空 roots、factory kind 和明显的跨字段错误。
- [ ] 从 `contexture` 顶层导出 `Contexture`。
- [ ] 保持 Role/Skill/Tool/Prompt/Resource 的真实类型身份不变。

### 必须先写的测试

1. import app module 后 ContextNode 实例计数不变。
2. 构造 app 不调用任何 root/Channels/publish factory。
3. 构造 app 不导入 `contexture.server` 或 `mcp`。
4. 输入 list 被正规化后，修改原 list 不改变 app。
5. 同一 app 可被 build 两次并产生不共享节点的森林。
6. 空 name、空 roots、实例代替 factory、错误 publish kind 给出面向用户的错误。
7. Role、Skill、Tool 都可作为 root factory；默认示例只用 Role root。

### 验收

- `from contexture import Contexture` 可用。
- 下列代码只创建一个 Application 声明对象：

  ```python
  app = Contexture(name="hello", roots=(Hello,))
  ```

- 原有 golden 和 server tests 不需要为 Application 改 payload。

## 6. P2 — 统一加载、构建和启动

### 目标

CLI、手写 main 和后续本地命令共享同一条 Application 到内核的翻译路径。

### 建议内部原语

命名可在实现评审时调整，但职责必须保持：

```python
load_application(target, project=...) -> Contexture
compile_application(app) -> CompiledApplication
build_server(app, options/auth...) -> ContextureServer
serve(app, options) -> None
```

`CompiledApplication` 如果存在，只能携带一次构建产生的 Manager lifecycle、Index 和
typed published declarations；不得成为新的万能 Assembly。若现有对象已经足够，则用
小函数/上下文管理器，不为对称性创建类。

### 任务

- [ ] 实现 `module:attribute` app loader，保留项目路径防 shadowing 检查。
- [ ] 实现唯一 application compiler：实例化 Channels、注册 roots、构建 Index、
      分别实例化 prompts/resources。
- [ ] 为不同 root kind 调用现有三个 typed register 方法。
- [ ] 在 `contexture.server` 提供 `serve(app, options)`。
- [ ] `contexture serve` 改为加载 app 后调用同一 server builder。
- [ ] demo 声明 `app`，其 `main()` 只展示 `serve(app, options)`。
- [ ] 添加 `[tool.contexture] app = "module:app"` 配置读取。
- [ ] 同一 table 同时出现 app 与 legacy keys 时给出确定错误。
- [ ] legacy name/roots/channels/publish 转成临时 Application，并发出一次迁移警告。
- [ ] 保留显式 `contexture serve package.module:RoleClass` 的兼容行为，文档标记为临时
      单 root 调试入口，而不是推荐项目配置。

### 测试矩阵

| 场景 | CLI | 手写入口 | 预期 |
| --- | --- | --- | --- |
| 最小 Role/Skill/Tool | serve | serve(app) | 相同 index 和 surface |
| 带 Channels | serve | serve(app) | 相同 open/close 顺序 |
| Prompt + Resource | serve | serve(app) | 分别进入正确 plane |
| app target 不存在 | serve | loader | 相同可操作错误 |
| app target 类型错误 | serve | loader | 不接受任意对象 |
| legacy table | serve | 不适用 | 成功并只警告一次 |
| mixed table | serve | 不适用 | 拒绝，不猜优先级 |

### 验收

- CLI 与手写 main 没有第二份 Manager/Index 组装代码。
- app import 仍保持惰性。
- stdio wire tests 和 golden tests 不变。
- demo 可以通过 CLI 和 `python -m contexture.demo.server` 两条路径启动。

## 7. P3 — 本地开发闭环

### 7.1 `contexture check`

#### 任务

- [ ] 增加 parser、command 和帮助文本。
- [ ] 复用 application compiler，但在 Channels `open()` 前停止。
- [ ] 将 loader、model validation、Index validation、binding/schema 错误统一为诊断项。
- [ ] 诊断包含 kind、ref/target、原因和下一步，不输出 traceback；`--debug` 才输出底层异常。
- [ ] 成功输出 app name、root/node/tool 数量和 `OK`。
- [ ] 固定退出码 0/1/2。

#### 验收样例

```text
$ contexture check
OK hello-context: 1 role, 1 skill, 1 tool
```

错误必须指向声明现场或 ref，例如：

```text
ERROR skill hello/greet_user uses unknown ref hello/say-helo
       known tool: hello/say_hello
```

### 7.2 `contexture call`

#### 任务

- [ ] 增加 `REF`、`--input`、`--input-file`、`--allow-write`。
- [ ] `--input` 与 `--input-file` 互斥；缺省输入是 `{}`。
- [ ] 通过 Index 找 Tool，使用生产 binding 校验并调用。
- [ ] 进入与 serve 相同的 Channels provisioned lifecycle。
- [ ] 拒绝 Role/Skill ref，并指向 `contexture inspect REF`。
- [ ] 默认拒绝非只读 Tool，在错误中给出 `--allow-write`。
- [ ] 结构化结果以 JSON 输出；纯文本结果直接输出；bytes/unsupported result 走现有翻译规则。
- [ ] 调用异常不吞掉业务原因，默认输出用户可读错误，`--debug` 保留 traceback。
- [ ] 不伪造远端认证 Principal；在文档中标明边界。

#### 必须覆盖

- read-only success、default arguments、schema failure。
- wrong ref、Role/Skill ref、write refusal 和显式 allow-write。
- Channels open success/failure、invoke failure、close failure以及逆序释放。
- 并发不是本命令目标，但不得修改 Tool 实例语义。
- stdout/stderr 分离和退出码。

### 7.3 现有命令统一

- [ ] `list` 与 `inspect` 改为消费 app/compiled application。
- [ ] 保留 inspect 的 disclosure trace 和预算功能。
- [ ] 增加 `contexture --version`。
- [ ] 更新所有 help 文本，使顺序呈现 `new → check → list/inspect/call → serve`。

## 8. P4 — 重写脚手架为第一条用户路径

### 目标

`contexture new hello-context` 生成的项目在没有 Host、数据库或额外文件的情况下完成
第一轮成功，同时诚实地展示 Role、Skill、Tool。

### 任务

- [ ] `Names` 增加稳定的 Python package name，例如 `hello_context`。
- [ ] 模板由固定 `assistant/` 改为派生的 importable package。
- [ ] `__init__.py` 声明并导出唯一 `app`。
- [ ] `pyproject.toml` 只写 `[tool.contexture] app = "hello_context:app"`。
- [ ] 第一版模板保留 `role.py`、`skills.py`、`tools.py`，不生成 Channels、Prompt、
      Resource 或自定义 main。
- [ ] Skill 明确 uses 同项目 Tool；Tool 有可直接调用的默认参数。
- [ ] README 第一屏依次给出 sync、check、inspect、call、serve。
- [ ] `.gitignore` 覆盖 `.venv`、`__pycache__`、构建产物和本地工具输出。
- [ ] 增加脚手架快照测试及生成后真实执行测试。

### 目标 README 第一屏

```bash
uv sync
uv run contexture check
uv run contexture inspect hello/greet_user
uv run contexture call hello/say_hello --input '{"name":"Alice"}'
uv run contexture serve
```

### 验收

- 新项目文件不超过第一轮任务所需概念。
- 用户无需修改任何文件即可看到 check/call 成功。
- 修改 SayHello 返回值后再次 call 能立刻看到结果。
- 添加第二个 Skill 或 Tool 的位置无需猜测。
- 生成项目没有自己的 framework interface 副本，只 import 安装的 `contexture`。

## 9. P5 — 公共接口、文档与开发体验

### 9.1 Python 接口视图

#### 任务

- [ ] 增加 `contexture/__init__.pyi`。
- [ ] 增加 `contexture/server/__init__.pyi`。
- [ ] 增加 `contexture/py.typed`。
- [ ] 在 wheel manifest test 中固定这些文件。
- [ ] 增加一个只依赖已构建 wheel 的 Pyright 或 mypy fixture。
- [ ] 增加 public API snapshot，检测意外增加/删除 `__all__` 名称。
- [ ] 增加 import identity tests，证明顶层 facade 与真实实现是同一对象。

#### 接口内容分层

`contexture` 的主视图先展示：

```text
Contexture, Role, Skill, Tool
Channels, Prompt, Resource, Principal
公开错误类型
```

当前兼容导出继续列出，但在注释中标为 advanced/internal compatibility。不能让 stub 与
运行时 `__all__` 无声不一致。

### 9.2 用户 docstring

- [ ] 重写 Role、Skill、Tool、Channels、Prompt、Resource、Principal 的首屏说明。
- [ ] 删除首屏中的历史版本叙述，将其链接到 ADR。
- [ ] Tool 文档固定 re-entrant/concurrency 约束。
- [ ] Skill 文档固定“模型执行，框架不调用”。
- [ ] Role 文档固定 children/skills/tools 的放置判断。
- [ ] Contexture 文档固定惰性和两种启动方式。
- [ ] `help(contexture.Tool)` 与 IDE hover 的核心文字做快照或 doctest。

### 9.3 Handbook 与 README

- [ ] 将 README 顶部改为五分钟成功路径。
- [ ] 按 Design 03 §13 建立九章 handbook。
- [ ] 每章从同一个示例演进，不创建互不相干的片段。
- [ ] 把 Manager/Index/Disclosure 移到 internals 章节。
- [ ] 将当前“Writing the entry point yourself”改为 app + serve 的短入口；低层组装另列。
- [ ] 更新 Host verification 文档使用新项目 app。
- [ ] 更新 HANDOFF A：删除完成项，只保留仍未解决项。

### 验收

- 新用户只读 README 前半页即可完成 call。
- `help()`、IDE、README、handbook 对 Role/Skill/Tool 的定义一致。
- 文档中的所有 shell 和 Python 示例由测试或文档 smoke job 执行。

## 10. P6 — 固化语言无关契约

### 目标

避免 Python `.pyi` 和继承写法成为未来 TypeScript、Go、PHP 的隐性标准。

### 任务

- [ ] 新增 `spec/model.md`：Application、Role、Skill、Tool、Channels、Prompt、Resource
      的字段、语义和不变量。
- [ ] 新增 `spec/conformance.md`：注册、编译、ref、披露、调用、生命周期和错误规则。
- [ ] 新增 `spec/fixtures/`，用语言无关数据描述 demo 森林，不包含 Python module path。
- [ ] 明确 Python type-hint schema derivation 只是 binding strategy。
- [ ] 为 TypeScript、Go、PHP 各写一页非规范映射草图，只说明语言习惯，不承诺实现日期。
- [ ] 让现有 Python demo 与语言无关 fixture 的语义对应关系可自动检查。
- [ ] 保持 `spec/golden/` 为 wire/output 事实来源。

### 验收

- 一个没有阅读 Python 源码的 port 作者能从 spec 确定每种节点携带什么、何时构建、
  如何寻址以及必须产生什么输出。
- spec 不出现“必须继承 Python class”“必须从 signature 反射”等 Python 独占要求。
- Python 实现继续通过现有 golden。

## 11. P7 — 发布级端到端验收

### 11.1 自动化测试层次

```text
unit              Application、loader、CLI 参数与诊断
kernel            Manager / Index / Disclosure 现有测试
integration       Channels + binding + call + server
wire              stdio / HTTP 与 golden
package           sdist/wheel manifest、隔离安装、typing
journey           从安装 wheel 到 scaffold、check、call、serve
host              Claude Code / Codex 实机验证
```

### 11.2 必跑命令

具体脚本在实施中固化，最低要求：

```bash
uv run python run_tests.py
uv build --no-sources
# 在仓库外的临时环境安装刚生成的 wheel
# 执行 import、CLI、scaffold、check、call 和 stdio smoke
node docs/atlas/check.mjs
```

如项目引入静态检查器，还必须对 wheel 安装后的示例运行，而不是只检查 checkout。

### 11.3 人工验收脚本

在全新临时目录中：

1. 安装刚构建的 wheel 所提供的 CLI。
2. `contexture new hello-context`。
3. 确认只出现设计规定的文件。
4. `uv sync`。
5. 运行 check、list、inspect 和 call。
6. 修改 Tool 返回值并再次 call。
7. 用 stdio 连接至少一个真实 Host，完成 discover/open/invoke。
8. 写一个短 `main()`，证明与 CLI 使用同一个 app。
9. IDE 或静态检查器确认错误 override 能被发现、正确代码无错误。

### 11.4 发布门禁

以下任一项不满足则不得发布：

- 测试有未解释失败。
- golden 出现未评审 diff。
- wheel 含 checkout 中不存在的 module。
- wheel 缺 template、`.pyi` 或 `py.typed`。
- 示例只能在仓库根目录运行，移到临时目录后失败。
- CLI 与自定义 main 对同一 app 构建出不同 surface。
- write Tool 可在没有 `--allow-write` 时被 `contexture call` 执行。
- README 的第一条路径无法逐行复制运行。

## 12. 迁移和提交策略

建议每个阶段拆成小而完整的提交，顺序如下：

1. `test(packaging): reproduce stale wheel contamination`
2. `build: use uv_build with the flat contexture module`
3. `test(application): define the lazy application contract`
4. `feat: add the Contexture application declaration`
5. `refactor: share application loading and compilation`
6. `feat(server): serve one application declaration`
7. `feat(cli): add check`
8. `feat(cli): add guarded local call`
9. `refactor(scaffold): generate an application-first project`
10. `feat(types): ship the public Python interface view`
11. `docs: replace the quick start with the six journeys`
12. `spec: state the language-neutral application contract`
13. `test(release): verify the installed wheel journey`

不要把目录整理、命名美化或无关 lint 修复混入这些提交。旧配置移除也不与新增 app 放在
同一 release 中。

## 13. 评审检查表

实施前请逐项确认：

- [ ] 顶层类名使用 `Contexture`，不再并列 `Application`/`ContextureApp`。
- [ ] 配置目标使用 `[tool.contexture] app = "module:app"`。
- [ ] 第一份项目同时包含 Role、Skill、Tool。
- [ ] 默认没有 main，自定义 main 仍正式支持。
- [ ] `check` 不打开连接，`call` 打开并关闭连接。
- [ ] `call` 默认拒绝写 Tool。
- [ ] 不新增 runtime `interfaces` package。
- [ ] 不移动现有源码目录、不增加 src。
- [ ] 采用 flat-layout `uv_build` 并做隔离 wheel 验证。
- [ ] legacy config 至少保留一个过渡 release。
- [ ] TypeScript/Go/PHP 只共享语义和 conformance，不复制 Python 类结构。

## 14. 完成定义

当且仅当以下结果同时成立，本计划完成：

1. 全新用户从安装 wheel 到得到 Hello World Tool 结果，不需要 MCP Host 或手写 main。
2. 第一份代码已经清楚区分 Skill 和 Tool，而不是以后再补学 Skill。
3. 同一 app 驱动 check/list/inspect/call/serve 和自定义 main。
4. Application 保持声明惰性，内核三段职责和 wire contract 未被门面侵入。
5. wheel 在仓库外可安装、可运行、带完整接口视图且不含历史源码。
6. README、handbook、docstring、stub 和 CLI help 对用户讲同一条路径。
7. 语言无关规范足以约束未来 TypeScript、Go 和 PHP binding。
