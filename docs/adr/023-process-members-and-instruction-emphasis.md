# ADR 023 — Process members, and how framework instructions announce themselves

**Status:** accepted

**Date:** 2026-09-09

**Supersedes:** [ADR 021](021-role-publication-and-instruction-composition.md).
**Extends:** [ADR 013](013-a-constructor-is-the-declaration.md),
[ADR 016](016-register-compile-disclose.md), and
[ADR 020](020-path-selectors-promote-subtrees.md).

## Context

Two problems arrived together, and they are answered together because the
answer to the second is what makes the first safe to generalize.

**A Role has two moments, and 0.14 named only one.** ADR 021 introduced
`Publication`: a specialized Role holding the procedure and equipment for
turning work into durable results, designated by an owner and disclosed only
when opened. The mechanism was never really about publishing. It was about a
Role needing a *dedicated, initially collapsed capability subtree for one phase
of its work*, plus a framework-composed reminder that the phase exists — so
that the reminder cannot be forgotten by whoever writes the next business
constructor. Preparation is the same shape: a Role may need checks or setup
performed, with their own Tools, before its work begins. Writing that as the
first sentence of `Role.instructions` is possible but loses both properties
that made `Publication` worth having — the procedure stays expanded in every
disclosure of the owner, and the reminder is again something a business author
must remember to write.

**Everything this framework composes is a suggestion an agent may skim.**
Disclosure is not enforcement, and this package deliberately has no agent loop
and no signal for work starting or finishing. What it does control is the text
it composes into a payload, and the text was doing nothing to distinguish
itself. `Role.instructions` arrived as business prose with a framework
paragraph concatenated onto it under a small `Publication (framework
contract):` prefix — two authorities, one indistinguishable block. Under a long
session, a weaker model, or an owner whose own instructions are several
paragraphs, the sentence that must be obeyed reads exactly like the sentence
that may be adapted.

The second problem bounds the first: generalizing designated members is only
responsible if the composed obligations are legible as obligations, and if the
generalization does not invite applications to express in prose what belongs in
code.

## Decision

### PreProcess and PostProcess replace Publication

`Publication` is removed. `Role` holds two optional designated members:

```python
pre_process: PreProcess | None = None
post_process: PostProcess | None = None
```

`PreProcess` and `PostProcess` are specialized Roles, exactly as `Publication`
was: `kind="role"`, `group="roles"`, the same inherited constructor, the same
registration, the same ordinary containment. The closed node set remains Role,
Skill, and Tool. `PostProcess` is `Publication` renamed and nothing else;
`PreProcess` is its symmetry.

The rename is a clean break with no compatibility alias. `Publication` named
one application's use of the mechanism rather than the mechanism, and keeping
both names would leave two words for one thing in a vocabulary whose whole
value is that there is one word for each thing. Applications keep whatever
names they like for their own subclasses: a business class may still be called
`TaskPublication` and subclass `PostProcess`, because a subclass name is the
application's vocabulary and has no effect on anything this package composes.

`members()` yields, in order: `pre_process`, children, `post_process`, skills,
tools — the order the work runs in. `branches()` still returns only children:
neither process member is an alternative way on from the owner, so neither
inflates a signpost or the bootstrap roster. Both remain real containment
members, addressable at their canonical paths and included in complete-subtree
selection, exactly as ADR 021 specified for `Publication`.

The slot is what decides the meaning, and the type is what protects the slot.
A `PreProcess` in `post_process` is refused at construction: the two differ
only in when the framework tells an agent to open them, so an ordinary Role or
the wrong process kind would compile and disclose perfectly well while saying
the wrong thing for the life of the deployment.

Nesting is ordinary containment with no added semantics. A `PostProcess` may
hold its own `post_process`, a `PreProcess` may hold a `pre_process`, and a
process member receives a framework contract only if it explicitly declares one
of its own. Containment never makes an owner's process member the process
member of its children.

### An ACTIVE owner is composed, not concatenated

`Role._compile_active` composes the disclosed instructions as:

```text
PreProcess contract     (only when pre_process is declared)

Role.instructions       (business text, unchanged, never wrapped)

PostProcess contract    (only when post_process is declared)
```

and adds `pre_process` / `post_process` **string** fields naming the actual
refs supplied by the view. Each designated member also appears as an ordinary
routing card in `roles`. Both contracts are refused rather than composed if the
member's card is not in the payload the view has already filtered, so a
contract can never name a ref the caller would be refused for opening, and the
refusal never leaks the hidden path.

The PreProcess contract instructs the agent to open the ref before starting the
owner's work **and to return to the owner's instructions afterwards**. This is
not decoration. A finishing contract is the end of the path; a preparing
contract is mid-path, and an agent that leaves for a preparation subtree has
pushed the owner's own instructions further back in exactly the way this
decision exists to counteract.

ROUTE and INSPECT are unchanged: neither designation nor contract appears at
either level, and a process member is an ordinary member card during
inspection ([ADR 022](022-inspect-is-a-non-activating-structural-view.md)).

### Framework instructions announce themselves, with one fixed shape

A new `core.emphasis` module owns how obeyed text looks, and only that:

