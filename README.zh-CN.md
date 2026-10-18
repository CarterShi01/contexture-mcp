# Contexture

<!-- markdownlint-configure-file {"MD033": {"allowed_elements": ["a", "details", "summary", "strong"]}} -->

**一套模型，连接应用结构与 Agent 交互。**

Contexture（`contexture-mcp`）是用于构建 MCP 服务与 AI 原生应用的应用层框架。
将职责、指导与带类型的动作组合为能力图；同一套声明既组织应用，也生成 Agent
可以导航的接口。

[文档](docs/README.zh-CN.md) · [快速开始](#快速开始) ·
[Demo](contexture/demo/README.zh-CN.md) · [变更记录](CHANGELOG.md) · [English](README.md)

[![PyPI](https://img.shields.io/pypi/v/contexture-mcp)](https://pypi.org/project/contexture-mcp/)
[![Python 3.11–3.14](https://img.shields.io/badge/python-3.11%E2%80%933.14-blue)](pyproject.toml)
[![Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

https://github.com/user-attachments/assets/0fe9b212-5353-4dc4-bba4-7271927f5a01

*1 分 59 秒 · 以动画解释已实现的架构。[文字稿与字幕](docs/overview.zh-CN.md)
· [下载视频](docs/media/contexture-overview.mp4?raw=1)。*

## 为什么使用 Contexture？

不断增长的应用不仅需要可调用的工具，还需要职责边界、可复用指导，以及开发者
能够维护的结构。

- **按职责组合。** Role 将局部指导、Skill、带类型 Tool 和子 Role 放在一起，
    通过显式引用复用能力。
- **声明一次，逐步披露。** 经过检查的声明驱动能力图、导航、指导和 Schema。
    Agent 按需打开细节，再显式调用 Tool。
- **保留业务服务。** 适配已有代码，不重复实现；MCP 与显式 REST 路由共用执行和验证。

Contexture 是 Controller 层，不是 Agent 循环或工作流引擎。当职责与过程需要共同
演进时，它提供统一的组织方式；少量独立工具通常直接暴露更简单。
[了解设计与取舍 →](docs/design.zh-CN.md)

<a id="安装"></a>
<a id="五分钟创建应用"></a>

## 快速开始

需要 Python 3.11–3.14 和 [uv](https://docs.astral.sh/uv/)。
创建本地应用并调用第一个 Tool，不需要模型或凭据：

```bash
uv tool install contexture-mcp
contexture new hello-context
cd hello-context
uv sync
uv run contexture check
uv run contexture call hello-context-assistant/ping --input '{"target":"example.test"}'
```

```text
example.test: reachable (placeholder)
```

接着按[教程](docs/handbook.zh-CN.md)检查、扩展生成的应用，或体验
[Kubernetes Demo](contexture/demo/README.zh-CN.md)。已有项目可使用
`uv add contexture-mcp` 添加依赖；固定当前 1.0 版本使用 `contexture-mcp==1.0.0`。

<a id="连接-mcp-host"></a>
<details>
<summary><strong>连接 Claude Code 或 Codex</strong></summary>

在 `hello-context` 中选择一个命令，让 Host 启动 stdio 服务：

```bash
# Claude Code：项目范围注册
claude mcp add --scope project hello-context -- uv run contexture serve

# 或 Codex：修改用户配置
codex mcp add hello-context -- uv run contexture serve
```

从该项目启动所选 Host 并确认 MCP 连接。配置范围、HTTP 与 REST 见
[托管指南](docs/guides/hosting.zh-CN.md)。

</details>

<a id="一套声明词汇"></a>

## 应用模型

脚手架已经是一个小型能力图：

```text
hello-context-assistant       Role：职责与局部指导
├── check-target             Skill：引用 ping 的过程
└── ping                     Tool：带类型的可执行代码
```

`Role` 包含能力与子 Role；`Skill` 描述过程；`Tool` 执行业务代码。
显式 `uses` 引用连接能力，Tool 类型签名生成 Schema 并验证调用。

**[查看完整 Python 声明 →](docs/guides/authoring.zh-CN.md)**

<a id="渐进式公开"></a>

运行时提供五个 MCP 网关入口，承担发现、检查、打开和读写调用。
Open 披露指导与 Schema，Invoke 执行 Tool；业务授权仍由应用与 Host 负责。
[了解披露过程 →](docs/guides/hosting.zh-CN.md#网关与披露)

<a id="深入了解"></a>

## 文档

| 我想…… | 阅读 |
| --- | --- |
| 构建和扩展第一个应用 | [入门教程](docs/handbook.zh-CN.md) |
| 组合 Role、Skill 与 Tool | [应用声明指南](docs/guides/authoring.zh-CN.md) |
| 连接 Host 或提供 MCP／REST | [托管指南](docs/guides/hosting.zh-CN.md) |
| 理解设计及其他方案 | [设计与取舍](docs/design.zh-CN.md) |
| 探索可运行示例 | [事故 Demo](contexture/demo/README.zh-CN.md) |
| 查看后续方向 | [路线图](docs/roadmap.zh-CN.md) |

[文档索引](docs/README.zh-CN.md)区分教程、操作指南、设计解释与参考资料。
本仓库提供 Python 实现；另见 [TypeScript](https://github.com/CarterShi01/contexture-mcp-typescript)
与 [Go](https://github.com/CarterShi01/contexture-mcp-go)。

<a id="本地开发闭环"></a>
<a id="面向人的-rest-路由"></a>
<a id="公共-api"></a>

在找原首页章节？[开发闭环](docs/guides/authoring.zh-CN.md#开发闭环)、
[REST 示例](docs/guides/hosting.zh-CN.md#面向人的-rest-路由)与
[API 边界](docs/guides/authoring.zh-CN.md#公共-api-边界)现已归入指南。

<a id="参与开发"></a>

## 参与贡献

欢迎问题报告、文档改进和聚焦的代码贡献。请先阅读[贡献指南](CONTRIBUTING.md)，
通过 [Issue](https://github.com/CarterShi01/contexture-mcp/issues)讨论变更。
安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 许可证

Apache-2.0，详见 [LICENSE](LICENSE)。
