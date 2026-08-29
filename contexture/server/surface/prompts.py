"""What a person triggers by name: declared commands, and `goto`.

MCP's prompt primitive is the *user-controlled* one — a person picks an entry
from a menu their host shows. So what is hung here is not a second surface for
the model but a second way in for whoever owns this server.

Two kinds of entry, and they are the same door addressed two ways:

    a declared command    carries its ref in its registration
    goto                  carries its ref in an argument, completed as it is typed

Both go through `SystemAPI.open_for_a_person`, so a command and `contexture_open`
cannot answer differently about one node. That matters more than it sounds:
reaching a capability two ways and being told two different things about how to
call it is worse than either answer alone.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.prompts import Prompt as SDKPrompt
from mcp_types import Completion

from ...core.errors import ModelValidationError
from ...core.mcp_interface.prompt import Prompt
from ...core.model.system_api import DisclosureAPI, SystemAPI
from .. import messages
from . import published_name, translated


class Prompts:
    """The declared commands, plus the one prompt that opens anything."""

    __slots__ = ("_api", "_entries")

    def __init__(
        self, api: DisclosureAPI | SystemAPI, entries: tuple[Prompt, ...]
    ) -> None:
        """Refuse two commands a person would reach the same way.

        A node's name only has to be unique among its siblings, because a ref
        supplies the rest of the address. Here there is no such context: these
        are flat names in a menu, so two `deploy` prompts from two branches
        produce one name nobody can aim.

        Refused rather than disambiguated. Generating `deploy-2`, or spelling a
        whole ref into a menu, both answer "which one did you mean" with
        something nobody would have chosen — and the declaration is right there
        to be edited.
        """

        seen: dict[str, str] = {}
        for entry in entries:
            name = published_name(entry)
            if name in seen:
                raise ModelValidationError(
                    f"{seen[name]!r} and {entry.opens!r} are both exposed as "
                    f"the prompt {name!r}. A ref tells them apart and a name in "
                    "a menu cannot; rename one."
                )
            seen[name] = entry.opens
        self._api = api
        self._entries = entries

    def install(self, wire: MCPServer) -> None:
        api = self._api

        for entry in self._entries:
            node = api.tree.find(entry.opens)
            # No arguments: the node is fixed at registration, so there is
            # nothing for a person to fill in and nothing to complete. The
            # prompt *is* the argument.
            wire.add_prompt(
                SDKPrompt.from_function(
                    _command(api, entry.opens),
                    name=published_name(entry),
                    description=messages.command_description(
                        entry.opens, entry.description or node.description
                    ),
                )
            )

        async def goto(ref: str) -> str:
            return await open_by_name(api, ref)

        wire.add_prompt(
            SDKPrompt.from_function(
                goto,
                name=messages.GOTO_PROMPT,
                description=messages.GOTO_DESCRIPTION,
            )
        )

        index = api.tree.index

        @wire.completion()
        async def complete(ref: Any, argument: Any, context: Any) -> Completion | None:
            """Offer the tree's addresses while a person types one.

            Answers for `goto` and nothing else. A declared command takes no
            argument — the node it opens was fixed when it was registered — so
            there is nothing there to complete, and answering anyway would put
            this server's refs under somebody else's prompt.
            """

            if getattr(ref, "name", None) != messages.GOTO_PROMPT:
                return None
            if argument.name != messages.GOTO_ARGUMENT:
                return None

            matches, total = index.matching_refs(
                argument.value, limit=messages.COMPLETION_LIMIT
            )
            values = list(matches)
            if total > len(values):
                # The protocol carries `total` and `has_more`, and a host may
                # show neither. One value spent saying so is cheaper than a
                # person believing they have seen everything.
                values[-1] = messages.truncated_completion(len(values), total)
            return Completion(
                values=values, total=total, has_more=total > len(matches)
            )


def _command(
    api: DisclosureAPI | SystemAPI, ref: str
) -> Callable[[], Awaitable[str]]:
    """Build the one prompt that opens `ref`.

    The text is assembled per call rather than at registration, so a command and
    `contexture_open` cannot answer differently about the same node — a snapshot
    taken at startup is a second copy waiting to disagree.
    """

    async def command() -> str:
        return await open_by_name(api, ref)

    return command


async def open_by_name(api: DisclosureAPI | SystemAPI, ref: str) -> str:
    """Render one node for a person who named it rather than navigated to it.

    Shared by `goto` and by every declared command, which is what makes them the
    same door with two ways of addressing it: a command carries the ref in its
    registration, `goto` carries it in an argument, and a person gets the same
    answer either way.

    It goes through the kernel's own `open`, by the door reserved for a person.
    That is what keeps the two planes from drifting: a command and
    `contexture_open` are one call with two sets of people allowed to make it,
    and `Prompt.model_may_open` reserves a node from a model while nothing
    reserves one from a person — a tree holding capabilities its owner could not
    read would be a strange thing to have built.

    The message is **the payload `contexture_open` would have returned**, plus
    signposts, so the two doors differ in who may knock and in nothing else.
    """

    with translated():
        payload = await api.open_for_person(ref)
        levels = api.tree.index.signpost(ref)
    sections = [
        messages.COMMAND_PREAMBLE.format(ref=ref),
        messages.signpost(levels),
        json.dumps(payload, ensure_ascii=False, indent=2),
        messages.COMMAND_CLOSING,
    ]
    return "\n\n".join(section for section in sections if section)


__all__ = ["Prompts", "open_by_name"]
