"""What this server exposes on each of MCP's three primitives.

One module per primitive, so that the answer to *what does this server put in
front of a host?* is a directory listing rather than a search.

    tool.py       which four entry points occupy this plane; a business
                  adds none, and `core.model.system_api` implements them
    resource.py   content a host may take up on its own
    prompt.py     capabilities a person triggers by name

The three are split by **who decides when it is used** — the protocol's own
axis, not this project's. See this package's README.

**Nothing here imports the SDK.** Declaring what a primitive carries and
putting it on a wire are two jobs, and only the second belongs to `server`.
"""

from typing import Callable, cast

from ..errors import DeclarationError
from .prompt import Prompt
from .resource import Resource
from .tool import TOOLS, ToolPlane


def published(entry: object) -> Prompt | Resource:
    """Normalise one published entry, whether it arrived as a class or a value.

    A business states these the way it states everything else — as a class
    whose constructor hands its identity to the base — and this is where such a
    class is built into the value the server hangs on a primitive. An
    already-built instance passes through, which is what keeps a test able to
    write one inline.
    """

    if isinstance(entry, (Prompt, Resource)):
        return entry
    if isinstance(entry, type) and issubclass(entry, (Prompt, Resource)):
        factory = cast(Callable[[], Prompt | Resource], entry)
        return factory()
    raise DeclarationError(
        f"{entry!r} is neither a Prompt nor a Resource. A published entry "
        "names a node the tree already holds; it is not a node itself."
    )


__all__ = [
    "Prompt",
    "Resource",
    "TOOLS",
    "ToolPlane",
    "published",
]
