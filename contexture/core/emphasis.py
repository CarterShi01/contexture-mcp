"""How text that must be obeyed announces itself to an agent.

Two authors write into one `instructions` string. A business writes what its
Role does; this framework appends what its own mechanisms require. An agent
reads the concatenation, and if the two are typographically identical it has
no way to tell a procedure that may be adapted from a contract that may not.

**The signal is sameness, not volume.** No exclamation marks, no capitals, no
second copy of a sentence. A fixed head and tail, repeated verbatim at every
occurrence, is what an agent can come to recognize across a long session and
across applications: seen once, the shape is known, and what is inside it does
not have to be re-read as fresh prose to be weighted correctly. Emphasis that
varies per call site teaches nothing and dilutes what is already there.

**Two functions, because there are two authorities and they must not be
confused.** `framework_instruction` is this package speaking about its own
mechanisms and is not exported; the name `contexture-mcp` inside its banner is
a claim only this package may make. `binding_instruction` is exported for an
application marking a hard rule of its own, and it names the application's own
authority instead — a business rule that borrowed the framework's name would
tell an agent that a decision the business may revise tomorrow is a contract
of the framework itself.

Both are pure string composition. Nothing here inspects a type, reads a class
name, or holds state: which mechanism a block belongs to is decided by the
caller, not discovered from the value it happens to be describing.
"""

from __future__ import annotations

from .errors import ModelValidationError

#: The head every framework-composed block opens with, whatever it is about.
#:
#: Fixed rather than composed from the mechanism's name, so that PreProcess and
#: PostProcess — and whatever a later release adds — cross an agent's context
#: as one recognizable thing rather than as a family of similar banners it has
#: to learn separately. The mechanism is named on the line below it instead.
_OUTER_HEAD = "===== contexture-mcp framework instruction — binding, follow exactly ====="

#: The tail, which restates the one property a long session erodes.
#:
#: "regardless of surrounding context" is the whole reason this wrapper exists:
#: the block is composed once, at the moment a Role is opened, and may have to
#: survive many turns of unrelated tool output before it is acted on.
_OUTER_TAIL = "===== end framework instruction — binding regardless of surrounding context ====="

#: What no application may name itself. See `binding_instruction`.
_RESERVED_SOURCE_PREFIX = "contexture"


def _required_line(action: str | None) -> str:
    """Render the one step that must actually happen, or nothing.

    A contract is usually a single imperative wrapped in several sentences of
    explanation, and an agent that skims the explanation has to find the
    imperative inside it. Lifting the imperative onto its own marked line means
    the answer to "what am I being told to *do*" is one line, and the sentences
    around it are what they should have been all along: the reasons.

    Optional because not every binding block reduces to a call. "Never edit
    this file" has no step to sequence, and inventing one to fill the slot
    would make the marker mean less everywhere it is real.
    """

    return f">>> REQUIRED: {action}\n" if action else ""


def framework_instruction(
    kind: str, body: str, *, action: str | None = None
) -> str:
    """Compose one framework-owned addition to a Role's disclosed instructions.

    Internal to this package: `kind` names which of this framework's own
    mechanisms is speaking — "PreProcess", "PostProcess" — and the banner
    claims `contexture-mcp` as the author, which is true only here.

    The business text this is concatenated with is never passed through it and
    never modified by it. A caller supplies what the contract says; this
    function is the whole of how it announces that it is one.
    """

    return f"{_OUTER_HEAD}\n{kind}:\n{_required_line(action)}{body}\n{_OUTER_TAIL}"


def binding_instruction(
    source: str, body: str, *, action: str | None = None
) -> str:
    """Mark one block of an application's own instructions as a hard rule.

    ::

        instructions=(
            "Work from the supplied task context.\n\n"
            + binding_instruction(
                "task-execution role",
                "The supplied brief and inherited context are managed for you "
                "and must not be edited.",
            )
        )

    `source` names whose rule this is — a Role's own name, a policy's name,
    whatever an agent should understand as the authority behind it. It becomes
    the banner, so it is the one word doing the work: an application that marks
    everything, or that names them all differently, gets the same nothing that
    an application marking nothing gets.

    **A text marker is the weaker of the two tools available.** Where a rule
    can be enforced by the Tool that would otherwise break it — refusing the
    call, with facts, the way a writing Tool invoked through the read-only door
    is refused — enforce it there instead. Mark what genuinely cannot be
    checked in code; an agent that has read a rule is not an agent that has
    obeyed one.

    Refuses a `source` claiming this framework's own name, because that name is
    how an agent tells a contract of the framework from a decision of the
    application that may change tomorrow.
    """

    if source.strip().lower().startswith(_RESERVED_SOURCE_PREFIX):
        raise ModelValidationError(
            f"binding_instruction source {source!r} claims this framework's "
            "own name. Name the application authority behind the rule — the "
            "Role, policy or document it comes from — so that an agent can "
            "tell it from a contract this framework itself composed."
        )
    if not source.strip():
        raise ModelValidationError(
            "binding_instruction needs a source naming whose rule this is."
        )
    head = f"===== {source} — binding, follow exactly ====="
    tail = f"===== end {source} ====="
    return f"{head}\n{_required_line(action)}{body}\n{tail}"


__all__ = ["binding_instruction"]
