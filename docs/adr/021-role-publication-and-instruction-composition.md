# ADR 021 — Role Publication and instruction composition

**Status:** accepted and implemented in Python 0.14.0, 2026-09-08.
**Extends:** [ADR 013](013-a-constructor-is-the-declaration.md),
[ADR 016](016-register-compile-disclose.md), and
[ADR 020](020-path-selectors-promote-subtrees.md).

## Context

A business Role may need to turn its work into durable, reusable results before
finishing. That responsibility can have its own procedure, Skills, dedicated
Tools, and references to shared capabilities. Putting all of it in the owner's
instructions defeats progressive disclosure; repeating a reminder in every
business constructor makes the framework contract easy to omit.

Publication here means making results durable and reusable. It does not mean
publishing to the public Internet or automatically making anything user-visible.
The capitalized `Publication` is distinct from the existing lower-case
Prompt/Resource *publications*: those are exposure pointers to existing nodes,
not nodes or result-finalization procedures.

## Decision

### A specialized Role, not another node kind

Export `Publication` from `contexture`. It subclasses `Role`, keeps
`kind="role"` and `group="roles"`, and inherits the same constructor:
`name`, `description`, `instructions`, `children`, `skills`, `tools`, `uses`,
and `publication`. The closed node set remains Role, Skill, and Tool.

`Role` gains `publication: Publication | None = None`. It holds a constructed
member, such as `publication=TaskPublication()`, never the class
`publication=TaskPublication`. A plain Role or other node is not a substitute
for a Publication in this field. A business Publication uses an ordinary
constructor to author its procedure and compose its dedicated capabilities or
reference shared Tools through existing `uses` refs. There is no new node
protocol, callback, or workflow declaration language.

`None` means absence: no extra member, field in disclosure, instruction, or
obligation. It preserves the exact existing ROUTE and ACTIVE output. There is
no framework `enabled` flag, no no-op Publication, and no implicit default
construction when `None` is supplied.

### Containment and ordinary inheritance

`members()` yields children, then the optional Publication, then Skills and
Tools. The Publication shares the owner's immediate member namespace. Normal
registration validates names, cross-kind uniqueness, shared instances, cycles,
and refs; stamps paths and Channels; and derives normal runtime Tool Bindings
throughout its subtree. Disclosure-only compilation remains unbound.

`branches()` still returns only `children`. A designated Publication is
finishing equipment, not an alternative work branch, so it must not inflate
branch counts or the ordinary branch roster. It is nevertheless a real
containment member, addressable at its actual canonical path and included in
complete-subtree selection.

