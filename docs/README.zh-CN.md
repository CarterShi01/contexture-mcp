# 文档导航

[English](README.md) · [项目首页](../README.zh-CN.md)

## 从这里开始

| 你想了解什么 | 从这里开始 |
| --- | --- |
| 第一次使用？ | [快速开始](../README.zh-CN.md#快速开始)，然后进入[应用教程](handbook.zh-CN.md) |
| 想先看看效果？ | [两分钟视频](overview.zh-CN.md) |
| 没有外部基础设施也能试用吗？ | [内置确定性事故 Demo](../contexture/demo/README.zh-CN.md) |

## 教程：边做边学

[应用开发手册](handbook.zh-CN.md)是完整操作路径：安装 → 生成 → 查看 → 调用 →
扩展 → 连接 Host。

- [理解生成的应用](handbook.zh-CN.md#3-理解生成的应用)。
- [完成第一次修改](handbook.zh-CN.md#5-完成第一次修改)。
- [添加能力](handbook.zh-CN.md#6-把能力放在正确位置)。

## 操作指南：完成具体任务

- [应用声明](guides/authoring.zh-CN.md)：完整 Python 示例、声明词汇、开发命令和 API 边界。
- [托管](guides/hosting.zh-CN.md)：Claude Code／Codex、MCP 网关、HTTP 与 REST。
- [外部依赖](handbook.zh-CN.md#7-只在需要时连接外部系统)：Channels 与生命周期。
- [Prompt 和 Resource](handbook.zh-CN.md#8-添加可选入口)：已有能力的其他入口。
- [安全与故障处理](handbook.zh-CN.md#10-本地安全与故障处理)。

## 设计解释：理解选择

- [设计与取舍](design.zh-CN.md)：职责组合、渐进披露、其他方案与适用场景。
- [视频与文字稿](overview.zh-CN.md)：已实现的交互模型。
- [路线图](roadmap.zh-CN.md)：计划中的可视化与能力级评估。

## 参考资料：查询契约

- [语言无关规范](../spec/README.md)：规范模型、一致性规则、fixture 与 golden 输出。
- [变更记录](../CHANGELOG.md)：按版本记录变化。

## 维护者与贡献者

- [贡献指南](../CONTRIBUTING.md)：架构边界和必要检查。
- [安全政策](../SECURITY.md)：私下报告安全问题。
- [发布指南](../RELEASING.md)：打包与发布流程。
- [项目状态](project-status.md)：带日期的发布准备记录，不替代对已安装版本或当前
  发布证据的检查。

### 设计历史与验证

[ADR](adr/) 及带编号的设计、执行计划文档保留过去决策的原因。
请注意日期与版本：提案和旧图示不一定代表当前 API。当前行为以手册和规范为准，
不通过静默重写历史文档来消除版本差异。

- [Controller 框架与 MVC](05-controller-framework-and-mvc.md)：架构设计依据。
- [历史 Atlas](atlas/index.html)：v0.5 可视架构，不是当前 1.0 参考。
- [Host 验证](verification/hosts.md)：特定版本的历史运行及边界，不表示所有当前
  Host 或版本都已测试。
