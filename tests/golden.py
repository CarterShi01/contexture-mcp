"""Capture everything this server says, so a refactor cannot change one word.

`spec/golden/` holds the exact bytes the bundled demo produces: the five entry
points as a host lists them, the instructions a host reads first, every payload
`contexture_open` answers with, every sentence a refusal becomes, what a person
gets from a command, and what a host gets from a resource read.

**This is the safety net, and it is deliberately dumb.** It asserts nothing
about *why* a payload looks the way it does — `tests/` does that. It asserts
that the bytes are the same as they were, which is the one thing a large
refactor cannot verify by reading.

It is also the first instalment of what the README promises as `spec/golden/`:
the same files a Go or TypeScript implementation has to reproduce.

Regenerate deliberately, never casually::

    .venv/bin/python tests/golden.py --update
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GOLDEN = PROJECT_ROOT / "spec" / "golden"

# Importable both as `tests.golden` and as a script run from anywhere: the
# capture builds the bundled demo, so the package has to be on the path even
# when this file is the entry point.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: Refs that are wrong in each of the five ways a lookup can fail, plus the two
#: ways a call can arrive at the door it does not belong to. Named here rather
#: than in the test so the capture and the comparison cannot disagree.
LOOKUP_FAILURES = (
    ("open", ""),                                                    # EMPTY_REF
    ("open", "no-such-root"),                                        # NO_SUCH_ROOT
    ("open", "kubernetes-platform/incident-response/get_pod_status/deeper"),
    ("open", "kubernetes-platform/no-such-member"),                  # NO_SUCH_MEMBER
    ("invoke_read_only", "kubernetes-platform/incident-response"),   # WRONG_KIND
)

WRONG_DOOR = (
    # A read-only tool sent through the writing door, and the reverse.
    ("invoke", "kubernetes-platform/incident-response/get_pod_status"),
    ("invoke_read_only", "kubernetes-platform/deployment-ops/roll_back_deployment"),
)


def _door(api: Any, name: str) -> Any:
    """The entry point named, as a one-argument callable."""

    if name == "open":
        return lambda ref: api.open(ref)
    if name == "invoke":
        return lambda ref: api.invoke(ref, {})
    return lambda ref: api.invoke_read_only(ref, {})


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"


def _text(result: Any) -> str:
    """The one text block a gateway call answers with."""

    return result.content[0].text


def capture() -> dict[str, str]:
    """Build the demo and return every golden file as `name -> exact bytes`."""

    from contexture.demo.server import build

    holder = build()
    wire = holder.build()
    tree = holder.surface.tree
    api = holder.surface.api

    files: dict[str, str] = {}

    files["instructions.txt"] = wire.instructions or ""

    files["tools.json"] = _json(
        [tool.model_dump(mode="json") for tool in _run(wire.list_tools())]
    )
    files["prompts.json"] = _json(
        [prompt.model_dump(mode="json") for prompt in _run(wire.list_prompts())]
    )
    files["resources.json"] = _json(
        [entry.model_dump(mode="json") for entry in _run(wire.list_resources())]
    )

    files["discover.json"] = _json(_run(api.discover()))

    opened: dict[str, Any] = {}
    for ref, _node in tree.index.nodes_with_refs():
        opened[ref] = _run(api.open(ref))
    files["open.json"] = _json(opened)

    refusals: dict[str, str] = {}
    for door, ref in LOOKUP_FAILURES + WRONG_DOOR:
        refusals[f"{door} {ref!r}"] = _sentence(_door(api, door), ref)
    files["refusals.json"] = _json(refusals)

    commands: dict[str, str] = {}
    for prompt in _run(wire.list_prompts()):
        if prompt.arguments:
            # `goto` takes the ref as an argument; drive it at one known node so
            # the capture is a fixed string rather than a menu.
            got = _run(
                wire.get_prompt(
                    prompt.name, {"ref": "kubernetes-platform/deployment-ops"}
                )
            )
        else:
            got = _run(wire.get_prompt(prompt.name, {}))
        commands[prompt.name] = got.messages[0].content.text
    files["commands.json"] = _json(commands)

    reads: dict[str, str] = {}
    for entry in _run(wire.list_resources()):
        contents = _run(wire.read_resource(str(entry.uri)))
        reads[str(entry.uri)] = "".join(part.content for part in contents)
    files["reads.json"] = _json(reads)

    completions: dict[str, Any] = {}
    for typed in ("", "kubernetes-platform", "kubernetes-platform/dep"):
        matches, total = tree.index.matching_refs(typed, limit=100)
        completions[typed] = {"values": list(matches), "total": total}
    files["completions.json"] = _json(completions)

    return files


def _sentence(call: Any, ref: str) -> str:
    """Run one call that must be refused, and return the sentence it produced."""

    from contexture.core.errors import ContextureError

    try:
        _run(call(ref))
    except ContextureError as refused:
        return str(refused)
    raise AssertionError(f"expected {ref!r} to be refused, and it was not")


def write() -> None:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, body in capture().items():
        (GOLDEN / name).write_text(body, encoding="utf-8")
    print(f"wrote {len(capture())} golden files to {GOLDEN}")


if __name__ == "__main__":
    import sys

    if "--update" in sys.argv:
        write()
    else:
        print(__doc__)
