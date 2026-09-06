"""Finding a project, reading what it declared, and resolving it to objects.

A business project is *served*, not distributed. It has no build system and is
never installed into the environment, so this module locates it the way Scrapy
locates a crawl project: walk up for the marker — a `pyproject.toml` carrying a
`[tool.contexture]` table — and put that directory on `sys.path`.

Moves when the packaging ecosystem does, which is why it is not in `main`.
"""

from __future__ import annotations

import importlib
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..core.model.node import ContextNode
from ..core.model.role import Role
from ..application import Contexture
from .usage import UsageError

#: The table `serve` and `list` read, and the marker they find a project by.
CONFIG_TABLE = "contexture"

#: The reference application this package ships, for `contexture demo` and for
#: `contexture inspect` run with no project in sight.
DEMO_TARGET = "contexture.demo:KubernetesPlatform"
DEMO_APP = "contexture.demo.server:app"

#: What the demo publishes, one target per plane. Named rather than imported,
#: for the same reason its roots are: this module resolves the demo at run time
#: and does not depend on it.
DEMO_PUBLISH = (
    "contexture.demo.server:COMMANDS",
    "contexture.demo.server:DOCUMENTS",
)

def find_project(start: Path | None = None) -> Path | None:
    """Walk up for the nearest `pyproject.toml` carrying our table."""

    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / "pyproject.toml"
        if candidate.is_file() and CONFIG_TABLE in _read_toml(candidate).get(
            "tool", {}
        ):
            return directory
    return None


def _read_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


@dataclass(slots=True, frozen=True, kw_only=True)
class ProjectConfig:
    """What `[tool.contexture]` said, plus where it was found."""

    root: Path
    name: str
    roots: tuple[str, ...]
    app: str | None = None

    #: What the project puts on the prompt and resource primitives, as
    #: `package.module:NAME` targets. Optional: a declaration reaches an agent
    #: through the gateway whether or not anybody named a way in for a person.
    publish: tuple[str, ...] = ()

    #: What every capability in this project may reach outside the process,
    #: as one `package.module:ClassName` target. Optional, and **one** rather
    #: than a list: a manager holds one handle, and two would be two answers
    #: to what a capability reaches.
    #:
    #: A live object cannot be written in a TOML table — but a **class** can be
    #: named, and a class is a zero-argument factory. That is the same door
    #: `roots` goes through (ADR 013), and it is why this key can exist at all:
    #: since ADR 015 a handle with a lifecycle *is* a class, where `provision`
    #: was a function returning a context manager and fitted no such rule.
    channels: str | None = None

    @classmethod
    def load(cls, root: Path) -> ProjectConfig:
        table = _read_toml(root / "pyproject.toml").get("tool", {}).get(
            CONFIG_TABLE, {}
        )
        app = table.get("app")
        legacy = {"name", "roots", "channels", "publish"} & set(table)
        if app is not None:
            if not isinstance(app, str) or not app.strip():
                raise UsageError(
                    f"{root / 'pyproject.toml'} must name `app` as \"package.module:app\"."
                )
            if legacy:
                raise UsageError(
                    f"{root / 'pyproject.toml'} names both `app` and legacy "
                    f"keys ({', '.join(sorted(legacy))}). Keep only `app`; it "
                    "is the application's one declaration."
                )
            return cls(root=root, name=root.name, roots=(), app=app.strip())
        targets = table.get("roots") or ()
        if isinstance(targets, str):
            targets = (targets,)
        if not targets:
            raise UsageError(
                f"{root / 'pyproject.toml'} has a [tool.contexture] table but no "
                "`roots`. List at least one, as \"package.module:RoleClass\"."
            )
        exposed = table.get("publish") or ()
        if isinstance(exposed, str):
            exposed = (exposed,)
        channels = table.get("channels")
        if isinstance(channels, (list, tuple)):
            raise UsageError(
                f"{root / 'pyproject.toml'} lists {len(channels)} `channels` "
                "targets. Name one: a manager holds one handle, and two would "
                "be two answers to what a capability reaches. Compose them "
                "inside a single Channels subclass instead."
            )
        return cls(
            root=root,
            name=str(table.get("name") or root.name),
            roots=tuple(str(target) for target in targets),
            publish=tuple(str(target) for target in exposed),
            channels=str(channels) if channels else None,
        )


