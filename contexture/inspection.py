"""Read the disclosure text yourself, one step at a time.

Everything an agent is told by this server is generated: the bootstrap
instructions, the routing cards, a skill's procedure, the sentence a wrong
reference becomes. The test suite asserts that text. Nothing until now let the
person who *wrote* it read it back.

That is the gap this module fills. It replays a session the way a host walks
one — the instructions loaded at connect, then `contexture_discover`, then one
`contexture_open` per reference — and hands back exactly what would have
arrived, with what it cost. No transport, no model, no agent in the room.

**It calls the same functions the server does.** `instructions.build`,
`Disclosure.skeleton`, `Disclosure.open`, `system_api.unresolved`: the four
wrappers in `contexture.server.surface.tools` do nothing but forward to these,
so a payload printed here is the payload sent there, character for character. What this cannot see is the wire — that the process starts under a
host's launch command, and that nothing but MCP messages reaches stdout. Both
are only visible from outside the process, which is what
`tests/test_stdio_server.py` pays a subprocess for.

Two things are deliberately not replayed. `contexture_invoke` is not, because
running a business tool is not disclosure and a debugging command should not
change anything. Reading content is, but only when asked for: a document's
content is often the largest single thing an agent receives, so its cost
belongs in the accounting, while reading it runs business code that may want
credentials nobody supplied.

**This module is public, and is not on the `contexture` facade.** Both halves
are deliberate. `contexture inspect` is a thin front end over it, and the names
in `__all__` are meant to be imported: a project that wants its context budget
watched in CI asserts against `as_json(trace(...))` rather than parsing the
printed form. It stays off the facade because that facade exports what a
business developer *declares* with, and this is read at development time — it
sits above `server` and belongs to no layer inside `core` (ADR 010). Importing
it costs no SDK: it reaches `server.messages` and `server.instructions` for the
text and the budget, and never for the wire.

Cost is reported as characters, UTF-8 bytes, and an *estimated* token count.
The estimate is one token per wide character plus a quarter token per
everything else — enough to tell 300 tokens from 3,000, and not a tokenizer.
Where a real limit exists it is checked in the unit it is actually enforced in:
Claude Code truncates the instructions field at 2,048 bytes, Codex reads the
first 512 characters.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Sequence, cast

from .core.errors import ContextureError, NodeNotFoundError
from .core.constants import (
    DISCOVER_TOOL,
    INVOKE_READ_ONLY_TOOL,
    OPEN_TOOL,
)
from .core.model.system_api import GATEWAY, unresolved
from .core.model.role import Role
from .core.model.tool import Tool
from .core.types import JsonObject
from .server import messages
from .server.instructions import INSTRUCTIONS_LIMIT, SELF_CONTAINED_PREFIX
from .core.model.disclosure import SEPARATOR, Disclosure

#: The label for the one step that is not a call: what the host loads before
#: it has asked this server anything.
CONNECT = "session start"

#: Codepoint ranges that cost roughly a token each rather than a quarter of
#: one. Enough of CJK, kana and Hangul that a declaration written in Chinese
#: is not reported at a quarter of its real cost.
_WIDE_RANGES = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x2E80, 0x9FFF),  # CJK radicals through the main unified block
    (0xA960, 0xA97F),  # Hangul Jamo Extended-A
    (0xAC00, 0xD7FF),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFF00, 0xFF60),  # fullwidth forms
    (0x20000, 0x3FFFF),  # CJK extensions B and beyond
)


def _is_wide(character: str) -> bool:
    codepoint = ord(character)
    return any(low <= codepoint <= high for low, high in _WIDE_RANGES)


@dataclass(frozen=True, slots=True, kw_only=True)
class Cost:
    """What one arriving thing costs to read."""

    characters: int
    bytes: int
    tokens: int

    @classmethod
    def of(cls, text: str) -> Cost:
        wide = sum(1 for character in text if _is_wide(character))
        return cls(
            characters=len(text),
            bytes=len(text.encode("utf-8")),
            tokens=round(wide + (len(text) - wide) / 4),
        )

    def __add__(self, other: Cost) -> Cost:
        return Cost(
            characters=self.characters + other.characters,
            bytes=self.bytes + other.bytes,
            tokens=self.tokens + other.tokens,
        )

    def as_json(self) -> JsonObject:
        return {
            "characters": self.characters,
            "bytes": self.bytes,
            "estimated_tokens": self.tokens,
        }


ZERO = Cost(characters=0, bytes=0, tokens=0)


@dataclass(frozen=True, slots=True, kw_only=True)
class Check:
    """One host limit or disclosure rule, measured against this step."""

    ok: bool
    note: str

    def as_json(self) -> JsonObject:
        return {"ok": self.ok, "note": self.note}


@dataclass(frozen=True, slots=True, kw_only=True)
class Step:
    """One thing an agent receives, and what it cost to receive it.

    `body` is the text as it arrives — the instructions field verbatim, a
    payload as JSON, or the refusal sentence a bad reference produces. It is
    what the cost is measured on, because it is what a model reads.
    """

    call: str
    ref: str | None = None
    body: str
    payload: JsonObject | None = None
    checks: tuple[Check, ...] = ()
    refused: bool = False
    #: Said to the developer, never to the agent: what this step does not show.
    aside: str | None = None

    @property
    def cost(self) -> Cost:
        return Cost.of(self.body)

    def as_json(self) -> JsonObject:
        return {
            "call": self.call,
            "ref": self.ref,
            "refused": self.refused,
            "body": self.body,
            "payload": self.payload,
            "checks": [check.as_json() for check in self.checks],
            "cost": self.cost.as_json(),
            "aside": self.aside,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class Trace:
    """A replayed session: every step, in the order a host takes them."""

    steps: tuple[Step, ...] = ()

    @property
    def total(self) -> Cost:
        total = ZERO
        for step in self.steps:
            total = total + step.cost
        return total

    @property
    def failures(self) -> tuple[Step, ...]:
        return tuple(
            step
            for step in self.steps
            if step.refused or any(not check.ok for check in step.checks)
        )

    def as_json(self) -> JsonObject:
        return {
            "steps": [step.as_json() for step in self.steps],
            "total": self.total.as_json(),
        }


# ------------------------------------------------------------------ the steps


def connect_step(tree: Disclosure, instructions: str) -> Step:
    """What the host loads before it has called anything.

    The three checks are the three ways this text fails in production, and
    every one of them fails silently: truncated past 2KB, an opening that does
    not stand on its own, a roster cut short of the roles it is meant to route
    to. None of them raises anywhere.
    """

    cost = Cost.of(instructions)
    opening = instructions[:SELF_CONTAINED_PREFIX]
    declared = len(tuple(tree.index.roles_by_level()))
    listed, cut = _roster_lines(instructions)

    checks = [
        Check(
            ok=cost.bytes <= INSTRUCTIONS_LIMIT,
            note=(
                f"{cost.bytes} of {INSTRUCTIONS_LIMIT} bytes — Claude Code "
                "truncates what is over, mid-sentence"
            ),
        ),
        Check(
            ok=OPEN_TOOL in opening,
            note=(
                f"{OPEN_TOOL} named in the first "
                f"{SELF_CONTAINED_PREFIX} characters — that is how far Codex "
                "reads while deciding whether to use this server"
            ),
        ),
        Check(
            ok=not cut and listed == declared,
            note=(
                f"roster lists {listed} of {declared} role(s)"
                + (" — the rest were cut for budget" if cut else "")
            ),
        ),
    ]

    payload: JsonObject = {
        "instructions": instructions,
        "gateway": cast(Any, _gateway()),
    }
    return Step(
        call=CONNECT,
        body=instructions,
        payload=payload,
        checks=tuple(checks),
        aside=(
            f"the {len(GATEWAY)} gateway tool descriptions arrive "
            f"here too, costing {_gateway_cost().tokens} more estimated "
            "tokens; they are fixed, so they are not counted below"
        ),
    )


def discover_step(tree: Disclosure) -> Step:
    """The first call a host makes: the roots, and nothing under them."""

    payload = tree.skeleton()
    return Step(
        call=DISCOVER_TOOL,
        body=_wire(payload),
        payload=payload,
    )


def open_step(tree: Disclosure, ref: str) -> Step:
    """One `contexture_open`, including the refusal if the ref is wrong.

    A wrong reference is not an error here. It is the most useful thing this
    command shows: the sentence an agent would read, which is the whole of
    what it has to recover from.
    """

    try:
        payload = tree.open(ref)
    except NodeNotFoundError as failure:
        return Step(
            call=OPEN_TOOL,
            ref=ref,
            body=unresolved(failure),
            refused=True,
            aside="this sentence is all the agent gets; it recovers from it",
        )
    except ContextureError as failure:
        # A declaration-time failure. It carries its own sentence, and its
        # audience is whoever wrote the declaration — which is this reader.
        return Step(
            call=OPEN_TOOL, ref=ref, body=str(failure), refused=True
        )

    aside = None
    if _is_content(tree.find(ref)):
        aside = (
            "the document itself is not here — an agent runs it with "
            f"{INVOKE_READ_ONLY_TOOL}; pass --read to include it and its cost"
        )
    return Step(
        call=OPEN_TOOL, ref=ref, body=_wire(payload), payload=payload,
        checks=_routing_checks(payload),
        aside=aside,
    )


#: How long a routing sentence may run before it has stopped routing.
#:
#: A description answers "should I go here". Past a certain length it has
#: started answering "what will I find inside", which is what opening delivers
#: — and the two copies are then free to disagree.
DESCRIPTION_BUDGET = 200


def _routing_checks(payload: JsonObject) -> tuple[Check, ...]:
    """Measure the one disclosure rule nothing refuses.

    `core` rejects a description that is structurally unusable — empty, or
    carrying a separator — and stops there deliberately: past that the rule is
    about wording, and a framework with an opinion about English would be wrong
    in Chinese and wrong again in Go. So it is measured here instead, where the
    audience is the developer who can act on it and nothing is being blocked.

    Two signals, both cheap and both specific:

    * a routing sentence that runs long has usually started describing the
      inside;
    * a card that spells the name of something the node holds *is* describing
      the inside, and that name is already in this same payload one line down.
    """

    cards = [
        card
        for group in ("roles", "skills", "tools")
        for card in payload.get(group, [])  # type: ignore[union-attr]
        if isinstance(card, dict)
    ]
    if not cards:
        return ()

    held = {str(card.get("name", "")) for card in cards}
    long = [
        str(card["name"])
        for card in cards
        if len(str(card.get("description", ""))) > DESCRIPTION_BUDGET
    ]
    listing = sorted(
        {
            str(card["name"])
            for card in cards
            for name in held
            if name != card.get("name") and name in str(card.get("description", ""))
        }
    )
    opened = str(payload.get("description", ""))
    if any(name in opened for name in held):
        listing = sorted({*listing, str(payload.get("name", ""))})

    return (
        Check(
            ok=not long,
            note=(
                f"every routing sentence is within {DESCRIPTION_BUDGET} "
                "characters"
                if not long
                else f"over {DESCRIPTION_BUDGET} characters: {', '.join(long)}"
                " — a sentence that long has stopped routing and started "
                "describing what opening would deliver"
            ),
        ),
        Check(
            ok=not listing,
            note=(
                "no routing sentence names what its node holds"
                if not listing
                else f"{', '.join(listing)} name(s) their own members — the "
                "inside is what opening delivers, and describing it twice is "
                "how the two copies start disagreeing"
            ),
        ),
    )


def _is_content(node: object) -> bool:
    """Whether this node is content already sitting there, rather than a call.

    A read-only tool that takes no arguments: two runs return the same bytes
    and nothing is computed from an argument. It is also exactly what may be
    published on the resource primitive, which is why the same test decides
    both.
    """

    return (
        isinstance(node, Tool)
        and node.read_only
        and not inspect.signature(getattr(node, "invoke")).parameters
    )


def read_step(tree: Disclosure, ref: str) -> Step:
    """One `contexture_invoke_read_only` against content that takes no arguments."""

    try:
        tool = tree.tool(ref)
        content = asyncio.run(getattr(tool, "invoke")())
    except NodeNotFoundError as failure:
        return Step(
            call=INVOKE_READ_ONLY_TOOL,
            ref=ref,
            body=unresolved(failure),
            refused=True,
        )
    except ContextureError as failure:
        return Step(
            call=INVOKE_READ_ONLY_TOOL, ref=ref, body=str(failure), refused=True
        )

    if isinstance(content, bytes):
        return Step(
            call=INVOKE_READ_ONLY_TOOL,
            ref=ref,
            body=f"<{len(content)} bytes of binary>",
            aside="binary content is described rather than printed",
        )
    return Step(call=INVOKE_READ_ONLY_TOOL, ref=ref, body=str(content))


# ----------------------------------------------------------------- the replay


def every_ref(tree: Disclosure) -> Iterator[str]:
    """Every reference in the tree, breadth-first, each role before its members.

    This is the sweep behind `--all`: it is how a developer reads all of their
    own disclosure text at once. Breadth-first for the same reason the roster
    is — a truncated read of a deep spine tells you least.
    """

    # Unlike the routing roster, a full inspection includes process equipment.
    queue = [root for root in tree.index.roots if isinstance(root, Role)]
    while queue:
        role = queue.pop(0)
        yield tree.index.ref_of(role)
        for member in role.members():
            if isinstance(member, Role):
                queue.append(member)
            else:
                yield tree.index.ref_of(member)


def trace(
    tree: Disclosure,
    refs: Sequence[str] = (),
    *,
    instructions: str,
    discover: bool = True,
    read: bool = False,
) -> Trace:
    """Replay a session: connect, discover, then open each reference in turn."""

    steps: list[Step] = [connect_step(tree, instructions)]
    if discover:
        steps.append(discover_step(tree))
    for ref in refs:
        step = open_step(tree, ref)
        steps.append(step)
        if read and not step.refused and _is_content(tree.find(ref)):
            steps.append(read_step(tree, ref))
    return Trace(steps=tuple(steps))


# --------------------------------------------------------------- the printing


def render(trace: Trace, *, payloads: bool = True) -> str:
    """Render a trace for a terminal: each step, then the running total."""

    lines: list[str] = []
    for index, step in enumerate(trace.steps):
        lines.extend(_render_step(index, step, payloads=payloads))
        lines.append("")
    lines.extend(_render_summary(trace))
    return "\n".join(lines)


def as_json(trace: Trace) -> str:
    """Render a trace as JSON, for diffing one revision against the next."""

    return json.dumps(trace.as_json(), indent=2, ensure_ascii=False)


def _render_step(index: int, step: Step, *, payloads: bool) -> list[str]:
    cost = step.cost
    heading = f"step {index}  {step.call}"
    if step.ref:
        heading += f"  {step.ref}"
    if step.refused:
        heading += "  [refused]"
    lines = [heading, f"  {_amount(cost)}"]
    for check in step.checks:
        lines.append(f"  {'ok  ' if check.ok else 'BAD '}{check.note}")
    if step.aside:
        lines.append(f"  note: {step.aside}")
    if payloads:
        lines.append("")
        lines.extend(f"  | {line}" for line in step.body.splitlines() or [""])
    return lines


def _render_summary(trace: Trace) -> list[str]:
    width = max((len(step.ref or "") for step in trace.steps), default=0)
    width = min(max(width, 3), 52)
    lines = ["-" * (36 + width), f"{'#':>2}  {'call':<26}  {'ref':<{width}}  ~tok"]
    running = ZERO
    for index, step in enumerate(trace.steps):
        running = running + step.cost
        ref = step.ref or "-"
        if len(ref) > width:
            ref = "…" + ref[-(width - 1):]
        lines.append(
            f"{index:>2}  {step.call:<26}  {ref:<{width}}  "
            f"{step.cost.tokens:>5}  (running {running.tokens})"
        )
    total = trace.total
    lines.append("-" * (36 + width))
    lines.append(
        f"total  {total.characters} characters, {total.bytes} bytes, "
        f"~{total.tokens} tokens over {len(trace.steps)} step(s)"
    )
    refused = sum(1 for step in trace.steps if step.refused)
    if refused:
        lines.append(f"       {refused} step(s) refused")
    failed = [
        check
        for step in trace.steps
        for check in step.checks
        if not check.ok
    ]
    if failed:
        lines.append(f"       {len(failed)} host limit(s) not met")
    return lines


def _amount(cost: Cost) -> str:
    return (
        f"{cost.characters} characters, {cost.bytes} bytes, "
        f"~{cost.tokens} tokens"
    )


def _wire(payload: JsonObject) -> str:
    """Serialize a payload the way it travels: JSON, and nothing added."""

    return json.dumps(payload, indent=2, ensure_ascii=False)


def _gateway() -> list[JsonObject]:
    return [
        {
            "name": entry.name,
            "description": entry.description,
            "read_only": entry.read_only,
        }
        for entry in GATEWAY
    ]


def _gateway_cost() -> Cost:
    return Cost.of(
        "".join(entry.name + entry.description for entry in GATEWAY)
    )


def _roster_lines(instructions: str) -> tuple[int, bool]:
    """Count the roles the roster actually names, and whether it was cut.

    Read back out of the rendered text rather than recomputed from the tree,
    because the question is what the host was told, not what it could have
    been told.
    """

    listed = 0
    cut = False
    for line in instructions.splitlines():
        if not line.startswith("- "):
            continue
        if line.startswith("- ...and "):
            cut = True
            continue
        listed += 1
    return listed, cut


__all__ = [
    "CONNECT",
    "Check",
    "Cost",
    "Step",
    "Trace",
    "as_json",
    "connect_step",
    "discover_step",
    "every_ref",
    "open_step",
    "read_step",
    "render",
    "trace",
]
