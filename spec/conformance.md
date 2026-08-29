# Contexture conformance

An implementation conforms when it can build the reference fixtures and
produce the payloads under `golden/` according to the following rules.

1. Register every root exactly once and reject duplicate root names, duplicate
   node identity, containment cycles, ambiguous names, and unresolved `uses`
   references on any node.
2. Compile an immutable address index before serving. The compilation derives
   one Tool binding per Tool.
3. Keep the MCP tool plane fixed to the gateway names in the golden fixtures.
   Business Tools are disclosed inside gateway payloads, never listed as MCP
   tools themselves.
4. Implement discover, open, read-only invoke, and invoke with the exact
   successful payload and recovery behavior represented by `golden/`.
5. Open Channels before serving the first request and close it after the last;
   a failed open must unwind resources already acquired.
6. Treat Application declaration as lazy. A port may use classes, structs,
   interfaces, or schema objects, but importing declarations must not create a
   served forest or external connection.
7. Derive parent, members, uses, and reverse dependents from the compiled graph
   for explicit introspection Tools. Do not implicitly disclose reverse edges
   that cross into a branch the caller has not entered.
8. Record Role/Skill entry and Tool invocation through a telemetry side channel.
   Telemetry must not enter disclosure payloads, and reporting failure must not
   change the result or exception of the business call being observed.

Python type-hint reflection is not normative. A Go input struct, a TypeScript
schema, or a PHP DTO is conformant when it yields the same Tool schema and
validated invocation behavior.
