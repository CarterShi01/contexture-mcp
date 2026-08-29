"""The four calls an agent may make, and everything it is told when one fails.

This is the framework's own API, and the only one an agent ever sees. A
declaration of any size projects onto exactly these four::

    contexture_discover              the roots, one level
    contexture_open                  one node's detail, plus its members' cards
    contexture_invoke_read_only      run a tool that leaves the world unchanged
    contexture_invoke                run a tool that does not

**They are not `Tool` nodes and must not become any.** A `Tool` is something a
business declares, hangs in a role, and pays for by disclosure; these are fixed
logic that exists before any declaration and is identical for every one of
them. Modelling them as nodes would put the framework's own plumbing into the
forest it is supposed to be disclosing.

**Why they live in `core` rather than in `server`.** Their identity used to sit
in `core.mcp_interface` and their behaviour in `server`, with the tree they act
on in a third place — and the seam left a mark: the sentence a failed lookup
becomes has to name the call that recovers from it, which meant the sentence
could not be written where the failure happened. It can now. Navigation is the
kernel's (ADR 014), so the names of the calls that navigate belong to the
kernel too, and so do the words it says when one of them is refused.

**What is *not* here.** Nothing about a wire: no JSON-RPC, no JSON Schema
derivation, no argument validation, no transport. One seam carries all of them
in from `server` — the `Binding` the tree holds for each tool — and this module
never inspects it. It was two seams until ADR 016, which is how a card's schema
and the check applied to a call could have come from two derivations.

The bootstrap text a host loads before calling anything is not here either. It
teaches the same four calls, but what fits in one host's instructions field is a
fact about that host's release, so it stays in `contexture.server.instructions`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..constants import (
    DISCOVER_TOOL,
    INVOKE_READ_ONLY_TOOL,
    INVOKE_TOOL,
    OPEN_TOOL,
)
from ..errors import ContextureError, LookupFailure, NodeNotFoundError, WrongDoorError
from ..principal import Principal
from ..types import CompiledContext
from .disclosure import Disclosure
from .runtime import ApplicationRuntime
from .telemetry import InMemoryTelemetry, Telemetry, report
from .tool import Tool


class Refused(ContextureError):
    """A call an agent made that this server will not answer as asked.

    It carries the finished sentence rather than facts, because unlike
    `NodeNotFoundError` it has exactly one audience: the agent that made the
    call, which is expected to read it and try something else. Every message
    below ends by naming what *does* work — a refusal that only says no has
    done half the job.
    """


@dataclass(slots=True, frozen=True, kw_only=True)
class SystemTool:
    """One entry point, and everything the agent learns about it.

    The description is stated once, here, rather than once on the function and
    again at registration. Two copies of a control's label is how the worse one
    ends up being the one that ships.
    """

    name: str
    description: str
    read_only: bool


#: The whole surface, in registration order. A declaration of any size projects
#: onto exactly this: business capabilities travel inside payloads, never here.
GATEWAY = (
    SystemTool(
        name=DISCOVER_TOOL,
        read_only=True,
        description=(
            "List the top-level capabilities this server serves, as short "
            "routing cards. Most are roles: open the one that matches the task; its "
            "sub-roles arrive with it, one level at a time, so a large tree "
            "costs only the branch you enter. A role card is a name, a "
            "sentence, and the ref that opens it — instructions and what a "
            "role holds arrive on opening, never here."
        ),
    ),
    SystemTool(
        name=OPEN_TOOL,
        read_only=True,
        description=(
            "Open one role, skill or tool by ref. Opening a role "
            "returns its instructions and a card for every skill, tool "
            "and sub-role it holds, each with the ref that opens it "
            "and each tool with the schema needed to call it. Opening a skill "
            "returns its complete procedure, available here and nowhere else. "
            "A tool's card is already complete, so run the tool rather than "
            "opening it. Pass a ref taken from a card; never assemble one."
        ),
    ),
    SystemTool(
        name=INVOKE_READ_ONLY_TOOL,
        read_only=True,
        description=(
            "Run a tool that leaves the world unchanged. Use this for every "
            "tool whose card says read_only: true. The ref and arguments come "
            "from that card. A tool that is not read-only is refused here."
        ),
    ),
    SystemTool(
        name=INVOKE_TOOL,
        read_only=False,
        description=(
            "Run a tool that changes something. Use this for every tool whose "
            "card says read_only: false. The ref and arguments come from that "
            "card. A read-only tool is refused here, so that a host can tell "
            "the two apart before a human is asked to approve anything."
        ),
    ),
)

#: Every entry point this server will ever expose, in the order they are
#: registered.
GATEWAY_TOOLS = tuple(entry.name for entry in GATEWAY)

# ------------------------------------------------------------ what is said


def unresolved(failure: NodeNotFoundError) -> str:
    """Render a failed lookup as something an agent can act on.

    Every branch ends by naming the call that recovers from it. A wrong ref is
    a routine, correctable mistake, and the reply is worth spending words on:
    the agent reads it and picks again, so a sentence that only says what went
    wrong has done half the job.
    """

    reason = failure.reason
    ref = failure.ref
    known = ", ".join(failure.known)

    if reason is LookupFailure.EMPTY_REF:
        return (
            "A reference must name at least a root role. Call "
            f"{DISCOVER_TOOL} for the roles this server serves."
        )

    if reason is LookupFailure.NO_SUCH_ROOT:
        return (
            f"No root role named {failure.scope!r}. This server serves: "
            f"{known}. Call {DISCOVER_TOOL} for their cards, then open one to "
            "reach what is beneath it."
        )

    if reason is LookupFailure.NOT_A_CONTAINER:
        return (
            f"Reference {ref!r} continues past {failure.scope!r}, which is a "
            f"{failure.kind} and holds nothing. Open {failure.scope!r} itself "
            f"with {OPEN_TOOL}, or go back to the card the ref came from."
        )

    if reason is LookupFailure.NO_SUCH_MEMBER:
        holds = f"It holds: {known}." if failure.known else "It holds nothing."
        return (
            f"Role {failure.scope!r} holds no member named "
            f"{failure.segment!r}. {holds} Call {OPEN_TOOL} on "
            f"{failure.scope!r} to see each member with the ref that opens it."
        )

    if reason is LookupFailure.WRONG_KIND:
        # The kind that was actually found decides the recovery, so name the
        # one call that works rather than offering a menu of three.
        recovery = {
            "tool": (
                f"Run it with {INVOKE_READ_ONLY_TOOL} or {INVOKE_TOOL}, "
                "whichever its card says."
            ),
        }.get(failure.kind, f"Open it with {OPEN_TOOL}.")
        return f"{ref} names a {failure.kind}, not a {failure.wanted}. {recovery}"

    # Unreachable while `test_every_lookup_failure_has_a_rendering` passes; an
    # agent must never be handed a bare repr, so this stays as the floor.
    return f"{ref!r} could not be resolved."


def wrong_door(ref: str, *, is_read_only: bool) -> str:
    """Render a call that came through the entry point it does not belong to.

    The host decided whether to involve a human from the hint on the entry
    point, so a mismatch is refused rather than honoured. The reply names the
    other door, because this is a mistake the agent can fix on the next call.
    """

    correct = INVOKE_READ_ONLY_TOOL if is_read_only else INVOKE_TOOL
    stated = "read-only" if is_read_only else "not read-only"
    return f"{ref} is {stated}, so it must be run through {correct}."


def taken_by_a_person(ref: str) -> str:
    """Refuse a model the door that was reserved for a person.

    Named after `wrong_door`, and for the same reason: the reply names what
    does work. Here that is a command rather than another call, so the agent's
    correct next move is to say so rather than to try again.

    The node keeps its card, so this is reached by a model that has seen the
    capability and chosen it. Refusing without naming the command would leave
    it to guess whether the thing is broken, forbidden, or merely elsewhere.
    """

    return (
        f"{ref} is opened by a person, not by an agent. It is reachable only "
        "as a command in this host's menu. Do not reproduce its steps another "
        "way; tell the user which command runs it and let them decide when."
    )


# ------------------------------------------------------------ what is done


@dataclass(slots=True, frozen=True)
class SystemAPI:
    """The four calls, bound to one tree.

    It remembers no traversal state, which keeps disclosure legal on a protocol
    that forbids a server to vary its surface as a consequence of an earlier
    request.  Usage telemetry is a side-channel collaborator: it records that a
    node was used but never changes what any disclosure call returns.
    """

    tree: Any

    #: Refs a person has claimed, and a model may therefore not open.
    #:
    #: The one thing here that is not a fact about the forest, and it is here
    #: rather than in `server` because "who may open a node" is a question this
    #: package answers (ADR 008). What fills it is the published prompt list,
    #: which the layer above assembles and passes in.
    reserved: frozenset[str] = field(default=frozenset())

    #: Framework-owned usage evidence. It is not included in disclosure.
    telemetry: Telemetry = field(default_factory=InMemoryTelemetry, repr=False)

    async def discover(self) -> CompiledContext:
        """The roots, as cards. The cost of entering, once, per session."""

        return self.tree.skeleton()

    async def open(self, ref: str) -> CompiledContext:
        """Open one node, as a model.

        The only door that consults `reserved`. A node a person has claimed
        still has a card and is still addressable — it is refused here and
        nowhere else, which is what makes this the model's door rather than a
        second kind of node.
        """

        if ref in self.reserved:
            raise Refused(taken_by_a_person(ref))
        return await self.open_for_a_person(ref)

    async def open_for_a_person(self, ref: str) -> CompiledContext:
        """Open one node, as the person who owns this server.

        Nothing reserves a node *from a person*: `reserved` exists to keep a
        model out of a procedure somebody wants to trigger themselves, and a
        tree holding capabilities its owner may not read would be a strange
        thing to have built.

        Sharing an implementation with `open` is the point rather than a
        saving. The two doors must differ in who may knock and in nothing else
        — one node reached two ways and answering two different things about
        how to call it is worse than either answer alone.
        """

        try:
            node = self.tree.find(ref)
        except NodeNotFoundError as failure:
            raise Refused(unresolved(failure)) from failure
        if isinstance(node, Tool):
            return self.tree.open(ref)
        try:
            opened = self.tree.open(ref)
        except Exception:
            report(self.telemetry, ref, failed=True)
            raise
        report(self.telemetry, ref)
        return opened

    async def read_for_a_host(
        self, ref: str, *, principal: Principal | None = None,
    ) -> Any:
        """Read one document, as the host that was given its address.

        A host reads a resource with no arguments and no model in the loop, so
        there is no door to check here — that a published address names a
        read-only, argument-free tool is settled when the server is built,
        which is the moment whoever wrote the declaration can still fix it.

        It goes through the kernel all the same, rather than reaching for the
        tool directly. That is what puts this path on the same footing as the
        other two: the same lookup failure sentences, the same validated call,
        and a caller's identity in reach of the capability's own code.
        """

        try:
            return await ApplicationRuntime(
                self.tree.index, self.telemetry
            ).invoke_read_only(
                ref, principal=principal
            )
        except NodeNotFoundError as failure:
            raise Refused(unresolved(failure)) from failure

    async def invoke_read_only(
        self,
        ref: str,
        arguments: dict[str, Any] | None = None,
        *,
        context: Any = None,
        principal: Principal | None = None,
    ) -> Any:
        """Run a tool that leaves the world unchanged."""

        return await self._invoke(ref, arguments, read_only=True, context=context,
                                  principal=principal)

    async def invoke(
        self,
        ref: str,
        arguments: dict[str, Any] | None = None,
        *,
        context: Any = None,
        principal: Principal | None = None,
    ) -> Any:
        """Run a tool that changes something."""

        return await self._invoke(ref, arguments, read_only=False, context=context,
                                  principal=principal)

    async def _invoke(
        self,
        ref: str,
        arguments: dict[str, Any] | None,
        *,
        read_only: bool,
        context: Any,
        principal: Principal | None,
    ) -> Any:
        """Resolve, check the door, then run.

        **`read_only` is which door, not which argument.** A host cannot see a
        business tool, so it cannot be told per tool whether to ask a human
        first; it can see which of the two entry points was used, and each
        carries the matching hint. A model that picks the wrong one gets the
        call refused rather than executed, which is the same protection as
        never letting the classification be an argument, relocated to where the
        host can still act on it.
        """

        runtime = ApplicationRuntime(self.tree.index, self.telemetry)
        try:
            if read_only:
                return await runtime.invoke_read_only(
                    ref, arguments, context=context, principal=principal
                )
            return await runtime.invoke(
                ref, arguments, context=context, principal=principal
            )
        except WrongDoorError as failure:
            raise Refused(wrong_door(ref, is_read_only=failure.read_only)) from failure
        except NodeNotFoundError as failure:
            raise Refused(unresolved(failure)) from failure

__all__ = [
    "GATEWAY",
    "GATEWAY_TOOLS",
    "Refused",
    "SystemAPI",
    "SystemTool",
    "taken_by_a_person",
    "unresolved",
    "wrong_door",
]
