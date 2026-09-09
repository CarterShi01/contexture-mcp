"""Snapshots for the import surfaces promised to downstream applications."""

from __future__ import annotations

import contexture
import contexture.core
import contexture.core.emphasis
import contexture.core.model
import contexture.core.model.role
import contexture.server

import pytest


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
    "PostProcess",
    "PreProcess",
    "Resource",
    "Role",
    "Skill",
    "Tool",
    "__version__",
    "binding_instruction",
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
    "FixedSurfaceSelector",
    "HeaderRootSelector",
    "HeaderSurfaceSelector",
    "InMemoryTelemetry",
    "LOOPBACK",
    "Launch",
    "NodeUsage",
    "ROOTS_HEADER",
    "SELECT_HEADER",
    "RootCeiling",
    "RootSelection",
    "RootSelector",
    "SurfaceCeiling",
    "SurfaceSelection",
    "SurfaceSelector",
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


@pytest.mark.parametrize("module", [
    contexture, contexture.core, contexture.core.model, contexture.core.model.role,
])
def test_process_exports_are_identical_and_have_no_legacy_alias(module) -> None:
    assert module.PreProcess is contexture.PreProcess
    assert module.PostProcess is contexture.PostProcess
    assert not hasattr(module, "Publication")
    assert "Publication" not in getattr(module, "__all__", ())


def test_emphasis_has_only_one_public_authoring_function() -> None:
    assert contexture.binding_instruction is contexture.core.binding_instruction
    assert contexture.binding_instruction is contexture.core.emphasis.binding_instruction
    assert contexture.core.emphasis.__all__ == ["binding_instruction"]
    for module in (contexture, contexture.core, contexture.core.model):
        assert not hasattr(module, "framework_instruction")
        assert "framework_instruction" not in module.__all__


def test_removed_designation_is_not_a_constructor_alias() -> None:
    with pytest.raises(TypeError, match="publication"):
        contexture.Role(
            name="worker", description="Work.", instructions="Work.", publication=None,
        )
