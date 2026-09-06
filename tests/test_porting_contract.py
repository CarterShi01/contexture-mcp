"""Deterministic checks for language-binding conformance metadata."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.verify_porting_contract import verify_assets, verify_manifest, verify_schema


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
            for number in range(1, 17)
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
    verify_assets()
    verify_schema()


def test_manifest_accepts_the_truthful_scaffold_state(tmp_path: Path) -> None:
    verify_manifest(_write(tmp_path, _manifest()), verify_assets())


@pytest.mark.parametrize("mutation", ["missing-rule", "false-credit", "wrong-revision", "false-status"])
def test_manifest_rejects_inconsistent_state(tmp_path: Path, mutation: str) -> None:
    manifest = deepcopy(_manifest())
    rules = manifest["rules"]
    assert isinstance(rules, dict)
    if mutation == "missing-rule":
        del rules["16"]
    elif mutation == "false-credit":
        manifest["implementedRules"] = [1, 2]
    elif mutation == "wrong-revision":
        manifest["revision"] = "0" * 40
    else:
        manifest["status"] = "conformant"

    with pytest.raises(ValueError):
        verify_manifest(_write(tmp_path, manifest), verify_assets())
