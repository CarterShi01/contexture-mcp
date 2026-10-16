# 两分钟了解 Contexture

[English](overview.md) · [文档导航](README.zh-CN.md) · [项目首页](../README.zh-CN.md)

**一套模型，连接应用结构与 Agent 交互。**

[![Role 内部结构与递归应用能力图；点击打开介绍视频。](media/contexture-overview.png)](media/contexture-overview.mp4?raw=1)

**[打开或下载视频](media/contexture-overview.mp4?raw=1)**：1 分 59 秒，1080p，约 4.5 MiB。
如果 Markdown 阅读器无法播放，可下载后用视频播放器打开。
提供[英文字幕](media/contexture-overview.en.srt)与[中文字幕](media/contexture-overview.zh-CN.srt)。

## 图示说明什么

封面左侧展示一个 Role：局部指导、Skill、Tool 和子 Role；右侧展示这些职责边界
如何组合为应用。两者是**局部与整体视图**，不是改造前后的比较。
包含关系不代表继承或自动委派。

视频将这种结构连接到发现、检查、指导披露与显式 Tool 执行。
它以动画解释已经实现的运行时和架构，不是自主模型运行录屏，也不是性能基准。
现代 MCP 框架和 Host 同样可以提供模块化与按需发现；这里的具体架构选择见
[设计说明](design.zh-CN.md)。

## 亲手体验

按照[内置事故 Demo](../contexture/demo/README.zh-CN.md)，查看真实能力图，
通过 MCP 调用确定性的 Tool，无需 Kubernetes 集群。Demo 的 Kubernetes
固定场景不是视频中服务运维图示的逐帧重放；两者使用同一套声明与运行时模型。
创建自己的应用请阅读[中文手册](handbook.zh-CN.md)。

## 旁白中文译文

以下对应英文视频；英文原文见[English transcript](overview.md#transcript)。

### 00:00 — 一个应用模型

contexture-mcp 连接 Agent 宿主与应用服务。一套模型，用于应用结构与 Agent 交互。

### 00:11 — 从工具到能力

MCP 暴露工具供 Agent 调用。随着应用增长，扁平工具列表越来越难以导航和维护。
如果 Agent 可以像面向对象程序与对象交互一样，与具有明确职责、指导和工具的
能力交互呢？先探索它的指导，再调用它的工具。

### 00:36 — 运行时调用过程

下面逐步展示 Contexture 如何实现这种交互。Discover 展示起点；Inspect 比较分支，
但不加载其指令。Agent 先打开服务运维，再打开事故响应。选中的分支展示指导和
工具定义，无关细节仍未披露。Agent 显式调用只读工具，检查健康状态和发布历史。
探索能力与执行业务代码始终分开。这就是渐进式上下文披露。

### 01:15 — 按职责组合

这种结构帮助开发者组织复杂应用。每个容器包含职责、局部指令和成员，子容器形成
层级。Skill 描述过程，Tool 连接可执行代码。将这些对象组合到网关中，Agent 就能
按目的导航应用。业务服务保留自己的逻辑和授权。一套模型，将 Agent 如何探索
与应用如何构建连接起来。

### 01:50 — 使用 Contexture 构建

构建在需要时披露相关内容的 MCP 服务。这就是 contexture-mcp。

[媒体信息与制作说明](media/README.md) · [设计与取舍](design.zh-CN.md)
