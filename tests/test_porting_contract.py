"""Deterministic checks for language-binding conformance metadata."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.verify_porting_contract import verify_assets, verify_manifest, verify_schema
from scripts.verify_porting_contract import (
    RULES,
    SPECIFICATION_REVISION,
    SPECIFICATION_VERSION,
    verify_release_bindings,
)


def _manifest() -> dict[str, object]:
    inventory = verify_assets()
    return {
        "$schema": "../conformance-manifest.schema.json",
        "repository": "https://example.com/contexture-binding",
        "revision": inventory["revision"],
        "specificationVersion": inventory["specificationVersion"],
        "status": "scaffold",
        "implementedRules": [1],
        "rules": {
            str(number): {
                "status": "implemented" if number == 1 else "not-started",
                "evidence": ["test/lazy.test"] if number == 1 else [],
            }
            for number in RULES
        },
        "fixtures": inventory["fixtures"],
        "golden": inventory["golden"],
        "notes": "Test manifest.",
    }


def _write(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_porting_assets_and_schema_are_complete() -> None:
    inventory = verify_assets()
    verify_schema()
    assert inventory["specificationVersion"] == SPECIFICATION_VERSION
    assert inventory["revision"] == SPECIFICATION_REVISION


def test_manifest_accepts_the_truthful_scaffold_state(tmp_path: Path) -> None:
    verify_manifest(_write(tmp_path, _manifest()), verify_assets())


@pytest.mark.parametrize("mutation", ["missing-rule", "false-credit", "wrong-revision", "false-status"])
def test_manifest_rejects_inconsistent_state(tmp_path: Path, mutation: str) -> None:
    manifest = deepcopy(_manifest())
    rules = manifest["rules"]
    assert isinstance(rules, dict)
    if mutation == "missing-rule":
        del rules[str(RULES[-1])]
    elif mutation == "false-credit":
        manifest["implementedRules"] = [1, 2]
    elif mutation == "wrong-revision":
        manifest["revision"] = "0" * 40
    else:
        manifest["status"] = "conformant"

    with pytest.raises(ValueError):
        verify_manifest(_write(tmp_path, manifest), verify_assets())


def _conformant_manifest() -> dict[str, object]:
    manifest = _manifest()
    rules = manifest["rules"]
    assert isinstance(rules, dict)
    for number in RULES:
        rules[str(number)] = {
            "status": "implemented",
            "evidence": [f"test/rule-{number}.test"],
        }
    manifest["implementedRules"] = list(RULES)
    manifest["status"] = "conformant"
    return manifest


def _binding_root(tmp_path: Path, language: str) -> Path:
    root = tmp_path / language
    conformance = root / "conformance"
    conformance.mkdir(parents=True)
    (conformance / "specification.json").write_text(
        json.dumps(_conformant_manifest()), encoding="utf-8"
    )
    source_schema = Path("spec/conformance-manifest.schema.json")
    (conformance / "specification.schema.json").write_text(
        source_schema.read_text(encoding="utf-8"), encoding="utf-8"
    )
    inventory = verify_assets()
    for directory in ("fixtures", "golden"):
        destination = conformance / directory
        destination.mkdir()
        for name in inventory[directory]:
            source = Path("spec") / directory / name
            (destination / name).write_bytes(source.read_bytes())
    return root


def test_release_verifier_accepts_both_synchronized_bindings(tmp_path: Path) -> None:
    roots = {
        language: _binding_root(tmp_path, language)
        for language in ("typescript", "go")
    }

    verify_release_bindings(roots)


@pytest.mark.parametrize(
    "mutation",
    ["missing-binding", "wrong-assets", "schema-drift", "fixture-byte-drift"],
)
def test_release_verifier_rejects_ecosystem_drift(
    tmp_path: Path, mutation: str
) -> None:
    roots = {
        language: _binding_root(tmp_path, language)
        for language in ("typescript", "go")
    }
    if mutation == "missing-binding":
        del roots["go"]
    elif mutation == "wrong-assets":
        path = roots["go"] / "conformance" / "specification.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["fixtures"] = []
        path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "schema-drift":
        path = roots["typescript"] / "conformance" / "specification.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema["title"] = "Drifted schema"
        path.write_text(json.dumps(schema), encoding="utf-8")
    else:
        path = roots["typescript"] / "conformance" / "fixtures" / verify_assets()[
            "fixtures"
        ][0]
        path.write_bytes(b"{}\n")

    with pytest.raises(ValueError):
        verify_release_bindings(roots)
