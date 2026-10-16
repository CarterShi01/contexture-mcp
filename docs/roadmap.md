# Roadmap

[简体中文](roadmap.zh-CN.md) · [Documentation](README.md) · [Design](design.md)

Contexture's next directions build on its responsibility-based structure and
explicit capability graph: make agent-facing applications easier to understand,
evaluate, and evolve. These are **planned directions, not shipped features or
release commitments**. Scope should follow actual application needs.

## 1. Human–agent intent alignment for application code

As agents build and modify application code, human understanding and review
can become a bottleneck. We plan capability-graph visualization and disclosure
walkthroughs that connect intended responsibilities and relationships to the
context agents receive and the capabilities they use.

The current foundation is the compiled graph and existing `check`, `list`, and
`inspect` commands. A useful first step would render that actual structure and
its disclosure payloads, rather than introduce another manually maintained model.
A graph display would help review structure; it would not prove that human and
agent intent are semantically identical.

## 2. Capability-level evaluation and debugging

Correct code alone does not establish reliable agent behavior. Changes to a
model, description, instruction, or application structure can change outcomes.
We plan evaluation and debugging around capability nodes and navigation paths,
using explicit responsibility boundaries to help locate failures and improve
descriptions, Skill instructions, and graph topology.

Existing graph checks and runtime tests cover structural and execution
contracts. They do not replace task-level evaluation. Future diagnostics should
use observable tool calls, disclosed context, and task outcomes, and complement
end-to-end tests and regression checks—not equate a valid graph with a
successful task or assume access to hidden model reasoning.

## Keep one foundation

The ambition is for the capability graph to support application design, human
understanding, and evaluation-driven improvement. This does not require a new
agent loop or moving business authorization into Contexture.

Useful feedback describes an application task, the relevant capability
structure, what was hard to understand or debug, and an observable expected
outcome. Share a sanitized example in
[an issue](https://github.com/CarterShi01/contexture-mcp/issues), without
credentials or private data. Release mechanics and historical readiness checks
remain separate in [project status](project-status.md) and [releasing](../RELEASING.md).
