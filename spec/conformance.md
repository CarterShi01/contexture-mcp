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
5. The MCP model-controlled Tool plane equals the five-entry gateway in `golden/tools.json`.
   No business Tool is registered there.
6. Discover, inspect, open, read-only invoke, and writing invoke reproduce the successful
   payloads and recovery behavior under `golden/`.
7. Discover returns model roots only. Model-controlled open/invoke cannot enter
   `prompt_roots`; user-controlled Prompt navigation can.
8. Prompt, Resource, completion, instructions, and signposts reproduce the
   corresponding golden files. A Resource targets only an argument-free,
   read-only Tool.
9. A resolved SurfaceSelection is exact, non-empty, and reduced to a minimal
   antichain. Exact deep refs and terminal direct-child `*` patterns resolve
   against the canonical Index; `*` never crosses `/`. Every protocol door and
   invocation graph observes the same effective selection. Concurrent
   selections cannot affect one another.
10. Every resolved ref becomes a surface root with its complete subtree. Its
    unselected ancestors and siblings remain hidden, and a `uses` edge does not
    widen the selection.
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
17. Inspect accepts an atomic bounded batch of unique refs, preserves request
   and declaration order, enforces open-equivalent visibility, and returns
   only pure routing cards for targets, direct members, and declared uses. It
   discloses no instructions, Tool execution facets, Publication contract,
   content, result, or recursive expansion, and invokes nothing.

The JSON files under `golden/` are byte-level protocol fixtures, not examples to
reinterpret. When a binding cannot express an incidental JSON ordering detail,
it must still produce semantically identical JSON and exact string values.

## 0.14 Publication extension obligations (separate from legacy rules)

The original 16 numbered rules above are unchanged by this extension.
The legacy fixture/golden inventories remain pinned to 0.12; this section adds
no fixture or golden file and does not upgrade legacy port conformance claims.
The normative extension is [Optional Role Publication](model.md#optional-role-publication-014-extension).
It is accepted for Python 0.14.0, not evidence of TypeScript or Go parity.

Focused Python evidence is in `tests/test_publication.py`; runtime and MCP
integration evidence is in `tests/test_publication_integration.py`. A binding
must supply and pass its own evidence before claiming these obligations
implemented; this document does not replace execution evidence.

- **Declaration and compatibility:** the exported Publication is a Role with
  the inherited constructor, `kind="role"`, and `group="roles"`. Only a
  constructed Publication or `None` is accepted as `Role.publication`.
  Absence preserves exact legacy ROUTE/ACTIVE payloads and instructions, with
  no designation, flag, no-op, or obligation.
- **Containment and lifecycle:** member order is children, Publication, Skills,
  Tools; the designation does not add a branch. Cross-kind name collisions,
  shared instances, cycles, and unresolved refs are rejected. Paths, Channels,
  and runtime Tool Bindings are derived normally throughout the subtree;
  disclosure-only compilation remains unbound. Nested Publications obey the
  same rules.
- **Composition and laziness:** ACTIVE shows the ordinary Publication card in
  `roles` and its exact ref as a `publication` string. Business
  `Role.instructions` remains unchanged; framework composition appends the
  contract using the real ref and existing open gateway. Publication
  instructions and capabilities remain unopened. ROUTE gains no designation
  or contract.
- **Instruction meaning:** the contract requires opening the Publication
  before finishing owner work and following its procedure with results and
  evidence. It distinguishes opening from execution/success and requires
  truthful pending-approval, blocker, and failure reporting without invented
  success or approval bypass.
- **Inheritance and isolation:** business constructors can supply fresh
  Publication defaults and explicitly disable them with `None`. There is no
  containment-tree inheritance or sharing of node instances across owners or
  compilations. Opening a Publication directly adds no framework obligation
  unless it explicitly has its own Publication.
- **Surface boundaries:** owner selection includes its Publication; selecting
  the Publication alone hides the owner. `uses` cannot widen selection.
  Prompt-only owners and their Publications remain hidden from ordinary
  model-controlled disclosure. Composing an unavailable Publication card is
  refused without leaking its ref or emitting a dangling instruction.
- **Execution boundary:** the four runtime gateways remain unchanged. Opening
  executes no business Tool or finish handler; only explicit Tool execution
  performs effects under the existing approval and authorization rules.
  Instruction evidence is not a guarantee of external Agent compliance.
