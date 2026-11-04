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
from ..core.presentation import (
    Presentation,
    PresentationCatalog,
    resolve_presentation,
)
from .binding import TypeHintBinding
from .options import ContextureOptions
from .root_selector import RootSelector, SurfaceSelector
from .server import ContextureServer
from .surface import DisclosureSurface


@dataclass(frozen=True, slots=True)
class CompiledApplication:
    """The one build of an Application, before it is put on a transport."""

    name: str
    index: Index
    presentation: Presentation | PresentationCatalog = Presentation()
    prompt_root_refs: tuple[str, ...] = ()
    prompts: tuple[Prompt, ...] = ()
    resources: tuple[Resource, ...] = ()
    telemetry: Telemetry = field(default_factory=InMemoryTelemetry, repr=False)

    @property
    def disclosure(self) -> Disclosure:
        """The view used by local inspection and the MCP surface."""

        _, presentation = resolve_presentation(self.presentation)
        return Disclosure(
            self.index,
            frozenset(self.prompt_root_refs),
            node_presentations=presentation.nodes,
        )

    def server(
        self,
        *,
        locale: str | None = None,
        surface_selector: SurfaceSelector | None = None,
        root_selector: RootSelector | None = None,
    ) -> ContextureServer:
        """Create the immutable MCP server for this compiled declaration."""

        return ContextureServer(
            self.index,
            name=self.name,
            presentation=self.presentation,
            locale=locale,
            prompts=self.prompts,
            resources=self.resources,
            prompt_roots=self.prompt_root_refs,
            telemetry=self.telemetry,
            surface_selector=surface_selector,
            root_selector=root_selector,
        )

    def runtime(self) -> ApplicationRuntime:
        """Return the Host-neutral invocation API over this compiled forest."""

        return ApplicationRuntime(self.index, self.telemetry)


@dataclass(frozen=True, slots=True)
class CompiledDisclosureApplication:
    """An independently compiled application that can only be disclosed."""

    name: str
    index: Index
    presentation: Presentation | PresentationCatalog = Presentation()
    prompt_root_refs: tuple[str, ...] = ()
    prompts: tuple[Prompt, ...] = ()
    telemetry: Telemetry = field(default_factory=InMemoryTelemetry, repr=False)

    @property
    def disclosure(self) -> Disclosure:
        _, presentation = resolve_presentation(self.presentation)
        return Disclosure(
            self.index,
            frozenset(self.prompt_root_refs),
            node_presentations=presentation.nodes,
        )

    def server(
        self,
        *,
        locale: str | None = None,
        surface_selector: SurfaceSelector | None = None,
        root_selector: RootSelector | None = None,
    ) -> ContextureServer:
        resolved_locale, presentation = resolve_presentation(
            self.presentation, locale
        )
        disclosure = Disclosure(
            self.index,
            frozenset(self.prompt_root_refs),
            node_presentations=presentation.nodes,
        )
        surface = DisclosureSurface.of(
            disclosure,
            prompts=self.prompts,
            telemetry=self.telemetry,
            presentation=presentation.gateway,
        )
        return ContextureServer(
            self.index,
            name=self.name,
            presentation=presentation,
            locale=resolved_locale,
            telemetry=self.telemetry,
            surface=surface,
            surface_selector=surface_selector,
            root_selector=root_selector,
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
        prompt_roots=application.prompt_roots,
        channels=channels,
        prompts=application.prompts,
        resources=application.resources,
        presentation=application.presentation,
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
        prompt_roots=application.prompt_roots,
        prompts=application.prompts,
        presentation=application.presentation,
        telemetry=telemetry,
    )


def compile_disclosure_parts(
    *,
    name: str,
    roots: Iterable[object],
    prompt_roots: Iterable[object] = (),
    prompts: Iterable[object] = (),
    presentation: Presentation | PresentationCatalog = Presentation(),
    telemetry: Telemetry | None = None,
) -> CompiledDisclosureApplication:
    """Compile structural data into an unbound Index."""

    model_roots = tuple(roots)
    user_roots = tuple(prompt_roots)
    manager = ControllerManager()
    for root in (*model_roots, *user_roots):
        register_root(manager, root)
    normal_prompts = tuple(_published(entry, Prompt, "prompt") for entry in prompts)
    index = Index.unbound(manager)
    _validate_presentation(index, presentation)
    return CompiledDisclosureApplication(
        name=name,
        index=index,
        presentation=presentation,
        prompt_root_refs=tuple(
            index.ref_of(root) for root in index.roots[len(model_roots):]
        ),
        prompts=normal_prompts,
        telemetry=telemetry if telemetry is not None else InMemoryTelemetry(),
    )


def compile_parts(
    *,
    name: str,
    roots: Iterable[object],
    prompt_roots: Iterable[object] = (),
    channels: Any = None,
    prompts: Iterable[object] = (),
    resources: Iterable[object] = (),
    presentation: Presentation | PresentationCatalog = Presentation(),
    telemetry: Telemetry | None = None,
) -> CompiledApplication:
    """Compile known declaration parts through the one runtime path.

    ``compile_application`` is the public door.  This lower-level helper also
    lets the temporary legacy TOML adapter use exactly the same registration,
    binding, and publication path while old projects are still supported.
    """

    model_roots = tuple(roots)
    user_roots = tuple(prompt_roots)
    manager = ControllerManager(channels=channels)
    for root in (*model_roots, *user_roots):
        register_root(manager, root)

    normal_prompts = tuple(_published(entry, Prompt, "prompt") for entry in prompts)
    normal_resources = tuple(
        _published(entry, Resource, "resource") for entry in resources
    )
    index = Index.of(manager, bind=TypeHintBinding)
    _validate_presentation(index, presentation)
    return CompiledApplication(
        name=name,
        index=index,
        presentation=presentation,
        prompt_root_refs=tuple(
            index.ref_of(root) for root in index.roots[len(model_roots):]
        ),
        prompts=normal_prompts,
        resources=normal_resources,
        telemetry=telemetry if telemetry is not None else InMemoryTelemetry(),
    )


def build_server(
    application: Contexture,
    *,
    locale: str | None = None,
) -> ContextureServer:
    """Compile one Application and return the server that serves it."""

    return compile_application(application).server(locale=locale)


def serve(
    application: Contexture,
    options: ContextureOptions | None = None,
    *,
    locale: str | None = None,
) -> None:
    """Compile and serve an Application over the requested transport."""

    build_server(application, locale=locale).start(options)


def _published(entry: object, expected: type[Any], label: str) -> Any:
    built = published(entry)
    if not isinstance(built, expected):
        raise TypeError(
            f"{entry!r} is a {built.kind}, not a {label}; declare it in the "
            f"Application's `{label}s` collection."
        )
    return built


def _validate_presentation(
    index: Index,
    source: Presentation | PresentationCatalog,
) -> None:
    """Refuse translation typos in every locale, not only the selected one."""

    if isinstance(source, Presentation):
        localized = ((None, source),)
    elif isinstance(source, PresentationCatalog):
        localized = tuple(source.locales.items())
    else:
        raise ModelValidationError(
            "Presentation source must be a Presentation or PresentationCatalog."
        )
    for locale, presentation in localized:
        for ref in presentation.nodes:
            if ref not in index:
                context = f" for locale {locale!r}" if locale is not None else ""
                raise ModelValidationError(
                    f"Presentation{context} names unknown node ref {ref!r}."
                )


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
