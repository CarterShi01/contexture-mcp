"""Reusable workflow knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .node import ContextNode, View
from ..errors import ModelValidationError
from ..types import CompiledContext


@dataclass(slots=True, kw_only=True)
class Skill(ContextNode):
    """A reusable method that explains how to perform a class of work.

    ::

        class InspectPodFailure(Skill):
            def __init__(self) -> None:
                super().__init__(
                    name="inspect-pod-failure",
                    description="Diagnose why a Kubernetes Pod is failing.",
                    instructions="1. Inspect status. 2. Read logs.",
                )

    Same shape as a `Tool` minus the behaviour: a Skill has no executable body,
    so its constructor is the whole class. See `Tool` for why everything is
    stated and why nothing is built at import.

    A Skill and a Role both carry instructions, and the difference is whether
    the node holds anything. A Role's instructions orchestrate its members; a
    Skill holds none, so its instructions are the whole of it and opening one
    is the end of a path rather than a step along it. A method that needs its
    own tools to be kept away from its siblings' tools is a child Role, not a
    Skill.

    Against a Tool the split is who performs the work: a Tool is executed by
    the framework and returns a result, a Skill is executed by the model and
    returns nothing. Work that has to be judged rather than computed can only
    be a Skill — which is also why a Skill is the right home for a procedure
    whose steps are existing tools, with no code of its own to run.

    A procedure whose steps live outside its own parent names them in `uses`::

        class ComposeAndShip(Skill):
            def __init__(self) -> None:
                super().__init__(
                    name="compose-and-ship",
                    description="Assemble the weekly letter and send it.",
                    instructions="1. Generate the cover. 2. Apply ...",
                    uses=(
                        "one-creator/assets/image-gen/generate_cover",
                        "one-creator/publishing/layout/apply_template",
                    ),
                )
    """

    #: The complete procedure. There is no second, fuller copy anywhere: this
    #: text reaches an agent only when the skill is opened, so anything left
    #: out of it is not disclosed late — it is not disclosed at all.
    instructions: str

    kind: ClassVar[str] = "skill"
    group: ClassVar[str] = "skills"

    def __post_init__(self) -> None:
        ContextNode.__post_init__(self)
        if not self.instructions.strip():
            raise ModelValidationError(
                f"Skill {self.name!r} must have execution instructions."
            )

    def _compile_active(self, view: View) -> CompiledContext:
        """The whole procedure, plus a card for each capability it names.

        The cards are at ROUTE, and that is what makes a reference cycle safe
        to declare (ADR 008): `diagnose -> remediate -> diagnose` renders as two
        cards naming each other rather than as a walk that does not terminate.
        Rendering a referenced skill at ACTIVE here is the one change that
        would make this unbounded.
        """

        payload = {
            **self.card(view),
            "instructions": self.instructions,
        }
        if self.uses:
            payload["uses"] = view.cards_for(self.uses)
        return payload
