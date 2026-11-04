"""Human-facing names for one Contexture application and its fixed gateway.

Machine identity and presentation have different compatibility rules.  The
application ``name`` and the five gateway tool names are stable protocol
identifiers; these values are labels a capable Host may show to a person.
Keeping the two apart lets a product use its own language without changing
the names an agent calls or the schemas it relies on.

This module deliberately has no dependency on the MCP SDK.  A declaration can
therefore choose presentation while remaining as lazy and Host-neutral as the
rest of Contexture's authoring API.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Mapping, TypeAlias

from .constants import (
    DISCOVER_TOOL,
    INSPECT_TOOL,
    INVOKE_READ_ONLY_TOOL,
    INVOKE_TOOL,
    OPEN_TOOL,
)
from .errors import ModelValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayPresentation:
    """Human-readable titles for the fixed model-controlled gateway.

    The defaults describe intent rather than implementation.  Applications may
    replace them with product language, but cannot change the corresponding
    machine names, descriptions, schemas, or safety annotations.
    """

    discover: str = "Discover capabilities"
    inspect: str = "Inspect capabilities"
    open: str = "Open capability"
    invoke_read_only: str = "Read data"
    invoke: str = "Make a change"

    def __post_init__(self) -> None:
        for declared in fields(self):
            value = getattr(self, declared.name)
            if not isinstance(value, str) or not value.strip():
                raise ModelValidationError(
                    "A gateway presentation title must be a non-empty string; "
                    f"`{declared.name}` was {value!r}."
                )
            object.__setattr__(self, declared.name, value.strip())

    def title_for(self, tool_name: str) -> str:
        """Return the title paired with one fixed gateway machine name."""

        titles = {
            DISCOVER_TOOL: self.discover,
            INSPECT_TOOL: self.inspect,
            OPEN_TOOL: self.open,
            INVOKE_READ_ONLY_TOOL: self.invoke_read_only,
            INVOKE_TOOL: self.invoke,
        }
        try:
            return titles[tool_name]
        except KeyError as error:  # pragma: no cover - framework invariant
            raise ValueError(f"{tool_name!r} is not a Contexture gateway tool.") from error


@dataclass(frozen=True, slots=True, kw_only=True)
class NodePresentation:
    """Human-facing copy for one stable Contexture ref.

    ``title`` is a short display label. ``description`` optionally localizes
    the routing sentence an agent uses to choose a branch. Neither value
    changes the node's machine name, reference, instructions, or behavior.
    """

    title: str
    description: str | None = None

    def __post_init__(self) -> None:
        for name in ("title", "description"):
            value = getattr(self, name)
            if value is None and name == "description":
                continue
            if not isinstance(value, str) or not value.strip():
                raise ModelValidationError(
                    f"Node presentation `{name}` must be a non-empty string."
                )
            object.__setattr__(self, name, value.strip())


@dataclass(frozen=True, slots=True, kw_only=True)
class Presentation:
    """One language's human identity, gateway labels, and node routing copy."""

    title: str | None = None
    description: str | None = None
    gateway: GatewayPresentation | None = None
    nodes: Mapping[str, NodePresentation] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("title", "description"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ModelValidationError(
                    f"Presentation `{name}` must be None or a non-empty string."
                )
            object.__setattr__(self, name, value.strip())
        if self.gateway is not None and not isinstance(
            self.gateway, GatewayPresentation
        ):
            raise ModelValidationError(
                "Presentation `gateway` must be None or a GatewayPresentation."
            )

        nodes: dict[str, NodePresentation] = {}
        if not isinstance(self.nodes, Mapping):
            raise ModelValidationError(
                "Presentation `nodes` must map stable refs to NodePresentation values."
            )
        for ref, value in self.nodes.items():
            if not isinstance(ref, str) or not ref.strip():
                raise ModelValidationError(
                    "A presentation node ref must be a non-empty string."
                )
            normalized_ref = ref.strip()
            if normalized_ref in nodes:
                raise ModelValidationError(
                    f"Presentation declares node ref {normalized_ref!r} twice."
                )
            if not isinstance(value, NodePresentation):
                raise ModelValidationError(
                    f"Presentation node {normalized_ref!r} must be a NodePresentation."
                )
            nodes[normalized_ref] = value
        object.__setattr__(self, "nodes", MappingProxyType(nodes))


@dataclass(frozen=True, slots=True, kw_only=True)
class PresentationCatalog:
    """Localized presentations selected by a normalized BCP-47-style tag.

    Resolution tries the exact tag, successively broader parent tags, and the
    declared default. For example, ``zh-Hans-CN`` tries ``zh-Hans-CN``,
    ``zh-Hans``, ``zh``, then the default. The catalog is open to any future
    locale; Contexture gives no special status to English or Chinese.
    """

    default_locale: str
    locales: Mapping[str, Presentation]

    def __post_init__(self) -> None:
        default = normalize_locale(self.default_locale)
        normalized: dict[str, Presentation] = {}
        if not isinstance(self.locales, Mapping) or not self.locales:
            raise ModelValidationError(
                "A PresentationCatalog needs at least one locale."
            )
        for locale, presentation in self.locales.items():
            tag = normalize_locale(locale)
            if tag in normalized:
                raise ModelValidationError(
                    f"PresentationCatalog declares locale {tag!r} twice."
                )
            if not isinstance(presentation, Presentation):
                raise ModelValidationError(
                    f"Presentation locale {tag!r} must contain a Presentation."
                )
            normalized[tag] = presentation
        if default not in normalized:
            raise ModelValidationError(
                f"PresentationCatalog default locale {default!r} is not declared."
            )
        object.__setattr__(self, "default_locale", default)
        object.__setattr__(self, "locales", MappingProxyType(normalized))

    def resolve(self, locale: str | None = None) -> tuple[str, Presentation]:
        """Return the matched locale and immutable presentation."""

        requested = normalize_locale(locale or self.default_locale)
        parts = requested.split("-")
        candidates = ["-".join(parts[:end]) for end in range(len(parts), 0, -1)]
        candidates.append(self.default_locale)
        for candidate in candidates:
            presentation = self.locales.get(candidate)
            if presentation is not None:
                return candidate, presentation
        raise AssertionError("the validated default locale must resolve")


PresentationSource: TypeAlias = Presentation | PresentationCatalog


def normalize_locale(locale: str) -> str:
    """Normalize a practical BCP-47 language tag without fixing a locale set."""

    if not isinstance(locale, str) or not locale.strip():
        raise ModelValidationError("A presentation locale must be a non-empty string.")
    raw_parts = locale.strip().replace("_", "-").split("-")
    if (
        not 2 <= len(raw_parts[0]) <= 8
        or not raw_parts[0].isalpha()
        or any(not part or len(part) > 8 or not part.isalnum() for part in raw_parts[1:])
    ):
        raise ModelValidationError(
            f"Presentation locale {locale!r} is not a valid BCP-47-style tag."
        )
    normalized = [raw_parts[0].lower()]
    for part in raw_parts[1:]:
        if len(part) == 4 and part.isalpha():
            normalized.append(part.title())
        elif (len(part) == 2 and part.isalpha()) or (
            len(part) == 3 and part.isdigit()
        ):
            normalized.append(part.upper())
        else:
            normalized.append(part.lower())
    return "-".join(normalized)


def resolve_presentation(
    source: PresentationSource,
    locale: str | None = None,
) -> tuple[str | None, Presentation]:
    """Resolve either a language-neutral presentation or a localized catalog."""

    if isinstance(source, PresentationCatalog):
        return source.resolve(locale)
    if not isinstance(source, Presentation):
        raise ModelValidationError(
            "Presentation source must be a Presentation or PresentationCatalog."
        )
    return (normalize_locale(locale) if locale is not None else None), source


__all__ = [
    "GatewayPresentation",
    "NodePresentation",
    "Presentation",
    "PresentationCatalog",
    "PresentationSource",
    "normalize_locale",
    "resolve_presentation",
]
