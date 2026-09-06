"""Verify deterministic metadata used by Contexture language ports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "spec"
PORTING = SPEC / "porting"
RULES = tuple(range(1, 17))
STATUSES = {"scaffold", "partial", "conformant"}
RULE_STATUSES = {"not-started", "in-progress", "implemented"}


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def verify_assets() -> dict[str, Any]:
    inventory = _object(PORTING / "assets.json")
    actual_fixtures = sorted(path.name for path in (SPEC / "fixtures").glob("*.json"))
    actual_golden = sorted(path.name for path in (SPEC / "golden").iterdir() if path.is_file())
    if inventory.get("fixtures") != actual_fixtures:
        raise ValueError("fixture inventory does not match spec/fixtures")
    if inventory.get("golden") != actual_golden:
        raise ValueError("golden inventory does not match spec/golden")
    if inventory.get("specificationVersion") != "0.12":
        raise ValueError("porting inventory must target Specification 0.12")
    revision = inventory.get("revision")
    if not isinstance(revision, str) or len(revision) != 40 or any(
        character not in "0123456789abcdef" for character in revision
    ):
        raise ValueError("porting inventory revision must be a lowercase Git SHA")
    return inventory


def verify_schema() -> None:
    schema = _object(SPEC / "conformance-manifest.schema.json")
    required = set(schema.get("required", ()))
    expected = {
        "repository", "revision", "specificationVersion", "status",
        "implementedRules", "rules", "fixtures", "golden",
    }
    if required != expected:
        raise ValueError("manifest schema required fields changed unexpectedly")
    rule_schema = schema["properties"]["rules"]
    if set(rule_schema.get("required", ())) != {str(rule) for rule in RULES}:
        raise ValueError("manifest schema must require all 16 conformance rules")


def verify_manifest(path: Path, inventory: dict[str, Any]) -> None:
    manifest = _object(path)
    expected_keys = {
        "$schema", "repository", "revision", "specificationVersion", "status",
        "implementedRules", "rules", "fixtures", "golden", "notes",
    }
    unknown = set(manifest) - expected_keys
    if unknown:
        raise ValueError(f"{path} contains unknown fields: {sorted(unknown)}")
    required = expected_keys - {"$schema", "notes"}
    missing = required - set(manifest)
    if missing:
        raise ValueError(f"{path} is missing fields: {sorted(missing)}")
    if manifest["revision"] != inventory["revision"]:
        raise ValueError(f"{path} revision is not synchronized with the porting inventory")
    if manifest["specificationVersion"] != inventory["specificationVersion"]:
        raise ValueError(f"{path} specification version is not synchronized")
    if manifest["fixtures"] != inventory["fixtures"] or manifest["golden"] != inventory["golden"]:
        raise ValueError(f"{path} asset inventory is not synchronized")
    if manifest["status"] not in STATUSES:
        raise ValueError(f"{path} has an invalid implementation status")

    rules = manifest["rules"]
    if not isinstance(rules, dict) or set(rules) != {str(rule) for rule in RULES}:
        raise ValueError(f"{path} must contain exactly rules 1 through 16")
    derived: list[int] = []
    for number in RULES:
        entry = rules[str(number)]
        if not isinstance(entry, dict) or set(entry) != {"status", "evidence"}:
            raise ValueError(f"{path} rule {number} has an invalid shape")
        if entry["status"] not in RULE_STATUSES:
            raise ValueError(f"{path} rule {number} has an invalid status")
        evidence = entry["evidence"]
        if not isinstance(evidence, list) or any(not isinstance(item, str) or not item for item in evidence) or len(evidence) != len(set(evidence)):
            raise ValueError(f"{path} rule {number} has invalid evidence")
        if entry["status"] == "implemented":
            if not evidence:
                raise ValueError(f"{path} rule {number} needs implementation evidence")
            derived.append(number)

    if manifest["implementedRules"] != derived:
        raise ValueError(f"{path} implementedRules does not match rule statuses")
    expected_status = "conformant" if derived == list(RULES) else "scaffold" if derived == [1] else "partial"
    if manifest["status"] != expected_status:
        raise ValueError(f"{path} status must be {expected_status!r} for {derived}")


def main() -> int:
    try:
        inventory = verify_assets()
        verify_schema()
        for argument in sys.argv[1:]:
            verify_manifest(Path(argument), inventory)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as failure:
        print(f"porting contract invalid: {failure}", file=sys.stderr)
        return 1
    print("porting contract metadata is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
