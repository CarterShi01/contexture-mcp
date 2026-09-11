# ADR 013 — A constructor is the declaration

**Status:** accepted, v0.5.0
**Supersedes:** the class-body front end described in
[Design 02 §4](../02-framework-layers.md) through v0.4.

## Context

A business stated a capability by writing a class body and letting the
framework read it:

```python
class GetPodLogs(Tool):
    """Return recent container logs for one Pod."""

    name = "get_pod_logs"
    read_only = True

    async def invoke(self, namespace: str, pod: str) -> str: ...


class IncidentResponse(Role):
    """Diagnose unhealthy workloads."""

    instructions = "Work from evidence."

    diagnose = DiagnoseCrashLoopBackOff
    pod_logs = GetPodLogs
```

`core/model/declarative.py` — 371 lines — turned that body into a
`Declaration`: it walked the MRO, filtered out the slot descriptors that
`@dataclass(slots=True)` leaves in `vars(cls)`, derived a node name from the
class name, derived a routing description from the first paragraph of the
docstring, and collected any attribute whose value was a `Role`, `Skill` or
`Tool`. Three `__init_subclass__` hooks ran it at class-creation time and
rebound `__init__` to a generated one.

Four things were wrong with it, and the fourth is what forced the decision.

**1. It built a second forest at import.** Collecting a member instantiated it,
so `Declaration.members[].value` held a prototype. For a nested role that
recursed. Measured on the bundled demo:

```text
import contexture.demo.role      live ContextNode instances: 20
manager.register(...)                                        32
        of which the manager is actually serving:            12
```

Twenty objects, built before anybody asked for anything, never served, and
retained for the life of the process because they hang off class objects.

**2. Two spellings meant two different things.** `diagnose = Diagnose` built a
fresh member per owner; `diagnose = Diagnose()` shared one object across every
owner, so registering the containing role twice failed with *"is held twice"*.
`_materialize`'s own docstring said the two "should mean the same thing". They
did not.

**3. The attribute name was decoration.** `pod_status = GetPodStatus` produced
a node called `get-pod-status`, from the class name. The attribute read like a
member name and was not one; it appeared only in error messages.

**4. None of it survives translation.** TypeScript and Go implementations are
planned, and that is now a constraint on this repository rather than a note
about the roadmap (see the README's *Three languages, one behaviour*). Of the
three things a class body bought:

| | Python | Go | TypeScript |
|---|---|---|---|
| class name → node name | yes | yes | **no** — bundlers rename classes |
| docstring → description | yes | **no** | **no** — not readable at run time |
| attribute scan → members | yes | **no** — no inheritance | awkward |

A field that is optional in one implementation and required in the others is
one declaration meaning two things.

## Decision

**A declaration is a class whose constructor hands its identity to the base
class and builds what the node holds.**

```python
class GetPodLogs(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="pod_logs",
            description="Return recent container logs for one Pod.",
            read_only=True,
        )

    async def invoke(self, namespace: str, pod: str) -> str: ...


class IncidentResponse(Role):
    def __init__(self) -> None:
        super().__init__(
            name="incident-response",
            description="Diagnose unhealthy workloads.",
            instructions="Work from evidence.",
            skills=[DiagnoseCrashLoopBackOff()],
            tools=[GetPodLogs()],
        )
```

Five consequences follow, and each one deletes something.

**Nothing is inferred.** `derive_name` and `derive_description` are gone. What
an agent reads is written down, in every language this framework runs in.

**Nothing is scanned.** Members are built in the constructor that holds them,
so there is no filter deciding what an unrecognised class attribute meant — and
a business can still hang its own `target`, `writes`, or `precondition` contract
on a tool.

**Nothing exists until it is registered.** A class is a zero-argument factory;
its members are built by its own constructor. Importing a declaration
constructs no nodes at all, and `ControllerManager` calling one factory is the
single moment a node comes into existence — which is the only moment it can be
told its `path` and handed its `channels`.

**`Prompt` and `Resource` are written the same way.** They previously refused
to be subclassed, on the grounds that they are pointers rather than nodes. The
distinction is real and the type still keeps it; the *syntax* difference was
not worth it. A business writes one kind of declaration.

**Registration is three methods.** `register_role`, `register_skill`,
`register_tool` — because what a root is decides what may hang beneath it, and
naming the kind at the call site says so where a reader is looking. Three typed
collections are also what Go has instead of a list of `any`. They share one
namespace: a root name is the first segment of every reference beneath it.

## What this costs

**Two lines of ceremony per node.** A skill that is pure data now spends
`def __init__` and `super().__init__(` on saying so. Twenty skills is forty
lines of business boilerplate, against 371 lines of framework machinery and a
second reader-facing concept, in three languages, forever.

**Declaration errors move from import to registration.** A malformed class used
to fail when its module was imported; it now fails when it is built.
Registration is `main()`'s first act, so this is still start-up rather than run
time — and it is the only moment the other two implementations can share, since
Go has no import-time hook.

## Alternatives considered

**Keep the class body, drop only the prototype.** Replace the pre-built member
with a factory reference and leave the reading in place. This fixes the shadow
forest and the two-spellings bug, and leaves the inference and the 371 lines.
It was implemented and then reverted, because the reading layer is exactly the
part that does not translate.

**State everything at the construction site**
(`GetPodLogs(name=..., description=...)`), with no class-level identity at all.
Rejected: twenty skills become twenty argument lists at the point of use, and a
project loses the ability to name, reuse, and subclass a capability. The
objection came from the framework's own author and it was correct.

**Package structure as the role tree** (`assistant/incident/tools.py` becomes
the `incident` role's tools). Rejected: a directory gives a capability exactly
one position, and the same tool legitimately hangs under two roles.

## Consequences

- `core/model/declarative.py` deleted; `tests/test_declarative.py` replaced by
  construction-time tests in `tests/test_manager.py`.
- Three `__init_subclass__` hooks deleted from `Role`, `Skill` and `Tool`; two
  subclass-refusing hooks deleted from `Prompt` and `Resource`.
- `ControllerManager` gained `register_role` / `register_skill` /
  `register_tool` and three lists; `register` and `register_all` are gone.
- A standalone skill or tool may now be a root. `contexture_discover` therefore
  answers with the same three keys `contexture_open` puts under a role —
  `roles`, `skills`, `tools` — where it previously answered with `roles` alone
  and `open` said `sub_roles`. **One payload shape, which is one golden fixture
  per depth instead of two.** This is a wire change.
- A tool registered as a root has its input schema in every `discover` payload.
  Allowed, and documented as the cost of putting it there rather than under a
  role.
- `Role.__post_init__` refuses a class where a node belongs, naming the fix.
  Without it the first thing to fail is the uniqueness check, with a sentence
  about two members sharing a name that nobody wrote.
