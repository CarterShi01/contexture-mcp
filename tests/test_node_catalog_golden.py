"""The Node Catalog extension has an independent language-neutral fixture."""

from __future__ import annotations

import json
from pathlib import Path

from contexture import Role, Skill, Tool
from contexture.core.model.index import Index
from contexture.demo.server import app
from contexture.introspection import canonical_digest
from contexture.server import compile_application


ROOT = Path(__file__).parents[1]


def reference_declaration(index: Index, name: str) -> dict[str, object]:
    """Capture native author facts, independently of Catalog serialization."""
    roles: list[dict[str, object]] = []
    skills: list[dict[str, object]] = []
    tools: list[dict[str, object]] = []
    for ref, node in index.walk():
        row: dict[str, object] = {
            "ref": ref, "name": node.name, "description": node.description,
            "uses": list(index.uses_of(ref)),
        }
        if isinstance(node, Role):
            row["instructions"] = node.instructions
            for slot in ("children", "skills", "tools"):
                row[slot] = [index.ref_of(child) for child in getattr(node, slot)]
            for slot in ("pre_process", "post_process"):
                process = getattr(node, slot)
                if process is not None:
                    row[slot] = index.ref_of(process)
            roles.append(row)
        elif isinstance(node, Skill):
            row["instructions"] = node.instructions
            skills.append(row)
        elif isinstance(node, Tool):
            row["read_only"] = node.read_only
            row["input_schema"] = index.binding_of(ref).schema
            tools.append(row)
        else:
            raise AssertionError(f"Unsupported reference declaration: {ref}")
    return {
        "application": {"name": name, "roots": [index.ref_of(node) for node in index.roots]},
        "roles": roles, "skills": skills, "tools": tools,
    }


def test_reference_application_produces_the_node_catalog_extension_golden() -> None:
    fixture_path = ROOT / "spec/node-catalog/fixtures/reference-catalog.json"
    fixture = json.loads(
        fixture_path.read_text(encoding="utf-8")
    )
    source = json.loads(
        (fixture_path.parent / fixture["application_fixture"]).read_text(encoding="utf-8")
    )
    compiled = compile_application(app)
    assert reference_declaration(compiled.index, compiled.name) == source
    actual = compiled.catalog(
        view=fixture["view"],
        detail=fixture["detail"],
        locale=fixture["locale"],
    ).snapshot().as_json()
    expected = json.loads(
        (ROOT / "spec/golden/node-catalog.json").read_text(encoding="utf-8")
    )

    assert actual == expected


def test_language_neutral_canonicalization_vectors() -> None:
    corpus = json.loads(
        (ROOT / "spec/node-catalog/canonicalization-vectors.json").read_text(
            encoding="utf-8"
        )
    )

    assert corpus["schema"] == "contexture.node-catalog-canonicalization/v1"
    for vector in corpus["vectors"]:
        canonical = json.dumps(
            vector["value"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        assert canonical == vector["canonical_json"], vector["name"]
        assert canonical_digest(vector["value"]) == vector["content_digest"]
