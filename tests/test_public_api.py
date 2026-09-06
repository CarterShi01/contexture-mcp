"""Snapshots for the import surfaces promised to downstream applications."""

from __future__ import annotations

import contexture
import contexture.server


AUTHORING_API = {
    "Channels",
    "Contexture",
    "ContextureError",
    "DeclarationError",
    "DuplicateNameError",
    "ModelValidationError",
    "NodeNotFoundError",
    "Principal",
    "Prompt",
    "Resource",
    "Role",
    "Skill",
    "Tool",
    "__version__",
    "current_graph",
    "current_principal",
    "current_telemetry",
}

SERVER_API = {
    "ApplicationRuntime",
    "Auth",
    "CompiledApplication",
    "CompiledDisclosureApplication",
    "ContextureOptions",
    "ContextureServer",
    "DEFAULT_HOST",
    "DEFAULT_PATH",
    "DEFAULT_PORT",
    "FixedRootSelector",
    "HeaderRootSelector",
    "InMemoryTelemetry",
    "LOOPBACK",
    "Launch",
    "NodeUsage",
    "ROOTS_HEADER",
    "RootCeiling",
    "RootSelection",
    "RootSelector",
    "ServeError",
    "Telemetry",
    "TokenVerifier",
    "Transport",
    "build_server",
    "claude_code_config",
    "cli_commands",
    "codex_config",
    "compile_application",
    "compile_disclosure_application",
    "compile_disclosure_parts",
    "compile_parts",
    "configure_logging",
    "cursor_config",
    "serve",
}


def test_authoring_api_is_deliberate() -> None:
    assert set(contexture.__all__) == AUTHORING_API


def test_server_api_is_deliberate() -> None:
    assert set(contexture.server.__all__) == SERVER_API


def test_every_promised_name_resolves() -> None:
    for module, promised in (
        (contexture, AUTHORING_API),
        (contexture.server, SERVER_API),
    ):
        for name in promised:
            assert getattr(module, name) is not None