Containment does not make a parent's Publication the Publication of its child
Roles. Reuse and defaults come from ordinary Python inheritance and business
constructors. A base constructor can accept `Publication | None = None`; a
publishing subclass can distinguish an omitted argument from explicit `None`
with a typed business-local sentinel, constructing a fresh default only for
omission. See the [handbook example](../handbook.md#optional-publication-python-0140).
Never use a constructed node as a mutable default or reuse one across owners
or compilations. Share services through Channels or existing `uses` refs, not
graph-node instances.

A Publication may explicitly hold another Publication or compose child Roles,
including other Publications. This is ordinary acyclic containment, not a new
composition engine. Merely opening a Publication adds no special obligation
because of its type; it receives a framework publication contract only if it
explicitly holds its own `publication` member.

### Exactly two instruction owners

`Role.instructions` stays the business-authored text, unchanged. Framework
`Role._compile_active` composes the disclosed instructions by appending the
framework publication contract when a Publication is present. The instruction
ownership categories remain **business instructions** and **framework
instructions**. The Publication's procedure is business instructions, not a
third category or a second owner-instructions field.

The appended contract uses the existing `OPEN_TOOL` constant
(`contexture_open`) and the actual ref supplied by the view, not a guessed path
or a fixed member name. It tells the agent to open that Publication before
finishing the owner's work and follow its procedure using the work's results
and evidence. Opening alone is not execution or successful publication.
Pending approval, blockers, and failures must be reported accurately; the agent
must never invent success or bypass required approval.

An ACTIVE owner exposes an ordinary routing card for the Publication in its
`roles` array, after its child Role cards, and adds a `publication` **string**
whose value is that card's ref. For example, the relevant payload fields are:

```json
{
  "roles": [
    {
      "kind": "role",
      "name": "task-publication",
      "description": "Preserve task findings and continuation evidence.",
      "ref": "worker/task-publication"
    }
  ],
  "publication": "worker/task-publication"
}
```

This is a payload excerpt, not a golden fixture. The designation is not an
embedded object, a new `publications` group, or an eagerly opened subtree.
Publication instructions, Skills, and Tool schemas stay hidden until it is
opened. ROUTE compilation adds neither the designation nor the contract.

### Selection and audience remain authoritative

Selecting an owner selects its complete subtree, including its Publication.
Selecting the Publication alone promotes only that subtree and exposes neither
the owner nor an obligation to finish the owner's work. `uses` never widens
selection: a shared Tool must separately be inside the effective surface to be
disclosed or called.

Prompt-only owners and their Publications remain hidden from ordinary
model-controlled disclosure, even if a caller guesses a ref. Framework
composition must refuse an unavailable Publication card rather than leak its
path, silently drop the obligation, or emit instructions pointing outside the
current view. User-controlled Prompt navigation retains its existing audience
rules; this feature is not a new entrance to hidden trees.

### Disclosure is not execution or enforcement

The MCP runtime still has exactly four gateways: `contexture_discover`,
`contexture_open`, `contexture_invoke_read_only`, and `contexture_invoke`.
Only Tools execute. No hook, host-specific runtime, `Role.invoke`, finish
handler, destructor, or automatic execution is introduced. Contexture neither
detects that a Role's work has finished nor guarantees that an external Agent
will comply with instructions. Approval and authorization remain with the
existing host/application boundaries.

## OC use cases motivating the decision

These are application design examples, not an OC migration in this change.

**TaskPublication** consolidates findings, acceptance evidence, and continuation
information through the existing Work TaskContext storage. It preserves what
the next task needs and distinguishes completed work from pending approval,
blockers, and failures. Preparing inherited context from predecessor tasks
happens before the work; it is not publication and is not moved into this
finishing responsibility.

**ProjectKnowledgePublication** coordinates durable project knowledge by genre:
product, design, ADR, research, runbook, and reference. It first proposes a
change, then obtains authorized review. Only after the required approval may
it write approved content to Desk or apply it to project git and obtain a
commit receipt. The procedure must preserve Founder approval, not infer it from
Task completion or from opening the Publication.

A composite Publication may sequence task memory consolidation and then
consider project promotion under business-defined guards. Not every Task
completion deserves or requires project promotion. The business owns genre
selection, evidence thresholds, approval conditions, and storage Tools; this
decision introduces no universal workflow engine.

## Alternatives considered

- **Lightweight instructions plus `uses` only.** Rejected as the sole
  abstraction because the business publication procedure itself needs to
  compose Skills, dedicated Tools, and sometimes child responsibilities. A
  specialized Role already provides those semantics; `uses` remains useful for
  shared Tools.
- **Append the full publication procedure to every owner.** Rejected because
  it eagerly spends context and couples business procedure changes to every
  owner. Append only the framework contract and real ref.
- **Add a fourth node kind or a workflow runtime.** Rejected because existing
  Role composition, navigation, Tool execution, and authorization suffice.
- **Automatically inherit through the containment tree or execute on finish.**
  Rejected because composition is not Python inheritance and the framework has
  no reliable external-agent finish event. Neither can substitute for explicit
  business composition or approval.

## Compatibility and evidence

Applications without Publication must retain exact existing output and
behavior. Existing Prompt/Resource exposure declarations, gateway names, and
Tool Binding semantics do not change.

The normative extension is in [the public model](../../spec/model.md).
[Conformance](../../spec/conformance.md) records separate 0.14 Publication
obligations, with focused Python evidence in `tests/test_publication.py` and
runtime/MCP persistence and approval evidence in
`tests/test_publication_integration.py`. The original 16 numbered legacy rules and the
0.12 fixture/golden inventories remain unchanged by this decision. No new
neutral fixture or golden is introduced, and no TypeScript or Go parity is
claimed. An instruction contract is not evidence that an external agent will
follow it.
