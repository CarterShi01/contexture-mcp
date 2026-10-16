# Contexture in two minutes

[简体中文](overview.zh-CN.md) · [Documentation](README.md) · [Project](../README.md)

**One model for application structure and agent interaction.**

[![Role internals and the recursive application capability graph. Open the overview video.](media/contexture-overview.png)](media/contexture-overview.mp4)

**[Open or download the video](media/contexture-overview.mp4)** — 1:59, 1080p,
approximately 4.5 MiB. If your Markdown viewer cannot play it, download the MP4
and open it in a video player. [English captions](media/contexture-overview.en.srt)
and [Chinese subtitles](media/contexture-overview.zh-CN.srt) are available.

## What the pictures explain

The cover's left panel shows one Role: local guidance, Skills, Tools, and child
Roles. Its right panel shows those responsibility boundaries composed into an
application. These are **a detail view and a whole-application view**, not a
before/after comparison. Containment is not inheritance or automatic delegation.

The film connects that structure to discovery, inspection, guidance disclosure,
and explicit Tool execution. It visualizes implemented runtime and architecture;
it is not an autonomous model-run recording or a performance benchmark.
Modern MCP frameworks and hosts can also provide modularity and on-demand
discovery; see [the design discussion](design.md) for the specific choice here.

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