def resolve_target(
    target: str,
    *,
    project: Path | None = None,
) -> Role | type[Role]:
    """Resolve `package.module:RoleClass` to the root it names.

    The **class**, not an instance of it. A declared root is turned into nodes
    by a `ControllerManager`, at registration, and building one here would put
    a second, never-registered copy in front of the one being served.

    Naming the class is required rather than inferred. A module that declares a
    dozen roles has no obvious root, and guessing wrong would swap what the
    server offers without failing.

    `project` is where the declaration is expected to live. Passing it turns a
    name the project shares with something already installed into a sentence
    rather than a server quietly built from somebody else's module — see
    `_require_declared_here`.
    """

    declared = _declared(target, project=project, key="roots", shape="package.module:RoleClass")
    if isinstance(declared, Role) or (
        isinstance(declared, type) and issubclass(declared, Role)
    ):
        return declared
    raise UsageError(
        f"{target} is a {type(declared).__name__}, not a Role subclass."
    )


def load_application(target: str, *, project: Path | None = None) -> Contexture:
    """Resolve the one lazy Application object a modern project exports."""

    if project is not None and str(project) not in sys.path:
        sys.path.append(str(project))
    declared = _declared(
        target,
        project=project,
        key="app",
        shape="package.module:app",
    )
    if isinstance(declared, Contexture):
        return declared
    raise UsageError(
        f"{target} is {declared!r}, not a Contexture application. Export "
        "`app = Contexture(name=..., roots=(... ,))`."
    )


def _declared(target: str, *, project: Path | None, key: str, shape: str) -> object:
    """Resolve one `package.module:NAME` to whatever the project put there.

    The one piece of reflection in this package, and it stays this small on
    purpose: a string becomes an attribute of a module, and nothing decides
    what that attribute *is*. Each caller says what it needed, because only the
    caller knows what a wrong answer would have broken. `shape` is what this
    key expects on the right of the colon, so a malformed target is refused
    with the form the reader was actually reaching for.
    """

    module_name, separator, attribute = target.partition(":")
    if not separator or not module_name or not attribute:
        raise UsageError(f"{target!r} must be written as \"{shape}\".")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise UsageError(
            f"Cannot import {module_name!r} ({exc}). Run this from inside the "
            f"project, or check `{key}` in pyproject.toml."
        ) from exc
    _require_declared_here(module, module_name, project)
    try:
        return getattr(module, attribute)
    except AttributeError:
        raise UsageError(
            f"{module_name!r} has no attribute {attribute!r}."
        ) from None


def _require_declared_here(
    module: object,
    module_name: str,
    project: Path | None,
) -> None:
    """Refuse a module that resolved to something outside the project.

    A project directory goes on the end of `sys.path`, so a top-level name it
    shares with an installed distribution resolves to the *installed* one. That
    is the safe half of appending — nothing of Python's own is shadowed — but
    the failure it leaves is the quiet kind: a server built from somebody
    else's module, with no error anywhere. Naming it is one comparison.
    """

    if project is None:
        return
    origin = getattr(module, "__file__", None)
    if origin is None:
        return
    resolved = Path(origin).resolve()
    if resolved.is_relative_to(project.resolve()):
        return
    raise UsageError(
        f"{module_name!r} resolved to {resolved}, which is outside this "
        f"project ({project}). Something already installed answers to that "
        "name, so the project's own module is unreachable. Rename it, or name "
        "a module this project does not share with a dependency."
    )


def load_roots(
    targets: Sequence[str],
    *,
    project: Path | None,
) -> list[Role | type[Role]]:
    # Appended, never inserted at the front. A project directory holds whatever
    # its author put there, and a file called `types.py` or `logging.py` beside
    # `pyproject.toml` would shadow the standard library for the whole process
    # — the SDK included — if this directory outranked it. Appending gives the
    # standard library and every installed distribution priority, and
    # `_require_declared_here` turns the one failure that trade introduces into
    # a sentence.
    if project is not None and str(project) not in sys.path:
        sys.path.append(str(project))
    return [resolve_target(target, project=project) for target in targets]


def load_published(
    targets: Sequence[str],
    *,
    project: Path | None = None,
) -> list[object]:
    """Resolve each `package.module:NAME` naming a prompt or a resource.

    A target may name one entry or a sequence of them, because a project that
    declares eight commands should not have to list eight lines here as well.
    `sys.path` is already set by `load_roots`, which runs first.

    `project` is checked the same way `resolve_target` checks it, and is left
    out for a target that is deliberately not in a project — the bundled demo
    lives inside this package.
    """

    entries: list[object] = []
    for target in targets:
        declared = _declared(target, project=project, key="publish", shape="package.module:NAME")
        if isinstance(declared, (list, tuple)):
            entries.extend(declared)
        else:
            entries.append(declared)
    return entries


