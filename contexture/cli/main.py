"""The commands themselves, and the parser that reaches them.

    contexture new my-context      # write the project
    contexture list                # what would be served
    contexture inspect             # what an agent receives, and what it costs
    contexture serve               # serve it over MCP
    contexture demo                # serve the bundled reference application

Every command that serves builds its graph the same way a business `main()`
does — register, seal, serve — through `_assembled` below. Two doors, one
flow: a project that would rather not write an entry point gets these
commands, and one that writes its own follows the same five steps by hand.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from ..core.constants import PACKAGE_VERSION
from ..core.errors import ContextureError, LookupFailure, NodeNotFoundError
from .project import (
    DEMO_APP,
    Serving,
    _targets_and_project,
    load_application,
    load_channels,
    load_roots,
    load_published,
    resolve_target,
)
from .scaffold import available_templates, new_project
from .usage import UsageError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..server import CompiledApplication
    from ..server import ContextureOptions, ContextureServer

def command_new(args: argparse.Namespace) -> int:
    root = new_project(args.name, destination=args.into, template=args.template)
    relative = _display_path(root)
    print(f"Wrote {relative}")
    print()
    print("Next:")
    print(f"  cd {relative}")
    print("  uv sync")
    print("  uv run contexture check     # build and validate")
    print("  uv run contexture list      # what it would serve")
    print("  uv run contexture serve     # serve it over stdio")
    return 0


def command_list(args: argparse.Namespace) -> int:
    serving = _targets_and_project(args.target)
    index = _compiled(serving).index

    # Printed with the reference an agent would actually be handed, so what a
    # developer reads here is what the model reads there.
    for ref, role in index.roles_with_refs():
        indent = "  " * ref.count("/")
        print(f"{indent}{role.name}  — {role.description}")
        for skill in role.skills:
            print(f"{indent}  skill     {ref}/{skill.name}")
        for tool in role.tools:
            access = "read-only" if tool.read_only else "needs approval"
            print(f"{indent}  tool      {ref}/{tool.name}  ({access})")
    return 0


def command_check(args: argparse.Namespace) -> int:
    """Compile the project without opening Channels or starting a server."""

    compiled = _compiled(_targets_and_project(args.target))
    counts = {
        kind: len(compiled.index.of_kind(kind))
        for kind in ("role", "skill", "tool")
    }
    print(
        f"OK {compiled.name}: {counts['role']} role(s), {counts['skill']} skill(s), "
        f"{counts['tool']} tool(s)"
    )
    return 0


def command_call(args: argparse.Namespace) -> int:
    """Run one business Tool locally through its production binding."""

    from ..core.model.system_api import SystemAPI

    compiled = _compiled(_targets_and_project(args.target))
    arguments = _input_arguments(args)
    try:
        tool = compiled.index.tool(args.ref)
    except NodeNotFoundError as exc:
        if exc.reason is LookupFailure.WRONG_KIND:
            raise UsageError(
                f"{args.ref} is a {exc.kind}, not a Tool. Inspect it with "
                f"`contexture inspect {args.ref}`."
            ) from exc
        raise UsageError(
            f"Cannot find Tool {args.ref!r}. Run `contexture list`, or inspect "
            "a Role to see its available Tool refs."
        ) from exc
    if not tool.read_only and not args.allow_write:
        raise UsageError(
            f"{args.ref} is not read-only. Re-run with `--allow-write` only "
            "when you intend this local call to change external state."
        )

    async def invoke() -> object:
        async with compiled.index.provisioned():
            api = SystemAPI(compiled.disclosure)
            if tool.read_only:
                return await api.invoke_read_only(args.ref, arguments)
            return await api.invoke(args.ref, arguments)

    result = asyncio.run(invoke())
    if isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


def _input_arguments(args: argparse.Namespace) -> dict[str, object]:
    if args.input is not None and args.input_file is not None:
        raise UsageError("Pass either `--input` or `--input-file`, not both.")
    raw = args.input
    if args.input_file is not None:
        try:
            raw = args.input_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"Cannot read {args.input_file}: {exc}") from exc
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError(f"Tool input must be a JSON object: {exc.msg}.") from exc
    if not isinstance(parsed, dict):
        raise UsageError("Tool input must be a JSON object.")
    return parsed


def _compiled(serving: Serving) -> "CompiledApplication":
    """Register, compile, and hand back the view — `main()`'s middle steps.

    The same objects a project writes by hand, in the same order. What differs
    is only where each one is named: here from a `[tool.contexture]` table,
    there from an import. Two doors, one flow — which is what keeps a newcomer
    learning one path instead of two.

    What `inspect` replays is the disclosure a model receives; the published
    prompts and resources are a person's and a host's doors, not part of that
    trace, so the compiled index is all this needs. The schema on a card comes
    from the real binding, which is the whole point of the command.

    Imported here, not at module scope: `new` and `list` must work in an
    environment that has no SDK, and only the commands calling this need one.
    """

    from ..core.mcp_interface import Prompt, Resource, published
    from ..server import compile_application, compile_parts

    if serving.app is not None:
        return compile_application(load_application(serving.app, project=serving.project))

    project = serving.project
    # `load_roots` is what puts the project on `sys.path`, so it runs before
    # anything else the project declared is resolved.
    roots = load_roots(serving.roots, project=project)
    entries = [
        published(entry)
        for entry in load_published(serving.publish, project=project)
    ]
    return compile_parts(
        name=serving.name or "contexture",
        roots=roots,
        channels=load_channels(serving.channels, project=project),
        prompts=(entry for entry in entries if isinstance(entry, Prompt)),
        resources=(entry for entry in entries if isinstance(entry, Resource)),
    )


def command_inspect(args: argparse.Namespace) -> int:
    """Print what an agent would receive, step by step.

    The tree is sealed with the real binding rather than left bare, which is
    the whole point of the command: a tool schema on a card comes from the same
    derivation the server validates calls with, and the instructions come from
    the same builder. What is printed here is what is served there, or this command
    is worth nothing.
    """

    from .. import inspection
    from ..server import instructions as instructions_module

    serving = _targets_and_project(args.target, or_demo=True)
    if serving.project is None and not args.target:
        # stderr, so `--json` stays a clean document on stdout. Announced at
        # all because replaying something other than what the reader meant, in
        # silence, is worse than the refusal this replaced.
        print(
            "No project here, so this is the bundled demo. Run inside a "
            "project, or name one with --target, to read your own.",
            file=sys.stderr,
        )
    view = _compiled(serving).disclosure

    instructions = instructions_module.build(
        view,
        budget=(
            args.roster_budget
            if args.roster_budget is not None
            else instructions_module.ROSTER_BUDGET
        ),
    )
    refs = list(inspection.every_ref(view)) if args.all else list(args.refs)

    traced = inspection.trace(
        view,
        refs,
        instructions=instructions,
        discover=not args.no_discover,
        read=args.read,
    )

    if args.json:
        print(inspection.as_json(traced))
    else:
        print(inspection.render(traced, payloads=not args.summary))
    # A refused ref or an unmet host limit is a finding, not a crash: the text
    # is still printed, and the exit status is what a script can act on.
    return 1 if traced.failures else 0


def command_catalog(args: argparse.Namespace) -> int:
    """Print the compiled node graph without replaying agent disclosure."""

    from ..introspection import CatalogDetail, CatalogView
    from ..core.model.root_selection import SurfaceSelection

    compiled = _compiled(_targets_and_project(args.target))
    view = CatalogView(args.view)
    selection = None
    if args.select:
        selection = SurfaceSelection.only(args.select)
    catalog = compiled.catalog(
        view=view,
        detail=CatalogDetail(args.detail),
        locale=args.locale,
        surface_selection=selection,
    )

    if args.refs:
        nodes = [catalog.get(ref) for ref in args.refs]
        if args.json:
            print(json.dumps(
                [node.as_json() for node in nodes],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ))
            return 0
        for node in nodes:
            print(f"{node.kind:<5}  {node.ref}  — {node.description}")
        return 0

    if args.json:
        print(catalog.snapshot().to_json())
        return 0

    snapshot = catalog.snapshot()
    print(
        f"{snapshot.application.name}  {snapshot.application.mode}  "
        f"{snapshot.application.view.value}/{snapshot.application.detail.value}"
    )
    print(f"digest  {snapshot.content_digest}")
    for node in snapshot.nodes:
        indent = "  " * node.ref.count("/")
        print(f"{indent}{node.kind:<5}  {node.ref}  — {node.description}")
    return 0


def command_debug(args: argparse.Namespace) -> int:
    """Serve the compiled Node Catalog on a local developer-only port."""

    from ..core.model.root_selection import SurfaceSelection

    compiled = _compiled(_targets_and_project(args.target))
    selection = SurfaceSelection.only(args.select) if args.select else None
    compiled.debug(
        view=args.view,
        detail=args.detail,
        locale=args.locale,
        surface_selection=selection,
    ).run(host=args.host, port=args.port)
    return 0


def serve_options(args: argparse.Namespace) -> "ContextureOptions":
    """Turn the transport flags into the object that validates them.

    Every check lives on `ContextureOptions` rather than here, so that a
    project embedding a graph in its own process is held to the same rules as
    one served from this command. A command line that validated separately
    would be a second opinion, and the two would drift.

    Authentication is deliberately not a flag. A verifier is an object with a
    method, not a string: a deployment that needs one writes a few lines that
    build `ContextureOptions(auth=Auth(...))` and calls `app.run(options)`.
    """

    from ..server import ContextureOptions

    return ContextureOptions(
        transport=args.transport,
        host=args.host,
        port=args.port,
        path=args.path,
        allowed_hosts=tuple(args.allow_host),
        allowed_origins=tuple(args.allow_origin),
        allow_anonymous=args.allow_anonymous,
    )


def command_demo(args: argparse.Namespace) -> int:
    """Serve the bundled reference application, to prove an install works.

    Built the same way any project is — through `_server`, from the demo's
    targets — rather than by importing the demo, so this command line depends on
    nothing above it.
    """

    serving = Serving(roots=(), app=DEMO_APP)
    _server(serving).start(serve_options(args))
    return 0


def command_serve(args: argparse.Namespace) -> int:
    serving = _targets_and_project(args.target)
    _server(serving).start(serve_options(args))
    return 0


def _server(serving: Serving) -> "ContextureServer":
    """Register, compile, and open the doors — a project's whole `main`, from a table.

    The published entries are sorted into their two planes here, at the one call
    site that cannot name the kind at author time: a `[tool.contexture]` table
    lists strings, and which plane each names is only knowable once it is
    resolved. Everywhere a person writes the entry point by hand, they name the
    plane directly with `prompts=` and `resources=`.
    """

    return _compiled(serving).server()


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def add_transport_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the flags every serving command shares.

    Defined once and added twice rather than written twice, so `serve` and
    `demo` cannot end up offering different ways to reach the same server.

    Each defaults to `None`, never to the value it eventually takes, which is
    what lets `--transport stdio --port 9000` be reported as a contradiction
    rather than accepted and ignored. The defaults themselves live on
    `ContextureOptions`.
    """

    parser.add_argument(
        "--transport",
        default="stdio",
        choices=("stdio", "streamable-http"),
        help="transport to serve on (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="interface to bind (default: 127.0.0.1, this machine only)",
    )
    parser.add_argument(
        "--port", type=int, default=None, help="port to bind (default: 8000)"
    )
    parser.add_argument(
        "--path", default=None, help="path the MCP endpoint lives at (default: /mcp)"
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        metavar="HOST",
        help=(
            "a Host header this server answers, repeatable. Required once "
            "--host is not loopback"
        ),
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="an Origin header this server answers, repeatable",
    )
    parser.add_argument(
        "--allow-anonymous",
        action="store_true",
        help=(
            "serve a non-loopback address with no authentication — everything "
            "the tools can reach becomes reachable by anyone who can route here"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contexture",
        description="Declare roles, skills and tools; serve them over MCP.",
    )
    parser.add_argument("--version", action="version", version=PACKAGE_VERSION)
    subcommands = parser.add_subparsers(dest="command", required=True)

    new = subcommands.add_parser(
        "new", help="write a runnable project you can edit into your own"
    )
    new.add_argument("name", help="project name, e.g. my-context")
    new.add_argument(
        "--into",
        type=Path,
        default=None,
        metavar="DIR",
        help="parent directory to write into (default: here)",
    )
    new.add_argument(
        "--template",
        default="project",
        help=f"template to use (available: {', '.join(available_templates()) or 'none'})",
    )
    new.set_defaults(func=command_new)

    listing = subcommands.add_parser(
        "list", help="print the roles, skills and tools that would be served"
    )
    listing.add_argument("target", nargs="?", help="package.module:RoleClass")
    listing.set_defaults(func=command_list)

    check = subcommands.add_parser(
        "check", help="build and validate the application without opening connections"
    )
    check.add_argument("target", nargs="?", help="legacy package.module:RoleClass")
    check.set_defaults(func=command_check)

    call = subcommands.add_parser(
        "call", help="run one Tool locally through the production binding"
    )
    call.add_argument("ref", help="Tool ref, e.g. hello/say_hello")
    call.add_argument("--input", default=None, help="Tool arguments as a JSON object")
    call.add_argument(
        "--input-file", type=Path, default=None, metavar="FILE", help="read Tool arguments from JSON FILE"
    )
    call.add_argument(
        "--allow-write", action="store_true", help="allow a non-read-only Tool to run"
    )
    call.add_argument("--target", default=None, help="legacy package.module:RoleClass")
    call.set_defaults(func=command_call)

    inspect = subcommands.add_parser(
        "inspect",
        help="print what an agent receives at each step, and what it costs",
        description=(
            "Replay a session without an agent: the instructions a host loads "
            "at connect, the roots contexture_discover answers with, then one "
            "contexture_open per ref you name. What is printed is what is "
            "served — the same builder, the same tree, the same schemas. Only "
            "the wire is missing. A wrong ref prints the sentence the agent "
            "would read rather than failing."
        ),
    )
    inspect.add_argument(
        "refs",
        nargs="*",
        help="refs to open, in order, e.g. my-role my-role/my-skill",
    )
    inspect.add_argument(
        "--target", default=None, help="package.module:RoleClass"
    )
    inspect.add_argument(
        "--all",
        action="store_true",
        help="open every node in the tree, so you can read all of your own text",
    )
    inspect.add_argument(
        "--read",
        action="store_true",
        help="also read each resource named, running its read() and costing it",
    )
    inspect.add_argument(
        "--summary",
        action="store_true",
        help="costs and host limits only, without the payloads",
    )
    inspect.add_argument(
        "--json", action="store_true", help="emit the trace as JSON, for diffing"
    )
    inspect.add_argument(
        "--no-discover",
        action="store_true",
        help="skip the contexture_discover step",
    )
    inspect.add_argument(
        "--roster-budget",
        type=int,
        default=None,
        metavar="CHARS",
        help=(
            "render the bootstrap roster against a different budget, to see "
            "where it gets cut (default: the one hosts actually get)"
        ),
    )
    inspect.set_defaults(func=command_inspect)

    catalog = subcommands.add_parser(
        "catalog",
        help="print the stable node metadata compiled for application integrations",
        description=(
            "Describe the compiled Role, Skill and Tool graph without running "
            "business tools. Unlike inspect, this is a structural application "
            "read model rather than a replay of model disclosure."
        ),
    )
    catalog.add_argument(
        "refs", nargs="*", help="optional exact refs to print instead of the snapshot"
    )
    catalog.add_argument("--target", default=None, help="package.module:RoleClass")
    catalog.add_argument(
        "--view",
        choices=("application", "model", "surface"),
        default="model",
        help="compiled graph view (default: model)",
    )
    catalog.add_argument(
        "--detail",
        choices=("summary", "definition"),
        default="summary",
        help="node detail level (default: summary)",
    )
    catalog.add_argument(
        "--locale", default=None, help="presentation locale to resolve"
    )
    catalog.add_argument(
        "--select",
        action="append",
        default=[],
        metavar="REF",
        help="surface selector, repeatable; valid only with --view surface",
    )
    catalog.add_argument(
        "--json", action="store_true", help="emit deterministic JSON"
    )
    catalog.set_defaults(func=command_catalog)

    debug = subcommands.add_parser(
        "debug",
        help="browse compiled node metadata on a loopback-only HTTP port",
        description=(
            "Start a read-only local Node Catalog UI. This is not an MCP "
            "endpoint and has no Tool invocation route. The server refuses "
            "non-loopback bind addresses."
        ),
    )
    debug.add_argument("--target", default=None, help="package.module:RoleClass")
    debug.add_argument(
        "--view",
        choices=("application", "model", "surface"),
        default="application",
        help="compiled graph view (default: application)",
    )
    debug.add_argument(
        "--detail",
        choices=("summary", "definition"),
        default="definition",
        help="node detail level (default: definition)",
    )
    debug.add_argument("--locale", default=None, help="presentation locale to resolve")
    debug.add_argument(
        "--select",
        action="append",
        default=[],
        metavar="REF",
        help="surface selector, repeatable; valid only with --view surface",
    )
    debug.add_argument(
        "--host",
        default="127.0.0.1",
        help="loopback interface to bind (default: 127.0.0.1)",
    )
    debug.add_argument(
        "--port", type=int, default=8787, help="local port to bind (default: 8787)"
    )
    debug.set_defaults(func=command_debug)

    serve = subcommands.add_parser("serve", help="serve the project over MCP")
    serve.add_argument("target", nargs="?", help="package.module:RoleClass")
    add_transport_arguments(serve)
    serve.set_defaults(func=command_serve)

    demo = subcommands.add_parser(
        "demo", help="serve the bundled reference application"
    )
    add_transport_arguments(demo)
    demo.set_defaults(func=command_demo)

    subcommands.add_parser("managed", help="explicit trusted managed CLI (use managed --help)")
    subcommands.add_parser("store", help="offline managed store operations (use store --help)")

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    tokens = list(argv) if argv is not None else sys.argv[1:]
    if tokens and tokens[0] == "managed":
        # Isolated parser keeps managed JSON errors redacted without changing
        # legacy human-facing commands or importing managed storage eagerly.
        from .managed import main as managed_main
        return managed_main(tokens[1:])
    if tokens and tokens[0] == "store":
        from .store import main as store_main
        return store_main(tokens[1:])
    parser = build_parser()
    args = parser.parse_args(tokens)
    try:
        return int(args.func(args))
    except UsageError as exc:
        # stderr, always: under stdio the protocol owns stdout exclusively.
        print(f"contexture: {exc}", file=sys.stderr)
        return 2
    except ContextureError as exc:
        print(f"contexture: {exc}", file=sys.stderr)
        return 1


__all__ = ["build_parser", "main"]
