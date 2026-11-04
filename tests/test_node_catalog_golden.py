"""The Node Catalog extension has an independent language-neutral fixture."""

from __future__ import annotations

import json
from pathlib import Path

from contexture.demo.server import app
from contexture.introspection import canonical_digest
from contexture.server import compile_application


ROOT = Path(__file__).parents[1]


def test_reference_application_produces_the_node_catalog_extension_golden() -> None:
    fixture = json.loads(
        (ROOT / "spec/node-catalog/fixtures/reference-catalog.json").read_text(
            encoding="utf-8"
        )
    )
    assert fixture["application_fixture"] == "../../fixtures/reference-application.json"
    actual = compile_application(app).catalog(
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
