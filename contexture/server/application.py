"""Compile a lazy :class:`contexture.Contexture` declaration for serving.

This is the only bridge from the business-facing Application object to the
existing runtime kernel.  It deliberately performs no new indexing or
disclosure work: it constructs a ``ControllerManager``, lets ``Index`` compile
it, and hands that frozen index to the existing server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..application import Contexture
from ..core.errors import ModelValidationError
from ..core.mcp_interface import Prompt, Resource, published
from ..core.model import ControllerManager, register_root
from ..core.model.disclosure import Disclosure
from ..core.model.index import Index
from ..core.model.runtime import ApplicationRuntime
from ..core.model.telemetry import InMemoryTelemetry, Telemetry
from .binding import TypeHintBinding
from .options import ContextureOptions
from .server import ContextureServer
from .surface import DisclosureSurface


@dataclass(frozen=True, slots=True)
class CompiledApplication:
    """The one build of an Application, before it is put on a transport."""

    name: str
    index: Index
    prompts: tuple[Prompt, ...] = ()
    resources: tuple[Resource, ...] = ()
    telemetry: Telemetry = field(default_factory=InMemoryTelemetry, repr=False)

    @property
    def disclosure(self) -> Disclosure:
        """The view used by local inspection and the MCP surface."""

        return Disclosure(self.index)

    def server(self) -> ContextureServer:
        """Create the immutable MCP server for this compiled declaration."""

        return ContextureServer(
            self.index,
            name=self.name,
            prompts=self.prompts,
            resources=self.resources,
            telemetry=self.telemetry,
        )

    def runtime(self) -> ApplicationRuntime:
        """Return the Host-neutral invocation API over this compiled forest."""

        return ApplicationRuntime(self.index, self.telemetry)


@dataclass(frozen=True, slots=True)
class CompiledDisclosureApplication:
    """An independently compiled application that can only be disclosed."""

    name: str
    index: Index
    prompts: tuple[Prompt, ...] = ()
    telemetry: Telemetry = field(default_factory=InMemoryTelemetry, repr=False)

    @property
    def disclosure(self) -> Disclosure:
        return Disclosure(self.index)

    def server(self) -> ContextureServer:
        surface = DisclosureSurface.of(
            self.disclosure,
            prompts=self.prompts,
            telemetry=self.telemetry,
        )
        return ContextureServer(
            self.index,
            name=self.name,
            telemetry=self.telemetry,
            surface=surface,
        )


def compile_application(
    application: Contexture,
    *,
    telemetry: Telemetry | None = None,
) -> CompiledApplication:
    """Build one fresh forest from an Application's factories."""

    channels = application.channels() if application.channels is not None else None
    return compile_parts(
        name=application.name,
        roots=application.roots,
        channels=channels,
        prompts=application.prompts,
        resources=application.resources,
        telemetry=telemetry,
    )


def compile_disclosure_application(
    application: Contexture,
    *,
    telemetry: Telemetry | None = None,
) -> CompiledDisclosureApplication:
    """Compile an independent, progressive, non-executable application.

    The declaration syntax stays the same, but execution dependencies are
    rejected rather than ignored. This is the lower-level guarantee required
    by operational and architecture views: their MCP surface cannot acquire a
    Tool binding, a Resource door, or application Channels.
    """

    if application.channels is not None:
        raise ModelValidationError(
            "A disclosure-only Contexture application cannot declare Channels."
        )
    if application.resources:
        raise ModelValidationError(
            "A disclosure-only Contexture application cannot declare Resources; "
            "a Resource executes a read-only Tool."
        )
    return compile_disclosure_parts(
        name=application.name,
        roots=application.roots,
        prompts=application.prompts,
        telemetry=telemetry,
    )


def compile_disclosure_parts(
    *,
    name: str,
    roots: Iterable[object],
    prompts: Iterable[object] = (),
    telemetry: Telemetry | None = None,
) -> CompiledDisclosureApplication:
    """Compile structural data into an unbound Index."""

    manager = ControllerManager()
    for root in roots:
        register_root(manager, root)
    normal_prompts = tuple(_published(entry, Prompt, "prompt") for entry in prompts)
    return CompiledDisclosureApplication(
        name=name,
        index=Index.unbound(manager),
        prompts=normal_prompts,
        telemetry=telemetry if telemetry is not None else InMemoryTelemetry(),
    )


def compile_parts(
    *,
    name: str,
    roots: Iterable[object],
    channels: Any = None,
    prompts: Iterable[object] = (),
    resources: Iterable[object] = (),
    telemetry: Telemetry | None = None,
) -> CompiledApplication:
    """Compile known declaration parts through the one runtime path.

    ``compile_application`` is the public door.  This lower-level helper also
    lets the temporary legacy TOML adapter use exactly the same registration,
    binding, and publication path while old projects are still supported.
    """

    manager = ControllerManager(channels=channels)
    for root in roots:
        register_root(manager, root)

    normal_prompts = tuple(_published(entry, Prompt, "prompt") for entry in prompts)
    normal_resources = tuple(
        _published(entry, Resource, "resource") for entry in resources
    )
    return CompiledApplication(
        name=name,
        index=Index.of(manager, bind=TypeHintBinding),
        prompts=normal_prompts,
        resources=normal_resources,
        telemetry=telemetry if telemetry is not None else InMemoryTelemetry(),
    )


def build_server(application: Contexture) -> ContextureServer:
    """Compile one Application and return the server that serves it."""

    return compile_application(application).server()


def serve(
    application: Contexture,
    options: ContextureOptions | None = None,
) -> None:
    """Compile and serve an Application over the requested transport."""

    build_server(application).start(options)


def _published(entry: object, expected: type[Any], label: str) -> Any:
    built = published(entry)
    if not isinstance(built, expected):
        raise TypeError(
            f"{entry!r} is a {built.kind}, not a {label}; declare it in the "
            f"Application's `{label}s` collection."
        )
    return built


__all__ = [
    "CompiledApplication",
    "CompiledDisclosureApplication",
    "build_server",
    "compile_application",
    "compile_disclosure_application",
    "compile_disclosure_parts",
    "compile_parts",
    "serve",
]
