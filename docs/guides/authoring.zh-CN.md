# 应用声明指南

[English](authoring.md) · [文档导航](../README.zh-CN.md) · [入门教程](../handbook.zh-CN.md)

本页是声明模型的简明参考。从安装、生成项目到首次修改的完整路径，见
[应用开发手册](../handbook.zh-CN.md)。

## 完整声明示例

下面将职责、可复用过程与带类型动作组合在一起。独立示例使用 `operations` ref，
而 CLI 脚手架使用 `hello-context-assistant` ref；它不会自动替换生成的文件。

```python
from contexture import Contexture, Role, Skill, Tool


class CheckStatus(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="check-status",
            description="返回一个服务的状态。",
            read_only=True,
        )

    async def invoke(self, service: str) -> dict[str, str]:
        return {"service": service, "status": "ready"}


class Diagnose(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="diagnose",
            description="诊断异常服务。",
            instructions="先读取状态，再根据证据进行解释。",
            uses=("operations/check-status",),
        )


class Operations(Role):
    def __init__(self) -> None:
        super().__init__(
            name="operations",
            description="处理服务运维任务。",
            instructions="提出变更前必须先检查现状。",
            skills=[Diagnose()],
            tools=[CheckStatus()],
        )


app = Contexture(name="service-operations", roots=(Operations,))
```

Contexture 不从类名或 docstring 推导公开名称与描述。Tool 的 `invoke()` 类型标注
同时生成输入 Schema 并验证调用，不需要分别维护两套定义。

## 声明词汇

| 概念 | 编写方式 | 含义 |
| --- | --- | --- |
| `Contexture` | 一个应用值 | 惰性组合根 |
| `Role` | 子类与构造函数 | 职责和包含关系边界 |
| `PreProcess` / `PostProcess` | Role 特化与构造函数 | 可选的准备与收尾过程及专属能力 |
| `Skill` | 子类与构造函数 | 模型遵循的过程 |
| `Tool` | 子类与带类型的 `invoke()` | Contexture 执行的确定性代码 |
| `Prompt` | 子类与构造函数 | 用户主动触发、指向已有节点的入口 |
| `Resource` | 子类与构造函数 | Host 可读、由已有只读 Tool 支持的 URI |
| `Channels` | 可选子类 | 共享外部依赖及其生命周期 |

Role、Skill、Tool 构成能力图。Prompt 和 Resource 为已有 ref 提供另一种协议入口，
不复制节点。只允许用户控制的 Prompt 平面进入的完整树，应放在 `prompt_roots` 中。

稳定的 1.0 API 允许 Role 声明 `pre_process` 与 `post_process`。ACTIVE 在不改变
业务 `Role.instructions` 的前提下组合可识别的框架指令块，指出开始或结束前要打开
的 ref。它们是单独披露的过程，不是自动执行的回调。`binding_instruction` 标记
应用自身声明、无法用代码强制的规则。参见[过程成员示例](../handbook.zh-CN.md#可选过程成员contexture-10)
与 [ADR 023](../adr/023-process-members-and-instruction-emphasis.md)。

## 开发闭环

用 `contexture check` 校验、`contexture list` 查找规范 ref、`contexture inspect`
查看生成的披露内容、`contexture call` 执行一个本地 Tool。CLI inspect 可以重放
打开 ref 的内容，与只返回导航卡片的 MCP 工具 `contexture_inspect` 不同。

从[生成项目的命令](../handbook.zh-CN.md#4-完成本地开发闭环)开始，再学习
[把能力放在正确位置](../handbook.zh-CN.md#6-把能力放在正确位置)。外部依赖使用
[Channels](../handbook.zh-CN.md#7-只在需要时连接外部系统)。

## 公共 API 边界

业务声明从 `contexture` 导入，高级托管从 `contexture.server` 导入。导出集合有
回归快照；`contexture.core` 和具体 server 子模块属于实现细节，不承诺兼容。
[规范](../../spec/README.md)定义跨语言契约；[托管指南](hosting.zh-CN.md)说明 MCP
与 REST 接口。
