"""Composite role objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Iterable, Iterator

from .node import ContextNode, View
from ..constants import OPEN_TOOL
from ..emphasis import framework_instruction
from ..errors import (
    DuplicateNameError,
    LookupFailure,
    ModelValidationError,
    NodeNotFoundError,
)
from .skill import Skill
from .tool import Tool
from ..types import CompiledContext


@dataclass(slots=True, kw_only=True)
class Role(ContextNode):
    """A responsibility boundary composed from roles, skills, and tools.

    ::

        class KubernetesOperator(Role):
            def __init__(self) -> None:
                super().__init__(
                    name="k8s-operator",
                    description="Operate and diagnose Kubernetes workloads.",
                    instructions="Inspect before changing the cluster.",
                    children=[DeploymentOps()],
                    skills=[DiagnoseDeployment()],
                    tools=[GetPodLogs(), GetPodStatus()],
                )

        manager.register_role(KubernetesOperator)

    **Members are built here, never discovered.** Three lists rather than one,
    because which of the three a capability belongs in is the modelling
    decision this framework asks a business to make. The three also survive
    translation: they are three typed slices in Go and three typed arrays in
    TypeScript, where one mixed list is neither.

    **Building them inside this constructor is what defers everything.** The
    members of a role that nobody registers are never constructed, and two
    registrations of one class are two independent subtrees — which matters as
    soon as anything is stamped onto a node, because a shared member would take
    the last stamp written anywhere in the process.

    **Membership is fixed once a tree has been built from this role.** The
    member lists are ordinary lists, and assembling one at runtime is supported
    — that is what the imperative door is for. Changing one *after* the role is
    serving is not: since the 2026-07-28 revision a server may not vary its
    surface as a consequence of an earlier call, and an `append` here does
    exactly that. It also skips the uniqueness, cycle, and
    separator checks, which all run at construction. Build the graph you mean to serve, then leave it alone;
    `tests/test_binding.py` holds the statelessness this depends on.
    """

    #: How this role behaves once opened, and how it uses what it holds.
    #:
    #: The second half is what a composite role runs on. Opening a role returns
    #: this text alongside a route card for each member, and a card says what a
    #: member is, never when to reach for it relative to its siblings. A role
    #: that coordinates others owns no tools at all, so every word here is
    #: orchestration — which branch a task belongs to, and what has to be
    #: established before one of them is opened.
    #: This is business-authored text. ACTIVE compilation composes the
    #: framework's own contracts around it when `pre_process` or
    #: `post_process` is present, without changing this field.
    instructions: str
    # Which member a thing belongs in is the modelling decision this
    # framework asks a business to make:
    #
    #   children   Is this a branch a session enters *instead of* its
    #              siblings? Since ADR 007 the role axis is lazy, so a child
    #              costs one round trip to reach and nothing at all to anyone
    #              who never goes there — its card arrives only when this role
    #              is opened. Splitting work that a single task needs both
    #              halves of buys the round trip and none of the saving.
    #   skills     Is this a method rather than a capability — something the
    #              model performs by following it, using tools that belong to
    #              the role rather than to the method?
    #   tools      Can the framework execute this deterministically? If the
    #              answer is "the model has to judge", it is a skill.
    children: list[Role] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)

    #: Optional preparing and finishing responsibilities, each with its own
    #: procedure and equipment. Built like other members, but neither is an
    #: alternative work branch. None adds neither a member nor instructions;
    #: containment does not inherit either of them.
    #:
    #: Neither is a callback. This framework has no signal for work starting
    #: or finishing, so both are disclosure and composed text only: an agent
    #: that ignores the contract is not detected, and a rule that must hold
    #: belongs in the Tool that would otherwise break it.
    pre_process: PreProcess | None = None
    post_process: PostProcess | None = None

    kind: ClassVar[str] = "role"
    group: ClassVar[str] = "roles"

    def __post_init__(self) -> None:
        ContextNode.__post_init__(self)
        if not self.instructions.strip():
            raise ModelValidationError(
                f"Role {self.name!r} must have active instructions."
            )
        self._require_process_member("pre_process", self.pre_process, PreProcess)
        self._require_process_member("post_process", self.post_process, PostProcess)
        self._require_built_members()
        self._require_unique_members()

    def branches(self) -> tuple[ContextNode, ...]:
        """The sub-roles a session enters *instead of* one another.

        Only the children: the two process members, skills and tools are
        equipment, not alternative ways on from it. What asks is anything that
        has to say how many choices remain — `signpost`, and the breadth-first
        roster.
        """

        return tuple(self.children)

    def members(self) -> Iterator[ContextNode]:
        """Yield everything this role holds, in a stable order.

        One definition of "what this role contains", used by the uniqueness
        check below, by `member()`, and by every caller that needs to walk a
        role without caring which member field a thing came from. The two
        process members are contained just like other equipment, so
        registration and validation traverse them without a separate
        lifecycle.

        Ordered the way the work is: what prepares it, then the branches it
        may take, then what finishes it, then its own equipment.
        """

        if self.pre_process is not None:
            yield self.pre_process
        yield from self.children
        if self.post_process is not None:
            yield self.post_process
        yield from self.skills
        yield from self.tools

    def member(self, name: str) -> ContextNode:
        """Return the one member of this role called `name`.

        The lookup is cross-kind because the invariant that makes it possible
        is cross-kind: `_require_unique_members` refuses a role whose skill and
        tool share a name, precisely so that a name is a complete address
        within one role. This method is what that constraint was paid for.
        """

        for member in self.members():
            if member.name == name:
                return member
        raise NodeNotFoundError(
            reason=LookupFailure.NO_SUCH_MEMBER,
            segment=name,
            scope=self.name,
            known=sorted(held.name for held in self.members()),
        )

    def _require_built_members(self) -> None:
        """Refuse a class where a node belongs.

        A member list holds nodes, and a class is not one — it is a factory for
        one. The mistake is `tools=[GetPodLogs]` where `tools=[GetPodLogs()]`
        was meant, and it is caught at the construction site because that is
        where somebody can see both halves of it.

        Left to itself the mistake is quiet and strange: a class carries the
        base dataclass's slot descriptors, so every unbuilt member reads as
        having the same `name`, and the first thing to fail is the uniqueness
        check below with a sentence about two members sharing a name that
        nobody wrote.
        """

        for held in (self.children, self.skills, self.tools):
            for member in held:
                if isinstance(member, type):
                    raise ModelValidationError(
                        f"Role {self.name!r} holds the class "
                        f"{member.__name__}, not a node. Build it: "
                        f"{member.__name__}() — a member list holds nodes, and "
                        "a class is the factory that makes one."
                    )

    @staticmethod
    def _require_process_member(
        field_name: str, member: Role | None, wanted: type[Role]
    ) -> None:
        """Refuse anything but a built member of the type that slot means.

        The two slots differ in one thing only — when the framework tells an
        agent to open what is in them — so the type is what keeps a preparing
        responsibility out of the finishing slot. An ordinary Role in either
        would compile and disclose perfectly well and say the wrong thing about
        itself for the life of the deployment.
        """

        if member is None:
            return
        if not isinstance(member, wanted):
            raise ModelValidationError(
                f"Role {field_name!r} must be a constructed "
                f"{wanted.__name__} or None. Build a {wanted.__name__} "
                "subclass with Subclass(); a class, an ordinary Role, or the "
                "other process kind is not a member of this slot."
            )

    def _require_unique_members(self) -> None:
        """Reject two members of this role that share a name.

        Uniqueness is checked across kinds rather than within them, because a
        member's name is the last segment of the reference that addresses it. A
        skill and a tool that share a name would share an address, and `member()`
        would have to guess which one was meant. Refusing the declaration is
        better than guessing, and a role holding two things called `diagnose`
        was going to confuse a reader anyway.
        """

        seen: dict[str, str] = {}
        for member in self.members():
            previous = seen.get(member.name)
            if previous is not None:
                raise DuplicateNameError(
                    f"Role {self.name!r} declares a {previous} and a "
                    f"{member.kind} both named {member.name!r}. A member's "
                    "name is the last segment of its reference, so members of "
                    "one role cannot share a name."
                )
            seen[member.name] = member.kind

    @staticmethod
    def _require_unique(values: Iterable[str], label: str) -> None:
        materialized = list(values)
        if len(materialized) != len(set(materialized)):
            raise DuplicateNameError(f"A role contains duplicate {label}.")

    def _compile_active(self, view: View) -> CompiledContext:
        """Describe this role, and hand back a card for each member it holds.

        A role used to describe only itself, on the grounds that it could not
        list a member *completely*: a member is reachable only through a
        reference, and a half-listed member — visible and not openable — is
        worse than an unlisted one. The reasoning was right and the conclusion
        was too strong. A role does not have to know how an address is spelled
        in order to hand out addresses; it asks the view for one, and every
        card it renders is therefore openable by construction.

        **One level, and cards.** The members arrive at ROUTE, so opening a
        role delivers what it holds without delivering what *they* hold: a
        sub-role is a card here and a separate call when it is actually chosen.
        That is the whole of the laziness on the role axis (ADR 007), and it
        lives in this method.
        """

        payload = {
            **self.card(view),
            "instructions": self.instructions,
            **view.cards_of(self.members()),
        }
        if self.uses:
            payload["uses"] = view.cards_for(self.uses)
        if self.pre_process is not None:
            ref = self._disclosed_ref(view, self.pre_process, payload, "PreProcess")
            payload["pre_process"] = ref
            payload["instructions"] = (
                framework_instruction(
                    "PreProcess",
                    "Opening it only discloses the preparation procedure; it "
                    "does not execute it. Complete what it requires, then "
                    "return to this role's own instructions below and carry on "
                    "with its work. If the preparation is blocked or fails, "
                    "report that state rather than continuing as though it had "
                    "succeeded.",
                    action=(
                        f"Call {OPEN_TOOL} with ref={ref!r} before starting "
                        "this role's work."
                    ),
                )
                + f"\n\n{payload['instructions']}"
            )
        if self.post_process is not None:
            ref = self._disclosed_ref(view, self.post_process, payload, "PostProcess")
            payload["post_process"] = ref
            payload["instructions"] = (
                f"{payload['instructions']}\n\n"
                + framework_instruction(
                    "PostProcess",
                    "Opening it only discloses the procedure; it does not "
                    "execute it or establish success. Use its available "
                    "capabilities as instructed, respect required approvals, "
                    "and report the actual outcome. If it is blocked, fails, "
                    "or awaits approval, report that state rather than "
                    "claiming success or bypassing approval.",
                    action=(
                        f"Call {OPEN_TOOL} with ref={ref!r} before finishing "
                        "this role's work."
                    ),
                )
            )
        return payload

    @staticmethod
    def _disclosed_ref(
        view: View, member: Role, payload: CompiledContext, kind: str
    ) -> str:
        """The member's reference, once this view is known to disclose it.

        A contract naming a reference the caller may not open would be worse
        than no contract: the agent is told to do something it will be refused
        for attempting, and the refusal names a path this surface was built to
        keep out of sight. So the card has to be in the payload already —
        `cards_of` filtered it against the same audience — and the failure is
        the application's to fix rather than the agent's to discover.
        """

        ref = view.ref_of(member)
        if not any(card["ref"] == ref for card in payload["roles"]):
            raise ModelValidationError(
                f"The declared {kind} is unavailable in this view. Open the "
                f"owning Role through a surface containing its complete "
                f"{kind} subtree."
            )
        return ref


class PreProcess(Role):
    """A Role specialized in preparing what its owner's work depends on.

    Business subclasses provide instructions and compose their own children,
    Skills and Tools, or reference shared capabilities with `uses`. An owner
    designates one with `pre_process=MySetup()`, and the framework composes the
    instruction to open it before starting that owner's work — and to return
    afterwards, because unlike a finishing responsibility this one is not the
    end of the path.

    **A procedure to perform, never an invariant to rely on.** Nothing here
    detects that work has started, so nothing detects an agent that skipped
    this; and unlike a finishing responsibility, whose absence shows up as
    results nobody preserved, a skipped preparation leaves no trace at all. A
    rule that must hold belongs in the Tool that would otherwise break it,
    where it can refuse the call with facts. Put a procedure here, and keep a
    guarantee in code.

    It remains an ordinary Role on the wire, not an executable callback or a
    fourth node kind. Only explicit Tool calls have effects.
    """

    __slots__ = ()


class PostProcess(Role):
    """A Role specialized in the procedure required to finish its owner's work.

    Business subclasses provide instructions and compose their own children,
    Skills and Tools, or reference shared capabilities with `uses`. An owner
    designates one with `post_process=MyCleanup()`, and the framework
    composes the instruction to open it before finishing that owner's work.

    This may mean cleanup, verification, handoff, or preserving results; the
    framework does not prescribe the business outcome. Put a procedure to
    perform here, never an invariant to rely on: a rule that must hold belongs
    in the Tool that would otherwise break it, where code can refuse the call.

    It remains an ordinary Role on the wire, not an executable callback or a
    fourth node kind. Only explicit Tool calls have effects. No host hook,
    automatic finish detection, or guarantee of agent compliance is implied:
    this framework has no signal for work being finished, which is exactly why
    the obligation is composed as text an agent may still ignore.
    """

    __slots__ = ()
