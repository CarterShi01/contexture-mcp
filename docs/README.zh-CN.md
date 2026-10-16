# 文档导航

[English](README.md) · [项目首页](../README.zh-CN.md)

## 理解并体验

| 你想了解什么 | 从这里开始 |
| --- | --- |
| Contexture 是什么？ | [两分钟图解介绍与文字稿](overview.zh-CN.md) |
| 为什么使用同一应用模型？是否适合我？ | [设计、其他方案与取舍](design.zh-CN.md) |
| 没有外部基础设施也能试用吗？ | [内置确定性事故 Demo](../contexture/demo/README.zh-CN.md) |
| 如何构建和扩展自己的应用？ | [应用开发手册](handbook.zh-CN.md) |
| 下一步计划是什么？ | [路线图](roadmap.zh-CN.md) |

手册仍然是逐步操作路径；介绍与设计文档解释模型，不再建立另一套重复教程。
本仓库提供 Python 参考实现。其他语言实现的链接本身不表示版本或验证结果完全相同。

## 参考与贡献

- [语言无关规范](../spec/README.md)：规范模型、一致性规则、fixture 与 golden 输出。
- [变更记录](../CHANGELOG.md)：按版本记录变化。
- [贡献指南](../CONTRIBUTING.md)：架构边界和必要检查。
- [安全政策](../SECURITY.md)：私下报告安全问题。
- [发布指南](../RELEASING.md)：打包与发布流程。
- [项目状态](project-status.md)：带日期的发布准备记录，不替代对已安装版本或当前
  发布证据的检查。

## 设计历史与验证

[ADR](adr/) 及带编号的设计、执行计划文档保留过去决策的原因。
请注意日期与版本：提案和旧图示不一定代表当前 API。当前行为以手册和规范为准，
不通过静默重写历史文档来消除版本差异。

- [Controller 框架与 MVC](05-controller-framework-and-mvc.md)：架构设计依据。
- [历史 Atlas](atlas/index.html)：v0.5 可视架构，不是当前 1.0 参考。
- [Host 验证](verification/hosts.md)：特定版本的历史运行及边界，不表示所有当前
  Host 或版本都已测试。