def load_channels(target: str | None, *, project: Path | None = None) -> object:
    """Resolve what every capability in this project may reach.

    **A class, and it is built here.** That is the whole trick, and it is the
    same one `roots` uses: a live handle cannot be written into a TOML table,
    but a class can be named, and a class is a zero-argument factory. So a
    project with downstream connections is served by `contexture serve` like
    any other, instead of having to write the entry point the README opens by
    promising it will not need.

    It only became possible in ADR 015. `provision` was a *function* returning
    an async context manager, and no rule in this package turns a named
    function into a live object — `Channels` is a class, and the rule already
    existed.

    What it must not be is a node. A `Role`, `Skill` or `Tool` here is a
    `roots` entry written under the wrong key, and building it as a handle
    would give every capability in the project a second, never-registered
    controller to reach for.
    """

    if not target:
        return None
    declared = _declared(
        target,
        project=project,
        key="channels",
        shape="package.module:ChannelsClass",
    )
    if isinstance(declared, (list, tuple)):
        raise UsageError(
            f"{target} names {len(declared)} objects. `channels` is one "
            "handle; compose several inside a single Channels subclass, in "
            "its own `open`."
        )
    if isinstance(declared, ContextNode) or (
        isinstance(declared, type) and issubclass(declared, ContextNode)
    ):
        raise UsageError(
            f"{target} is a {getattr(declared, 'kind', 'node')}, which belongs "
            "in `roots`, not `channels`. A handle is what a capability reaches "
            "*outside* this process."
        )
    # A class is a zero-argument factory — the same rule `ControllerManager`
    # applies to a root. An already-built value passes through, which is what
    # keeps a module-level singleton nameable.
    return declared() if isinstance(declared, type) else declared


@dataclass(slots=True, frozen=True, kw_only=True)
class Serving:
    """What a command was asked to serve, however it was asked.

    A named object rather than the tuple this used to be: it grew a field every
    time the project table learned a key, and three of its four callers were
    unpacking positions they did not use.
    """

    #: `package.module:RoleClass` targets, one or many.
    roots: tuple[str, ...]
    app: str | None = None

    #: Where the project lives, or None when a target was named explicitly and
    #: there is nothing to check it against.
    project: Path | None = None

    #: What a connecting host sees. None falls back to the framework default.
    name: str | None = None

    publish: tuple[str, ...] = ()

    #: One `package.module:ClassName`, or None for a server that reaches
    #: nothing outside its own process — which is the ordinary case.
    channels: str | None = None


def _targets_and_project(target: str | None, *, or_demo: bool = False) -> Serving:
    """Resolve what to serve: an explicit target, or the enclosing project.

    `or_demo` belongs to the one command that has something worth saying with
    no project in sight. `inspect` replays a disclosure, and a reader who has
    just installed this package — or who is standing in the framework's own
    checkout, which has no `[tool.contexture]` project of its own — should get
    the bundled demo rather than a refusal. Serving is not offered the same
    courtesy: starting a server nobody asked for is a different kind of
    surprise from printing one.
    """

    project = find_project()
    if target:
        return Serving(roots=(target,), project=project)
    if project is None:
        if or_demo:
            return Serving(
                roots=(DEMO_TARGET,),
                name="contexture-demo",
                publish=DEMO_PUBLISH,
                app=DEMO_APP,
            )
        raise UsageError(
            "No [tool.contexture] table found in this directory or above it. "
            "Run inside a project, name a root explicitly "
            "(contexture serve pkg.mod:RoleClass), or start one with "
            "`contexture new <name>`."
        )
    config = ProjectConfig.load(project)
    return Serving(
        roots=config.roots,
        project=project,
        name=config.name,
        publish=config.publish,
        channels=config.channels,
        app=config.app,
    )


__all__ = [
    "CONFIG_TABLE",
    "DEMO_PUBLISH",
    "DEMO_APP",
    "DEMO_TARGET",
    "ProjectConfig",
    "Serving",
    "find_project",
    "load_application",
    "load_roots",
    "load_channels",
    "load_published",
    "resolve_target",
]
