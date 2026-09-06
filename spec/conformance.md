# Contexture conformance

An implementation conforms when it maps the language-neutral fixtures to its
own declaration syntax and satisfies these rules and the exact golden outputs.

1. Application construction is lazy and opens no external resource.
2. Compilation builds fresh nodes, registers every ordinary and Prompt root
   exactly once, and freezes one canonical address Index before serving.
3. Compilation rejects empty applications, invalid names, duplicate or
   ambiguous identities, containment cycles, and unresolved `uses` refs.
4. One runtime Tool Binding owns both the disclosed input schema and validated
   invocation. Language-specific reflection is not normative.
5. The MCP model-controlled Tool plane equals the gateway in `golden/tools.json`.
   No business Tool is registered there.
6. Discover, open, read-only invoke, and writing invoke reproduce the successful
   payloads and recovery behavior under `golden/`.
7. Discover returns model roots only. Model-controlled open/invoke cannot enter
   `prompt_roots`; user-controlled Prompt navigation can.
8. Prompt, Resource, completion, instructions, and signposts reproduce the
   corresponding golden files. A Resource targets only an argument-free,
   read-only Tool.
9. A resolved RootSelection is exact, non-empty, and root-level. Every protocol
   door and invocation graph observes the same effective selection. Concurrent
   selections cannot affect one another.
10. Selecting one root preserves its complete subtree and immediate sibling
    groups. A cross-root `uses` edge does not widen the selection.
11. A caller-requested selection can only attenuate an identity-derived ceiling.
    The framework does not treat selection as business authorization.
12. Channels opens before runtime serving and closes afterward. Failed open
    unwinds acquired resources; declaration-only checks do not open Channels.
13. A disclosure-only application has an independent unbound Index, no
    execution, no Resources or Channels, no Tool input schemas, and only the
    navigation gateway plus selected Prompts.
14. REST publication, when implemented, is an explicit Tool-ref allowlist over
    the same runtime Binding. It rejects read/write method mismatches and never
    accepts a caller-supplied arbitrary ref or Principal.
15. Request identity is task-local. Telemetry is out of disclosure, and a
    telemetry failure cannot replace the observed business result or error.
16. Core declarations do not depend on the MCP or HTTP SDK used by a Host
    adapter.

The JSON files under `golden/` are byte-level protocol fixtures, not examples to
reinterpret. When a binding cannot express an incidental JSON ordering detail,
it must still produce semantically identical JSON and exact string values.
