"""Fitting the contract into one host's budget.

What this text *says* is `contexture.server.messages`'s business. What fits is
this module's, and the two are separated because they change for unrelated
reasons: the contract moves when the navigation model does, these numbers move
when a host ships a release.

Two host limits shape this file, and both are real rather than defensive:

* Claude Code truncates server instructions at 2KB and loads them at session
  start, before any tool schema.
* Codex reads the same field and asks that the first 512 characters be
  self-contained, because that is what it has in hand while deciding whether
  to use the server at all.

A roster is included here rather than left for the first
`contexture_discover` call. It is static, it is small — a role card is a name,
a sentence and a path — and putting it here answers the question a host asks
before it has called anything: *what is this server for?* Without it, a gateway
server presents four tools whose names all begin with `contexture_` and no
sign that any of them lead to Kubernetes. With it, the first call can be the
one that opens the right role.

Unlike `contexture_discover`, which answers with the roots and nothing below
them, this roster keeps going while there is budget left: it costs no round
trip, and a small forest fits whole. It walks **breadth-first**, because a
roster is a list that gets cut off, and a depth-first cut spends the budget on
one deep spine while never mentioning the root's siblings — the worst possible
answer for text whose only job is routing.

Breadth-first fixes the axis the budget is spent along. It does not, on its
own, stop the budget running out *inside* one parent's children, and a roster
that lists three of a role's eight sub-roles is worse than one that lists none
of them: the three look like the whole choice. So the budget is spent in whole
sibling groups. A group is listed only if all of it fits, which keeps the rule
ADR 004 stated and ADR 007 kept — every sibling is visible before the choice,
and what cannot be seen together is opened rather than guessed between.

The roots are the exception, and are cut entry by entry when even they do not
fit. A roster with no roots says nothing about what the server is for, which is
the one job this text has; and since ADR 007 the roots are exactly what
`contexture_discover` answers with, so a cut root list is the only incomplete
group a single named call restores. The message says which call.
"""

from __future__ import annotations

from typing import Iterator

from ..core.model.node import ContextNode
from ..core.model.disclosure import SEPARATOR, Disclosure
from ..core.constants import DISCOVER_TOOL
from .messages import PREAMBLE, REF_RULE

#: Claude Code truncates server instructions at 2KB; leave room for the rest.
ROSTER_BUDGET = 1200

#: What Claude Code keeps of this field. Past it, the text is simply gone.
INSTRUCTIONS_LIMIT = 2048

#: How far Codex reads while deciding whether to use the server at all, so
#: this much has to stand on its own.
SELF_CONTAINED_PREFIX = 512


def build(
    tree: Disclosure,
    *,
    preamble: str = PREAMBLE,
    budget: int = ROSTER_BUDGET,
) -> str:
    """Return server instructions: the contract first, the roster second."""

    roster: list[str] = []
    spent = 0
    dropped = 0
    full = False
    for index, group in enumerate(_sibling_groups(tree)):
        entries = [f"- {ref}: {node.description}" for ref, node in group]
        cost = sum(len(entry) + 1 for entry in entries)
        if index == 0:
            # The roots. Cut per entry rather than as a group, because a roster
            # with no roots says nothing about what the server is for, and
            # because this is the one group a single named call restores.
            for entry in entries:
                if spent + len(entry) > budget:
                    dropped += 1
                    continue
                roster.append(entry)
                spent += len(entry) + 1
            if dropped:
                roster.append(
                    f"- ...and {dropped} more root role(s); call "
                    f"{DISCOVER_TOOL} for the complete list."
                )
                return _assemble(preamble, roster)
            continue
        if full or spent + cost > budget:
            full = True
            dropped += len(entries)
            continue
        roster.extend(entries)
        spent += cost
    if dropped:
        roster.append(
            f"- ...and {dropped} more role(s) below these; open one of "
            "the roles above to see what it holds."
        )

    return _assemble(preamble, roster)


def _assemble(preamble: str, roster: list[str]) -> str:
    return "\n".join([preamble.strip(), "", "Capabilities:", *roster, "", REF_RULE])


def _sibling_groups(tree: Disclosure) -> Iterator[list[tuple[str, ContextNode]]]:
    """Group the breadth-first walk into the sets a reader chooses between.

    The first group is **every root, of every kind** — a standalone tool is as
    much a top-level answer to "what is this server for" as a role is. After
    that only roles can hold anything, so the rest of the walk is the role
    axis.

    `roles_by_level` queues each role's children together, so one parent's
    children arrive as a contiguous run and grouping is a matter of watching
    the ref's prefix change rather than of walking the tree a second time.
    """

    yield [(root.name, root) for root in tree.roots]

    group: list[tuple[str, ContextNode]] = []
    parent: str | None = None
    for ref, role in tree.index.roles_by_level():
        if not tree.model_can_see(ref):
            continue
        if SEPARATOR not in ref:
            continue                      # a root; already yielded above
        owner = ref.rsplit(SEPARATOR, 1)[0] if SEPARATOR in ref else ""
        if group and owner != parent:
            yield group
            group = []
        parent = owner
        group.append((ref, role))
    if group:
        yield group


__all__ = [
    "INSTRUCTIONS_LIMIT",
    "ROSTER_BUDGET",
    "SELF_CONTAINED_PREFIX",
    "build",
]
