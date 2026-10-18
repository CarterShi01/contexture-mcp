# Video overview

[简体中文](overview.zh-CN.md) · [Documentation](README.md) · [Project](../README.md)

**One model for application structure and agent interaction.**

<!-- GitHub expands this standalone attachment URL into an inline video player. -->
<!-- markdownlint-disable-next-line MD034 -->
https://github.com/user-attachments/assets/0fe9b212-5353-4dc4-bba4-7271927f5a01

**1:59 · 1080p.** Play here or on the [project homepage](../README.md).
[Download the MP4](media/contexture-overview.mp4?raw=1),
[English captions](media/contexture-overview.en.srt), or
[Chinese subtitles](media/contexture-overview.zh-CN.srt).

## What the pictures explain

The video follows an agent from discovering responsibilities to opening local
guidance and explicitly invoking Tools, then shows how Roles, Skills, Tools,
and child Roles compose an application. Its opening frame is the video preview,
not a separate cover. It visualizes implemented runtime and architecture;
it is not an autonomous model-run recording. For the design choices and
alternatives, see [the design discussion](design.md).

## Try the mechanism

Follow the [built-in incident demo](../contexture/demo/README.md) to inspect a
real capability graph and run deterministic Tools over MCP, without a cluster.
The demo's Kubernetes fixture is not a literal replay of the film's service
operations illustration. Both use the framework's declaration and runtime model.
For your own application, follow the [handbook](handbook.md).

## Transcript

The English narration below is preserved verbatim from the video.

### 00:00 — One application model

contexture-mcp connects agent hosts to application services. One model for
application structure and agent interaction.

### 00:11 — From tools to capabilities

MCP exposes tools for agents to call. As applications grow, flat tool lists
become harder to navigate and maintain. What if agents could interact with
capabilities the way object-oriented programs interact with objects—with a
clear responsibility, guidance, and tools? Explore its guidance. Then invoke
its tools.

### 00:36 — Runtime call flow

Here is how Contexture implements that interaction, illustrated step by step.
Discover reveals the starting points. Inspect compares branches without
loading their instructions. The agent opens Service Operations, then Incident
Response. The selected branch reveals its guidance and tool definitions;
unrelated details stay undisclosed. The agent explicitly invokes read tools
to check health and release history. Exploring a capability and executing
business code remain separate. That is progressive context disclosure.

### 01:15 — Responsibility-based composition

This structure helps developers organize complex applications. Each container
combines a responsibility, local instructions, and members. Child containers
build the hierarchy. Skills describe procedures; tools connect them to
executable code. Compose these objects into a gateway, and the agent can
navigate the application by purpose. Business services keep their own logic
and authorization. One model connects how the agent explores with how the
application is built.

### 01:50 — Build with Contexture

Build MCP servers that disclose what matters, when it matters. That's
contexture-mcp.

[Media details and credits](media/README.md) · [Design and trade-offs](design.md)
