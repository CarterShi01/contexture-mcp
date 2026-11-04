"""The Node Catalog is a graph read model, never a second disclosure path."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from contexture import (
    Channels,
    Contexture,
    NodePresentation,
    PreProcess,
    Presentation,
    PresentationCatalog,
    Role,
    Skill,
    Tool,
)
from contexture.core.errors import ModelValidationError
from contexture.core.model.root_selection import (
    SurfaceSelection,
    bound_surface_selection,
)
from contexture.introspection import (
    CatalogDetail,
    CatalogView,
    SCHEMA,
    canonical_digest,
)
from contexture.server import compile_application, compile_disclosure_application


class Prepare(PreProcess):
    def __init__(self) -> None:
        super().__init__(
            name="prepare",
            description="Prepare the work.",
            instructions="Collect the evidence first.",
        )


class Review(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="review",
            description="Review one finding.",
            instructions="Compare the finding with the contract.",
        )


class Record(Tool):
    calls = 0

    def __init__(self) -> None:
        super().__init__(
            name="record",
            description="Record an accepted finding.",
        )

    async def invoke(self, finding: str, accepted: bool = False) -> dict:
        type(self).calls += 1
        return {"finding": finding, "accepted": accepted}


class Root(Role):
    def __init__(self) -> None:
        super().__init__(
            name="root",
            description="Coordinate review work.",
            instructions="Review before recording.",
            pre_process=Prepare(),
            skills=[Review()],
            tools=[Record()],
        )


class Commands(Role):
    def __init__(self) -> None:
        super().__init__(
            name="commands",
            description="Commands chosen by a person.",
            instructions="Wait for a person to choose.",
        )


APP = Contexture(
    name="catalog-test",
    roots=(Root,),
    prompt_roots=(Commands,),
    presentation=Presentation(
        nodes={
            "root": NodePresentation(
                title="Review root", description="Choose review work."
            )
        }
    ),
)


def test_application_catalog_joins_node_index_disclosure_and_binding_facts() -> None:
    compiled = compile_application(APP)
    catalog = compiled.catalog(
        view=CatalogView.APPLICATION, detail=CatalogDetail.DEFINITION
    )
    snapshot = catalog.snapshot()

    assert snapshot.roots == ("root", "commands")
    assert snapshot.counts == {"role": 3, "skill": 1, "tool": 1}
    assert tuple(node.ref for node in snapshot.nodes) == (
        "root",
        "root/prepare",
        "root/review",
        "root/record",
        "commands",
    )

    root = catalog.get("root")
    assert root.presentation is not None
    assert root.presentation.title == "Review root"
    assert root.presentation.description == "Choose review work."
    assert [(member.ref, member.slot.value) for member in root.members] == [
        ("root/prepare", "pre_process"),
        ("root/review", "skill"),
        ("root/record", "tool"),
    ]
    assert root.role is not None
    assert root.role.authored_instructions == "Review before recording."
    assert root.role.effective_instructions.endswith("Review before recording.")
    assert "contexture-mcp framework instruction" in root.role.effective_instructions
    assert root.role.pre_process_ref == "root/prepare"

    tool = catalog.get("root/record")
    assert tool.tool is not None and tool.tool.execution_available
    assert tool.tool.read_only is False
    assert tool.tool.input_schema is not None
    assert tool.tool.input_schema["required"] == ("finding",)

    prompt = catalog.get("commands")
    assert prompt.plane == "prompt"
    assert prompt.parent_ref is None


def test_model_and_surface_views_apply_the_framework_visibility_rules() -> None:
    compiled = compile_application(APP)
    model = compiled.catalog(view="model", detail="summary")
    assert model.roots == ("root",)
    assert "commands" not in {node.ref for node in model.snapshot().nodes}

    surface = compiled.catalog(
        view="surface",
        detail="definition",
        surface_selection=SurfaceSelection.only("root/review"),
    )
    assert surface.roots == ("root/review",)
    assert tuple(node.ref for node in surface.snapshot().nodes) == ("root/review",)
    selected = surface.get("root/review")
    assert selected.parent_ref is None
    assert selected.ordinal == 0
    assert selected.skill is not None


def test_surface_root_ordinals_cover_mixed_promoted_and_top_level_roots() -> None:
    compiled = compile_application(
        Contexture(name="mixed-selection", roots=(Root, Commands))
    )

    surface = compiled.catalog(
        view="surface",
        detail="summary",
        surface_selection=SurfaceSelection.only(("root/review", "commands")),
    ).snapshot()

    assert surface.roots == ("root/review", "commands")
    assert [(node.ref, node.ordinal) for node in surface.nodes] == [
        ("root/review", 0),
        ("commands", 1),
    ]


def test_catalog_resolves_localized_presentation_with_the_server_rules() -> None:
    localized = Contexture(
        name="localized-catalog",
        roots=(Root,),
        presentation=PresentationCatalog(
            default_locale="en",
            locales={
                "en": Presentation(
                    nodes={"root": NodePresentation(title="Review root")}
                ),
                "zh": Presentation(
                    nodes={
                        "root": NodePresentation(
                            title="审查根节点", description="协调审查工作。"
                        )
                    }
                ),
            },
        ),
    )

    snapshot = compile_application(localized).catalog(
        view="model", locale="zh-CN"
    ).snapshot()

    assert snapshot.application.locale == "zh"
    root = next(node for node in snapshot.nodes if node.ref == "root")
    assert root.presentation is not None
    assert root.presentation.title == "审查根节点"
    assert root.presentation.description == "协调审查工作。"


def test_disclosure_only_tool_never_invents_an_execution_contract() -> None:
    compiled = compile_disclosure_application(APP)
    tool = compiled.catalog(
        view="model", detail="definition"
    ).get("root/record")

    assert tool.tool is not None
    assert tool.tool.execution_available is False
    assert tool.tool.read_only is None
    assert tool.tool.input_schema is None
    assert tool.as_json()["tool"] == {"execution_available": False}


class SourceContributor:
    namespace = "example/source"

    def describe(self, ref, node, graph):  # type: ignore[no-untyped-def]
        del node, graph
        return {"ref": ref, "items": [1, 2.0]} if ref == "root" else None


def test_extensions_are_namespaced_frozen_and_digest_portable() -> None:
    compiled = compile_application(APP)
    catalog = compiled.catalog(
        view="model", contributors=(SourceContributor(),)
    )
    root = catalog.get("root")
    assert root.as_json()["extensions"] == {
        "example/source": {"ref": "root", "items": [1, 2]}
    }

    first = catalog.snapshot().to_json(indent=None)
    second = compiled.catalog(
        view="model", contributors=(SourceContributor(),)
    ).snapshot().to_json(indent=None)
    assert first == second
    parsed = json.loads(first)
    assert parsed["schema"] == SCHEMA
    assert parsed["content_digest"].startswith("sha256:")
    body = {key: value for key, value in parsed.items() if key != "content_digest"}
    assert canonical_digest(body) == parsed["content_digest"]

    definition = compiled.catalog(view="model", detail="definition")
    assert [node.ref for node in definition.list(query="boolean")] == ["root/record"]


def test_canonical_digest_orders_keys_by_unicode_code_point() -> None:
    assert canonical_digest({"\ue000": 1, "😀": 2}) == (
        "sha256:871954531859c7572c6279f90eb83a594ddc3a289e8bdc28d2a84ffb8c1a1703"
    )


def test_normative_schema_accepts_runtime_and_disclosure_only_snapshots() -> None:
    schema = json.loads(
        (Path(__file__).parents[1] / "spec" / "node-catalog.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = Draft202012Validator(schema)
    for compiled in (compile_application(APP), compile_disclosure_application(APP)):
        payload = compiled.catalog(
            view="application", detail="definition"
        ).snapshot().as_json()
        assert list(validator.iter_errors(payload)) == []

    summary = compile_application(APP).catalog(
        view="model", detail="summary"
    ).snapshot().as_json()
    assert list(validator.iter_errors(summary)) == []

    invalid_summary = deepcopy(summary)
    invalid_summary["nodes"][0]["role"] = {
        "authored_instructions": "invented",
        "effective_instructions": "invented",
        "pre_process_ref": None,
        "post_process_ref": None,
    }
    assert list(validator.iter_errors(invalid_summary))

    invalid_unbound = deepcopy(
        compile_disclosure_application(APP).catalog(
            view="model", detail="definition"
        ).snapshot().as_json()
    )
    unbound_tool = next(
        node for node in invalid_unbound["nodes"] if node["kind"] == "tool"
    )
    unbound_tool["tool"] = {
        "execution_available": True,
        "read_only": True,
        "input_schema": {"type": "object"},
    }
    assert list(validator.iter_errors(invalid_unbound))


@pytest.mark.parametrize("namespace", ["", "source", "/source", "x/"])
def test_extension_namespace_is_explicit(namespace: str) -> None:
    contributor = SourceContributor()
    contributor.namespace = namespace
    with pytest.raises(ModelValidationError, match="namespace"):
        compile_application(APP).catalog(
            view="model", contributors=(contributor,)
        )


def test_view_and_selection_must_be_explicitly_consistent() -> None:
    compiled = compile_application(APP)
    with pytest.raises(ModelValidationError, match="requires"):
        compiled.catalog(view="surface")
    with pytest.raises(ModelValidationError, match="only"):
        compiled.catalog(
            view="model", surface_selection=SurfaceSelection.only("root")
        )


def test_catalog_is_not_implicitly_narrowed_by_a_request_local_selection() -> None:
    compiled = compile_application(APP)

    with bound_surface_selection(SurfaceSelection.only("root/review")):
        catalog = compiled.catalog(view="model")

    assert catalog.roots == ("root",)
    assert tuple(node.ref for node in catalog.snapshot().nodes) == (
        "root",
        "root/prepare",
        "root/review",
        "root/record",
    )


def test_snapshot_value_model_rejects_cross_node_semantic_drift() -> None:
    snapshot = compile_application(APP).catalog(
        view="model", detail="definition"
    ).snapshot()

    with pytest.raises(ModelValidationError, match="repeat a root"):
        replace(snapshot, roots=("root", "root"))
    with pytest.raises(ModelValidationError, match="counts disagree"):
        replace(snapshot, counts={"role": 99, "skill": 1, "tool": 1})

    record = next(node for node in snapshot.nodes if node.ref == "root/record")
    drifted = replace(record, uses=("absent",))
    with pytest.raises(ModelValidationError, match="not reciprocal"):
        replace(
            snapshot,
            nodes=tuple(drifted if node is record else node for node in snapshot.nodes),
        )


class DormantChannels(Channels):
    opened = 0
    closed = 0

    async def open(self) -> None:
        type(self).opened += 1

    async def close(self) -> None:
        type(self).closed += 1


def test_catalog_has_no_tool_channel_or_telemetry_side_effects() -> None:
    Record.calls = 0
    DormantChannels.opened = 0
    DormantChannels.closed = 0
    compiled = compile_application(
        Contexture(name="catalog-effects", roots=(Root,), channels=DormantChannels)
    )

    snapshot = compiled.catalog(view="model", detail="definition").snapshot()

    assert Record.calls == 0
    assert DormantChannels.opened == 0
    assert DormantChannels.closed == 0
    for node in snapshot.nodes:
        assert compiled.telemetry.usage(node.ref).call_count == 0


class FailingContributor:
    namespace = "example/failing"

    def describe(self, ref, node, graph):  # type: ignore[no-untyped-def]
        del node, graph
        if ref == "root/review":
            raise RuntimeError("source projection failed")
        return {"visited": ref}


def test_contributor_failure_is_atomic_and_does_not_taint_the_next_catalog() -> None:
    compiled = compile_application(APP)

    with pytest.raises(RuntimeError, match="source projection failed"):
        compiled.catalog(view="model", contributors=(FailingContributor(),))

    assert all(
        node.extensions == {}
        for node in compiled.catalog(view="model").snapshot().nodes
    )