```text
===== contexture-mcp framework instruction — binding, follow exactly =====
PostProcess:
>>> REQUIRED: Call contexture_open with ref='…' before finishing this role's work.
Opening it only discloses the procedure; it does not execute it or establish
success. …
===== end framework instruction — binding regardless of surrounding context =====
```

Three properties, each deliberate.

**The head and tail are fixed, not composed from the mechanism's name.** Every
framework-composed block, for every mechanism, in every application, crosses an
agent's context as the same shape. Recognition is the mechanism: a banner that
varied per call site would be something to read rather than something already
known. Which mechanism is speaking is named on the line below it instead.

**The required step is lifted out of the prose.** A contract is one imperative
wrapped in several sentences of consequence, and an agent that skims the
sentences has to find the imperative inside them. `>>> REQUIRED:` makes the
answer to *what am I being told to do* one line, and leaves the sentences to be
what they always were: the reasons. It is optional, because a constraint with
no step to sequence would only dilute the marker by filling the slot.

**The emphasis is sameness, not volume.** No exclamation marks, no capitals, no
sentence written twice. Two host limits make this a budget question as well as
a taste one — server instructions are truncated at 2KB by one host and read
self-contained for 512 bytes by another — and every character spent shouting is
a character not spent on the roster that says what the server is for.

`framework_instruction(kind, body, *, action=None)` composes this and is not
exported: the name `contexture-mcp` inside the banner is a claim only this
package may make.

### Applications may mark their own rules, under their own name

`binding_instruction(source, body, *, action=None)` is exported. It renders the
same shape with the application's own `source` as the banner, for a business
author marking a hard rule inside their own `instructions`. A `source` claiming
this framework's name is refused, because that name is how an agent tells a
contract of the framework from a decision of the application that may change
tomorrow.

Offering this is a positioning judgement rather than a convenience. Which
sentences matter is the application's knowledge and stays there; *how a
sentence that matters reaches an agent* is a property of the disclosure
protocol, which is this package's subject. It is the same split already made
for `read_only`, where the application declares a fact and the framework owns
the machinery — the two invoke doors, the hint, the refusal — that gives the
fact effect.

### The boundary this decision must not blur

A text marker is the weaker of the two tools an application has.

Where a rule can be enforced by the Tool that would otherwise break it, it
belongs there, refused with facts the way a writing Tool invoked through the
read-only door is refused. `PreProcess` makes it easy to express a hard
precondition as prose and get a framework-shaped banner around it, which would
be a worse outcome than having neither: unlike a finishing responsibility,
whose absence shows up later as results nobody preserved, a skipped preparation
leaves no trace at all. Both class docstrings, the handbook, and this decision
say the same thing: put a *procedure to perform* in a process member, and keep
an *invariant that must hold* in code.

## Consequences

- `contexture.Publication` no longer exists. Applications rename the base class
  and the `publication=` keyword; nothing else about their declarations changes.
- ACTIVE payloads for owners with a designated member change shape (`publication`
  becomes `post_process`, `pre_process` is new) and their composed instruction
  text changes wording. Applications without either member are unchanged, byte
  for byte.
- The public API grows by three names: `PreProcess`, `PostProcess`,
  `binding_instruction`. `framework_instruction` stays internal.
- No new node kind, gateway, callback, hook, or workflow runtime. The five
  fixed gateways, Tool Binding semantics, Prompt/Resource declarations, and
  audience rules are untouched.
- The marker text is a Python-implementation fact today. Go and TypeScript can
  express the same mechanism — a field on their Role struct or interface, a
  literal string in their compile step, no inheritance required — and should
  reproduce the banner verbatim when they follow, because an agent that has to
  learn a second shape per backend has learned nothing.

## Alternatives considered

- **Keep `Publication` and add `PreProcess` beside it.** Rejected: two words
  for one mechanism, one of them naming an application's use of it.
- **Keep a `Publication = PostProcess` alias for a release.** Rejected as
  explicitly not wanted; pre-1.0 the surface is snapshotted, not frozen, and a
  clean break is one migration rather than two.
- **Derive the banner label from the member's class name.** Rejected: it would
  make the wire text depend on an application's private naming, so renaming a
  business subclass would silently change what an agent reads.
- **Let `binding_instruction` default its `source`.** Rejected: an unnamed
  authority is the one thing the marker exists to supply.
- **Wrap the whole server `instructions` field in the same banner.** Rejected
  on measurement: the preamble already occupies ~497 of the 512 bytes one host
  reads as self-contained, and that field is wholly framework-authored anyway,
  so the banner would buy no disambiguation at real cost.
- **Emit the contract at ROUTE or INSPECT so it is seen earlier.** Rejected:
  evaluation is not adoption (ADR 022), and an obligation attached to a
  candidate is exactly the contamination that decision removed.

## Compatibility and evidence

`tests/test_process.py` covers declaration, validation, member order, both
compositions and their interaction, view-supplied refs, selection, and the
emphasis module including its refusal to be impersonated.
`tests/test_process_integration.py` covers the served MCP surface. The
normative extension is in [the public model](../../spec/model.md), with
obligations recorded in [conformance](../../spec/conformance.md). No Go or
TypeScript parity is claimed by this decision.

An instruction contract, however it is marked, is still not evidence that an
external agent will follow it.
