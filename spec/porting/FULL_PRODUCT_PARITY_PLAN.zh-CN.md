# Contexture TypeScript 与 Go 完整产品等价计划

[English](FULL_PRODUCT_PARITY_PLAN.md) 是权威版本；本文是对应的简体中文说明。

## 决策

新的目标不是仅完成 0.12 的 16 条中立契约，而是交付两个可独立安装、可开源发布的
Contexture 项目：它们与 Python 项目在产品能力、架构、公开概念、可观察行为、文档、
示例、测试覆盖与发布质量上等价。仅在 TypeScript/Go 的语法、包管理器和原生传输实现
确有必要时允许差异。

基准固定为 Python master 的
3b274421360d5569a23922bfc72b71d5828cf995（0.12.0rc1）。该提交的产品代码与
e107a81 相同，新增内容仅为项目状态文档。以后 Python 变更必须通过显式的基准升级和
已审查的差异清单进入两个绑定。

现有 TS/Go 仓库是有价值的内核原型，不是完成基线。在本计划所有验收项通过前，任何
README、元数据或发布说明都不得称它们为“完整等价”。

## 五个必须同时对齐的平面

| 平面 | Python 依据 | TS/Go 必须具备的结果 |
| --- | --- | --- |
| 公开声明 API | __init__.py、application.py、core/model | Contexture、Role、Skill、Tool、Channels、Prompt、Resource、Principal、引用、惰性和校验等相同概念与约束。 |
| 内核与架构 | core/、ADR 009–019 | 受测试保护的 foundation、model、MCP interface 与 SDK 无关内核，不能压扁成泛用 core。 |
| 产品接口 | cli/、inspection.py、server/、web/ | 等价的 new/check/list/inspect/call/serve/demo，MCP、REST、身份和根选择。 |
| 开发者体验 | README、handbook、demo、templates | 可安装包、脚手架、可运行 demo、API/架构文档；英文优先，中文保持翻译。 |
| 置信度与发布 | Python 测试和打包门禁 | 一一映射的测试、原生静态检查、跨语言差分场景、外部消费者安装测试和包检查。 |

Python 的实现机制不是要求：子类构造可变为 TS builder/class 或 Go
constructor/factory，类型提示反射可变为 Zod/schema 或 Go tagged struct；但用户可见
的声明含义和完整工作流必须等价。

## 架构要求

TS 必须建立 application、inspection、core/foundation、core/model、
core/mcp-interface、server/surface、web、cli/templates 与 demo 边界。core 不得导入
MCP SDK、HTTP、Node process/CLI 或 CLI；模型内部为请求局部上下文所需的 Node runtime
primitive 例外。MCP interface 仅能依赖 foundation；server 才能导入 MCP SDK，web 才能
导入 HTTP。

Go 必须建立 module-root facade、inspection、core/foundation、core/model、
core/mcpinterface、server/surface、web、cmd/contexture/templates 与 demo 边界。
mcpinterface 是 Go 路径风格对 Python core/mcp_interface 的命名差异。root facade
不得反向导入 server/web/cli/MCP SDK。

## 工作账本

第一步创建带版本控制的 PYTHON_0_12_PRODUCT_MANIFEST.json 和可读 Markdown 视图。
它逐项记录 Python 模块、公开符号、职责、TS/Go 对应路径、可观察行为、测试/文档证据、
Python 内容 hash 和状态。CI 必须拒绝未映射模块、缺失路径或没有证据却标记为 verified
的条目。

清单覆盖：公开 facade/application、foundation、全部 core/model、MCP interface、
全部 server 与 surface、web、inspection、全部 CLI/模板、demo，以及全部测试（含
外部消费者类型/编译测试）。只有 Python 语法机制、typing stub 或明确替换的原生传输
机制才能标为 not-applicable；不能因为旧端口缺失用户工作流而跳过。

## 执行阶段

1. **基线与可观察性**：生成产品/测试映射清单、CLI/模板/API 清单和跨语言行为场景。
2. **恢复架构**：TS 先重构目录并加分层测试，随后 Go；保留已有内核修复和回归测试。
3. **声明与模型**：完整实现 node、role、skill、tool、binding、channels、index、manager、
   disclosure、selection、runtime、graph context、principal、telemetry、system API。
4. **Server/MCP/Web**：完整移植编译、绑定、身份、选项、launch、所有 surface、真实
   MCP 传输和 REST allowlist。
5. **Inspection 与 CLI**：完整实现 new/check/list/inspect/call/serve/demo/--version，
   包括输出、错误流、退出码和安全默认值。
6. **Demo、模板、文档**：维护同等 demo，生成项目端到端可运行，所有 README 示例在 CI
   中执行，并补齐中英文文档。
7. **差分验证与发布**：每个 Python 测试模块均有映射；从干净 checkout 做 npm/Go 外部
   consumer 测试、真实 SDK/传输测试、格式/静态/竞态检查和包检查，最后 Sol 逐项验收。

每个阶段都有明确退出证据；16 条 conformance rules 只属于第三阶段的一部分，不能再
单独代表产品完成。

## 自动化与职责

Sol 负责基准、清单、架构决策、差分测试设计和最终验收；Terra 每次实现一个有边界的
清单切片，并同时提交测试、文档和原子 commit；Luna 仅做有明确 oracle 的机械工作。
CI 发布清单覆盖报告，缺少架构、consumer、场景或测试证据即拒绝合并。除 Python 未定义
的产品语义、registry 所有权、凭证或正式发布授权外，流程不需要人为介入。

立即开始的切片是：从 3b27442 生成 Python 产品与测试映射账本，加入覆盖校验器，随后
按清单设计 TS/Go facade 并先重构 TypeScript。当前不得推送、打 tag、发布，或称两个
绑定为完整版本。
