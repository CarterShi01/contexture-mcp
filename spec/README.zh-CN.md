# Contexture 语言无关规范

[English](README.md) · [中文项目首页](../README.zh-CN.md)

`spec/` 描述所有语言 Binding 必须保持的语义，而不是要求其他语言复刻 Python
的类继承或反射方式。

```text
fixtures/       语言无关的声明与选择输入
golden/         Python 参考应用产生的精确协议输出
model.md        Application、节点、编译与 Host 表面的规范模型
conformance.md  每个 Binding 必须满足的行为规则
bindings.md     各语言可采用的编写与 Schema 策略（非规范）
```

核心不变量包括：惰性 Application；Role/Skill/Tool 闭合集；Schema 与调用由
同一 Binding 管理；四工具固定 MCP 网关；Prompt-only roots；完整根树、单调
收窄的请求选择；运行时与独立 disclosure-only 编译分离；身份与 Telemetry
按调用隔离；REST 只能显式发布 Tool 白名单。

`golden/` 是字节级契约。修改它之前必须确认协议行为确实要改变，然后运行：

```bash
uv run --extra dev python tests/golden.py --update
git diff -- spec/golden
```

逐项审核差异，不能为了让测试变绿而机械更新。

当前仍然只发布 Python Binding。
[TypeScript](https://github.com/CarterShi01/contexture-mcp-typescript) 与
[Go](https://github.com/CarterShi01/contexture-mcp-go) 仓库目前是带发布保护的工程
骨架，不是可安装实现；`bindings.md` 中的 PHP 仍然只代表兼容设计目标。
