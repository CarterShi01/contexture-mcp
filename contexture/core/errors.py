"""Domain-specific exceptions for Contexture."""

from __future__ import annotations

from enum import Enum
from typing import Iterable


class ContextureError(Exception):
    """Base exception for the package."""


class ModelValidationError(ContextureError, ValueError):
    """Raised when a declared object violates a structural invariant."""


class DuplicateNameError(ModelValidationError):
    """Raised when names that must be unique collide in one scope."""


class DeclarationError(ModelValidationError):
    """Raised when a declarative class states something the model cannot accept."""


class WrongDoorError(ContextureError):
    """A Tool was invoked through a door with the wrong mutation semantics."""

    def __init__(self, *, ref: str, read_only: bool) -> None:
        self.ref = ref
        self.read_only = read_only
        stated = "read-only" if read_only else "writing"
        super().__init__(f"{ref!r} is a {stated} Tool")


class LookupFailure(str, Enum):
    """Every way a reference can fail to name a node.

    Exhaustive on purpose: the agent-facing renderer matches on this, and a
    reason with no rendering is a failure an agent would be told nothing about.
    A test asserts the match is total.
    """

    EMPTY_REF = "empty_ref"
    NO_SUCH_ROOT = "no_such_root"
    NOT_A_CONTAINER = "not_a_container"
    NO_SUCH_MEMBER = "no_such_member"
    WRONG_KIND = "wrong_kind"


class NodeNotFoundError(ContextureError):
    """Raised when a reference does not resolve to a node.

    This is the one error in the package with **two audiences**: a Python
    developer reading a traceback, and a connected agent that is expected to
    read what went wrong and try a different reference. Those two want
    different sentences — the developer wants to know which call site and which
    object, the agent wants to know what *is* available and which tool to call
    next — so this error carries neither sentence. It carries the facts, and
    each audience renders its own.

    The agent's rendering is `core.model.system_api.unresolved`, beside the
    calls whose names the good sentence has to name. The facts travel rather
    than the prose because the lookup that fails is several frames below the
    call that was made, and only the call knows which entry point an agent
    should try next.
    """

    def __init__(
        self,
        *,
        reason: LookupFailure,
        ref: str | None = None,
        segment: str | None = None,
        scope: str | None = None,
        kind: str | None = None,
        wanted: str | None = None,
        known: Iterable[str] = (),
    ) -> None:
        #: How the lookup failed.
        self.reason = reason
        #: The whole reference that was being resolved, once it is known.
        self.ref = ref
        #: The one segment of that reference which did not resolve.
        self.segment = segment
        #: The node the failing lookup happened inside.
        self.scope = scope
        #: The kind of node that was found instead, when something was.
        self.kind = kind
        #: The kind of node the caller required, when it required one.
        self.wanted = wanted
        #: What was actually available where the lookup happened.
        self.known = tuple(known)
        super().__init__(self._developer_summary())

    def within(self, ref: str) -> NodeNotFoundError:
        """Return this failure with the whole reference attached.

        A role knows its own name and what it holds; only the tree knows the
        path that was being walked when the lookup failed. Rather than passing
        the reference down into every lookup that will almost always succeed,
        the tree attaches it on the way back up.
        """

        if self.ref is not None:
            return self
        return NodeNotFoundError(
            reason=self.reason,
            ref=ref,
            segment=self.segment,
            scope=self.scope,
            kind=self.kind,
            wanted=self.wanted,
            known=self.known,
        )

    def _developer_summary(self) -> str:
        """Render for a traceback, not for an agent.

        Deliberately terse and field-shaped: whoever reads this has the source
        open and wants to know which lookup failed with what in hand.
        """

        fields = {
            "ref": self.ref,
            "segment": self.segment,
            "scope": self.scope,
            "kind": self.kind,
            "wanted": self.wanted,
        }
        stated = " ".join(
            f"{name}={value!r}" for name, value in fields.items() if value is not None
        )
        if self.known:
            stated += f" known={list(self.known)!r}"
        return f"{self.reason.value}{': ' + stated if stated else ''}"
