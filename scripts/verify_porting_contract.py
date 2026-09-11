"""Verify deterministic metadata used by Contexture language ports."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "spec"
PORTING = SPEC / "porting"
SPECIFICATION_VERSION = "0.16"
SPECIFICATION_REVISION = "cda2721c7c40128cd0b7eef990e5909edabd3b17"
RULES = tuple(range(1, 18))
STATUSES = {"scaffold", "partial", "conformant"}
RULE_STATUSES = {"not-started", "in-progress", "implemented"}
BINDING_DIRECTORIES = {
    "typescript": "contexture-mcp-typescript",
    "go": "contexture-mcp-go",
}


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _immutable_file(revision: str, path: Path) -> bytes:
    result = subprocess.run(
        ("git", "show", f"{revision}:{path.as_posix()}"),
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise ValueError(
            f"cannot read {path.as_posix()} from immutable specification revision "
            f"{revision}"
        )
    return result.stdout


def verify_assets() -> dict[str, Any]:
    inventory = _object(PORTING / "assets.json")
    actual_fixtures = sorted(path.name for path in (SPEC / "fixtures").glob("*.json"))
    actual_golden = sorted(path.name for path in (SPEC / "golden").iterdir() if path.is_file())
    if inventory.get("fixtures") != actual_fixtures:
        raise ValueError("fixture inventory does not match spec/fixtures")
    if inventory.get("golden") != actual_golden:
        raise ValueError("golden inventory does not match spec/golden")
    if inventory.get("specificationVersion") != SPECIFICATION_VERSION:
        raise ValueError(
            f"porting inventory must target Specification {SPECIFICATION_VERSION}"
        )
    if inventory.get("revision") != SPECIFICATION_REVISION:
        raise ValueError(
            "porting inventory must target the reviewed immutable specification revision"
        )
    for directory, names in (
        ("fixtures", actual_fixtures),
        ("golden", actual_golden),
    ):
        for name in names:
            relative = Path("spec") / directory / name
            if (ROOT / relative).read_bytes() != _immutable_file(
                SPECIFICATION_REVISION, relative
            ):
                raise ValueError(
                    f"{relative.as_posix()} differs from immutable specification revision"
                )
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
        raise ValueError(
            f"manifest schema must require all {len(RULES)} conformance rules"
        )
    if rule_schema.get("additionalProperties") is not False or set(
        rule_schema.get("patternProperties", {})
    ) != {"^(?:[1-9]|1[0-7])$"}:
        raise ValueError("manifest schema must allow exactly rules 1 through 17")
    implemented_schema = schema["properties"]["implementedRules"]
    if implemented_schema.get("items") != {
        "type": "integer",
        "minimum": 1,
        "maximum": RULES[-1],
    } or implemented_schema.get("uniqueItems") is not True:
        raise ValueError("implementedRules schema must allow unique rules 1 through 17")
    version_schema = schema["properties"]["specificationVersion"]
    if version_schema.get("const") != SPECIFICATION_VERSION:
        raise ValueError("manifest schema specification version is not synchronized")


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
        raise ValueError(f"{path} must contain exactly rules 1 through {RULES[-1]}")
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


def verify_release_bindings(binding_roots: dict[str, Path] | None = None) -> None:
    """Verify both binding release manifests against the immutable root contract."""

    roots = binding_roots or {
        language: ROOT.parent / directory
        for language, directory in BINDING_DIRECTORIES.items()
    }
    if set(roots) != set(BINDING_DIRECTORIES):
        raise ValueError("release verification requires TypeScript and Go binding roots")

    inventory = verify_assets()
    verify_schema()
    root_schema = _object(SPEC / "conformance-manifest.schema.json")
    for language, root in roots.items():
        manifest_path = root / "conformance" / "specification.json"
        verify_manifest(manifest_path, inventory)
        manifest = _object(manifest_path)
        if manifest["status"] != "conformant" or manifest["implementedRules"] != list(
            RULES
        ):
            raise ValueError(f"{language} binding is not fully conformant")
        binding_schema = _object(root / "conformance" / "specification.schema.json")
        if binding_schema != root_schema:
            raise ValueError(
                f"{language} conformance schema is not synchronized with the root schema"
            )
        for directory in ("fixtures", "golden"):
            for name in inventory[directory]:
                root_asset = SPEC / directory / name
                binding_asset = root / "conformance" / directory / name
                if binding_asset.read_bytes() != root_asset.read_bytes():
                    raise ValueError(
                        f"{language} {directory}/{name} is not byte-identical to "
                        "the root specification asset"
                    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify deterministic Contexture porting and release metadata."
    )
    parser.add_argument("manifests", nargs="*", type=Path)
    parser.add_argument(
        "--release-bindings",
        action="store_true",
        help="also verify the TypeScript and Go release manifests",
    )
    parser.add_argument(
        "--typescript-root",
        type=Path,
        default=ROOT.parent / BINDING_DIRECTORIES["typescript"],
    )
    parser.add_argument(
        "--go-root",
        type=Path,
        default=ROOT.parent / BINDING_DIRECTORIES["go"],
    )
    arguments = parser.parse_args(argv)
    try:
        inventory = verify_assets()
        verify_schema()
        for manifest in arguments.manifests:
            verify_manifest(manifest, inventory)
        if arguments.release_bindings:
            verify_release_bindings(
                {
                    "typescript": arguments.typescript_root,
                    "go": arguments.go_root,
                }
            )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as failure:
        print(f"porting contract invalid: {failure}", file=sys.stderr)
        return 1
    print("porting contract metadata is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
