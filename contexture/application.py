"""The lazy declaration a business project gives to Contexture.

``Contexture`` is deliberately only a specification.  It records the classes
that make up one application; it does not construct a Role, open Channels,
compile an Index, or import the MCP SDK.  The server runner consumes this
object later, once for each build.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, TypeAlias

from .core.errors import ModelValidationError
from .core.mcp_interface import Prompt, Resource
from .core.model import Channels, Role, Skill, Tool


RootFactory: TypeAlias = type[Role] | type[Skill] | type[Tool]


@dataclass(slots=True, frozen=True, kw_only=True)
class Contexture:
    """One application declaration.

    A project exports exactly one instance:

    ::

        app = Contexture(name="hello", roots=(Hello,))

    A command tree that only a person should enter is declared separately but
    compiled into the same canonical Index::

        app = Contexture(
            name="hello",
            roots=(Hello,),
            prompt_roots=(Commands,),
            prompts=(RunCommand,),
        )

    The values are classes, never constructed nodes.  Importing this module or
    creating ``app`` is therefore safe in a CLI, a test collector, and an IDE.
    ``contexture.server.compile_application`` is the first operation that
    builds the declared forest.
    """

    name: str
    roots: Sequence[RootFactory]
    #: Complete root trees owned by MCP's user-controlled Prompt plane. They
    #: remain addressable by Prompt and ``goto``, but are absent from
    #: ``contexture_discover``, server routing instructions, and model opens.
    prompt_roots: Sequence[RootFactory] = ()
    channels: type[Channels] | None = None
    prompts: Sequence[type[Prompt]] = ()
    resources: Sequence[type[Resource]] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ModelValidationError("A Contexture application needs a non-empty `name`.")

        roots = tuple(self.roots)
        if not roots:
            raise ModelValidationError(
                "A Contexture application needs at least one root Role, Skill, or Tool."
            )
        for root in roots:
            self._require_factory(root, (Role, Skill, Tool), "roots")

        prompt_roots = tuple(self.prompt_roots)
        for root in prompt_roots:
            self._require_factory(root, (Role, Skill, Tool), "prompt_roots")

        if self.channels is not None:
            self._require_factory(self.channels, (Channels,), "channels")

        prompts = tuple(self.prompts)
        for prompt in prompts:
            self._require_factory(prompt, (Prompt,), "prompts")

        resources = tuple(self.resources)
        for resource in resources:
            self._require_factory(resource, (Resource,), "resources")

        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "roots", roots)
        object.__setattr__(self, "prompt_roots", prompt_roots)
        object.__setattr__(self, "prompts", prompts)
        object.__setattr__(self, "resources", resources)

    @staticmethod
    def _require_factory(
        value: object,
        kinds: tuple[type[object], ...],
        field: str,
    ) -> None:
        if isinstance(value, type) and issubclass(value, kinds):
            return
        expected = " or ".join(kind.__name__ for kind in kinds)
        raise ModelValidationError(
            f"`{field}` contains {value!r}; name a {expected} class, not an "
            "already-built object. A declaration stays lazy until it is compiled."
        )


__all__ = ["Contexture", "RootFactory"]
