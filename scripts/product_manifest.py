"""Generate and verify the Python 0.12 product-parity inventory.

The inventory is deliberately generated from a pinned Git revision rather than
the working tree. That makes every TypeScript and Go mapping auditable when the
Python implementation advances.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
PORTING = ROOT / "spec" / "porting"
MANIFEST_PATH = PORTING / "PYTHON_0_12_PRODUCT_MANIFEST.json"
MARKDOWN_PATH = PORTING / "PYTHON_0_12_PRODUCT_MANIFEST.md"
BASELINE = "3b274421360d5569a23922bfc72b71d5828cf995"
SCHEMA_VERSION = 1
LANGUAGES = ("typescript", "go")
BINDING_DIRECTORIES = {
    "typescript": "contexture-mcp-typescript",
    "go": "contexture-mcp-go",
}
ENTRY_STATUSES = {"missing", "designed", "implemented", "verified"}
TARGET_STATUSES = {"unmapped", "not-applicable", "designed", "implemented", "verified"}
PRODUCT_ASSET_PATHS = (
    "pyproject.toml",
    "README.md",
    "README.zh-CN.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "RELEASING.md",
    "SECURITY.md",
)


@dataclass(frozen=True)
class GitFile:
    """One tracked file read from the pinned Python baseline."""

    path: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def _git(*arguments: str, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=text,
    )
    return result.stdout


def _paths(revision: str, *prefixes: str) -> list[str]:
    output = _git("ls-tree", "-r", "--name-only", revision, "--", *prefixes)
    assert isinstance(output, str)
    return sorted(path for path in output.splitlines() if path)


def _file(revision: str, path: str) -> GitFile:
    content = _git("show", f"{revision}:{path}", text=False)
    assert isinstance(content, bytes)
    return GitFile(path=path, content=content)


def _package_import(path: str) -> str:
    parts = list(Path(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _literal_strings(node: ast.AST) -> list[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values: list[str] = []
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        values.append(element.value)
    return values


def _symbols(source: GitFile, *, is_test: bool) -> list[str]:
    try:
        module = ast.parse(source.content, filename=source.path)
    except SyntaxError as failure:
        raise ValueError(f"cannot parse {source.path}: {failure}") from failure

    explicit: list[str] | None = None
    candidates: list[str] = []
    for statement in module.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if is_test:
                if statement.name.startswith("test_") or statement.name.startswith("Test"):
                    candidates.append(statement.name)
            elif not statement.name.startswith("_"):
                candidates.append(statement.name)
        elif isinstance(statement, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "__all__" for target in statement.targets):
                explicit = _literal_strings(statement.value)
        elif isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name) and statement.target.id == "__all__":
                explicit = _literal_strings(statement.value) if statement.value is not None else None

    return sorted(explicit if explicit is not None else candidates)


def _responsibility(path: str, *, is_test: bool) -> str:
    if is_test:
        if path == "tests/typing/consumer.py":
            return "External installed-package type and runtime consumer contract."
        if path in {"tests/channels_fixture.py", "tests/golden.py", "tests/http_fixture.py", "tests/serving.py"}:
            return "Shared test fixture support."
        return "Focused behavioural or integration test for the Python reference product."

    prefix_map = (
        ("contexture/core/model/", "Contexture kernel object model and runtime behaviour."),
        ("contexture/core/mcp_interface/", "SDK-free MCP primitive declaration surface."),
        ("contexture/core/", "Shared Contexture core foundation."),
        ("contexture/server/surface/", "MCP server surface implementation."),
        ("contexture/server/", "MCP server compilation, identity, launch, and transport integration."),
        ("contexture/web/", "Explicit REST/ASGI adapter."),
        ("contexture/cli/templates/", "Generated application template asset."),
        ("contexture/cli/", "Developer CLI, project loading, and scaffolding."),
        ("contexture/demo/", "Maintained reference application."),
    )
    for prefix, value in prefix_map:
        if path.startswith(prefix):
            return value
    if path == "contexture/application.py":
        return "Lazy application declaration facade."
    if path == "contexture/inspection.py":
        return "Transport-free disclosure inspection API."
    if path in {"contexture/__init__.py", "contexture/__init__.pyi"}:
        return "Public declaration-facing package facade."
    return "Contexture product source asset."


def _target_paths(language: str, path: str, *, kind: str) -> list[str]:
    """Return the planned native location for one pinned Python inventory row.

    This is a design map, not implementation credit.  The map deliberately
    names missing files: a missing but named counterpart is reviewable work,
    while an ``unmapped`` row can silently hide a user workflow.
    """

    if kind == "asset":
        if path == "pyproject.toml":
            return ["package.json"] if language == "typescript" else ["go.mod"]
        if path.startswith("contexture/cli/templates/"):
            prefix = "src/cli/templates/" if language == "typescript" else "cmd/contexture/templates/"
            return [prefix + path.removeprefix("contexture/cli/templates/")]
        return [path]

    if kind == "test":
        if path == "tests/typing/consumer.py":
            return [
                "scripts/verify-package-consumer.mjs"
                if language == "typescript"
                else "internal/releasecheck/module_consumer_test.go"
            ]
        name = Path(path).stem.removeprefix("test_")
        if path in {"tests/__init__.py", "tests/channels_fixture.py", "tests/golden.py", "tests/http_fixture.py", "tests/serving.py"}:
            return [
                f"test/support/{Path(path).stem}.ts"
                if language == "typescript"
                else f"internal/testsupport/{Path(path).stem}.go"
            ]
        return [f"test/{name}.test.ts" if language == "typescript" else f"{name}_test.go"]

    exact = {
        "contexture/__init__.py": ("src/index.ts", "facade.go"),
        "contexture/__init__.pyi": ("src/index.ts", "facade.go"),
        "contexture/application.py": ("src/application.ts", "core/model/application.go"),
        "contexture/inspection.py": ("src/inspection.ts", "inspection/inspection.go"),
        "contexture/core/__init__.py": ("src/core/index.ts", "core/model/doc.go"),
        "contexture/core/constants.py": ("src/core/foundation/constants.ts", "core/foundation/constants.go"),
        "contexture/core/errors.py": ("src/core/foundation/errors.ts", "core/foundation/errors.go"),
        "contexture/core/principal.py": ("src/core/foundation/principal.ts", "core/foundation/principal.go"),
        "contexture/core/types.py": ("src/core/foundation/types.ts", "core/foundation/types.go"),
    }
    if path in exact:
        return [exact[path][0 if language == "typescript" else 1]]

    if path.startswith("contexture/core/mcp_interface/"):
        suffix = Path(path).stem
        if suffix == "__init__":
            suffix = "index"
        return [
            f"src/core/mcp-interface/{suffix}.ts"
            if language == "typescript"
            else f"core/mcpinterface/{suffix}.go"
        ]
    if path.startswith("contexture/core/model/"):
        suffix = Path(path).stem
        if suffix == "__init__":
            suffix = "doc"
        names = {
            "disclosure_api": "system-api",
            "execution_api": "system-api",
            "graph_context": "runtime",
            "root_selection": "root-selection",
        }
        suffix = names.get(suffix, suffix)
        return [
            f"src/core/model/{suffix}.ts"
            if language == "typescript"
            else f"core/model/{suffix.replace('-', '_')}.go"
        ]
    if path.startswith("contexture/server/surface/"):
        suffix = Path(path).stem
        if suffix == "__init__":
            suffix = "surface"
        return [f"src/server/surface/{suffix}.ts" if language == "typescript" else f"server/surface/{suffix}.go"]
    if path.startswith("contexture/server/"):
        suffix = Path(path).stem
        if suffix == "__init__":
            suffix = "index"
        return [f"src/server/{suffix}.ts" if language == "typescript" else f"server/{suffix}.go"]
    if path.startswith("contexture/web/"):
        suffix = Path(path).stem
        if suffix == "__init__":
            suffix = "index"
        return [f"src/web/{suffix}.ts" if language == "typescript" else f"web/{suffix}.go"]
    if path.startswith("contexture/cli/"):
        suffix = Path(path).stem
        if suffix == "__init__":
            suffix = "index"
        elif suffix == "__main__":
            suffix = "main"
        return [
            f"src/cli/{suffix}.ts"
            if language == "typescript"
            else f"cmd/contexture/{suffix}.go"
        ]
    if path.startswith("contexture/demo/"):
        suffix = Path(path).stem
        if suffix == "__init__":
            suffix = "index"
        return [f"src/demo/{suffix}.ts" if language == "typescript" else f"demo/{suffix}.go"]
    raise ValueError(f"no target-path design for {path}")


def _target_placeholder(language: str, path: str, *, kind: str) -> dict[str, Any]:
    return {
        "status": "designed",
        "paths": _target_paths(language, path, kind=kind),
        "tests": _target_paths(language, path, kind="test") if kind == "test" else [],
        "documentation": [],
        "reason": "Phase-0 design mapping only; no product-parity implementation credit is claimed until focused native evidence is recorded.",
    }


def _module_entry(source: GitFile, *, kind: str) -> dict[str, Any]:
    is_test = kind == "test"
    return {
        "path": source.path,
        "kind": kind,
        "sha256": source.sha256,
        "pythonImport": _package_import(source.path) if source.path.endswith(".py") and not is_test else None,
        "publicSymbols": _symbols(source, is_test=is_test) if source.path.endswith(".py") else [],
        "responsibility": _responsibility(source.path, is_test=is_test),
        "status": "missing",
        "targets": {language: _target_placeholder(language, source.path, kind=kind) for language in LANGUAGES},
    }


def _asset_entry(source: GitFile) -> dict[str, Any]:
    return {
        "path": source.path,
        "sha256": source.sha256,
        "status": "missing",
        "targets": {language: _target_placeholder(language, source.path, kind="asset") for language in LANGUAGES},
    }


def source_paths(revision: str = BASELINE) -> list[str]:
    return [
        path
        for path in _paths(revision, "contexture")
        if path.endswith(".py") and "/__pycache__/" not in path
    ]


def test_paths(revision: str = BASELINE) -> list[str]:
    return [path for path in _paths(revision, "tests") if path.endswith(".py")]


def product_asset_paths(revision: str = BASELINE) -> list[str]:
    paths: set[str] = set(PRODUCT_ASSET_PATHS)
    paths.update(_paths(revision, "contexture/cli/templates", "docs"))
    return sorted(paths)


def build_manifest(revision: str = BASELINE) -> dict[str, Any]:
    sources = [_module_entry(_file(revision, path), kind="source") for path in source_paths(revision)]
    tests = [_module_entry(_file(revision, path), kind="test") for path in test_paths(revision)]
    assets = [_asset_entry(_file(revision, path)) for path in product_asset_paths(revision)]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "baseline": {
            "repository": "CarterShi01/contexture-mcp",
            "revision": revision,
            "package": "contexture-mcp",
            "version": "0.12.0rc1",
        },
        "languages": list(LANGUAGES),
        "sourceModules": sources,
        "testModules": tests,
        "productAssets": assets,
    }


def _markdown_rows(entries: Iterable[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for entry in entries:
        targets = entry["targets"]
        rows.append(
            "| {path} | {kind} | {symbols} | {ts} | {go} | {status} |".format(
                path=entry["path"],
                kind=entry.get("kind", "asset"),
                symbols=", ".join(entry.get("publicSymbols", ())) or "—",
                ts=_target_cell(targets["typescript"]),
                go=_target_cell(targets["go"]),
                status=entry["status"],
            )
        )
    return rows


def _target_cell(target: dict[str, Any]) -> str:
    paths = ", ".join(f"`{path}`" for path in target["paths"])
    return f"{target['status']}: {paths}" if paths else target["status"]


def render_markdown(manifest: dict[str, Any]) -> str:
    source = manifest["sourceModules"]
    tests = manifest["testModules"]
    assets = manifest["productAssets"]
    baseline = manifest["baseline"]
    lines = [
        "# Python 0.12 product parity manifest",
        "",
        "This file is generated by scripts/product_manifest.py. Do not edit it by hand.",
        "",
        f"Baseline: {baseline['repository']} at {baseline['revision']}",
        f"Package: {baseline['package']} {baseline['version']}",
        "",
        "## Inventory",
        "",
        f"- Source modules: {len(source)}",
        f"- Test modules: {len(tests)}",
        f"- Product assets: {len(assets)}",
        "",
        "Every row has a planned native target. A target status of designed means",
        "no implementation or parity credit has yet been claimed; it is not an exemption.",
        "",
        "## Source modules",
        "",
        "| Python path | Kind | Public symbols | TypeScript | Go | Status |",
        "| --- | --- | --- | --- | --- | --- |",
        *_markdown_rows(source),
        "",
        "## Test modules",
        "",
        "| Python path | Kind | Public symbols | TypeScript | Go | Status |",
        "| --- | --- | --- | --- | --- | --- |",
        *_markdown_rows(tests),
        "",
        "## Product assets",
        "",
        "| Python path | TypeScript | Go | Status |",
        "| --- | --- | --- | --- |",
    ]
    for asset in assets:
        lines.append(
            f"| {asset['path']} | {_target_cell(asset['targets']['typescript'])} | "
            f"{_target_cell(asset['targets']['go'])} | {asset['status']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _require_keys(value: dict[str, Any], keys: set[str], location: str) -> None:
    missing = keys - set(value)
    extra = set(value) - keys
    if missing or extra:
        raise ValueError(f"{location} has invalid keys; missing={sorted(missing)}, extra={sorted(extra)}")


def _verify_target(target: Any, location: str) -> None:
    if not isinstance(target, dict):
        raise ValueError(f"{location} must be an object")
    _require_keys(target, {"status", "paths", "tests", "documentation", "reason"}, location)
    if target["status"] not in TARGET_STATUSES:
        raise ValueError(f"{location} has invalid target status")
    for key in ("paths", "tests", "documentation"):
        if not isinstance(target[key], list) or any(not isinstance(item, str) or not item for item in target[key]):
            raise ValueError(f"{location}.{key} must be a list of non-empty strings")
    if not isinstance(target["reason"], str):
        raise ValueError(f"{location}.reason must be a string")
    if target["status"] == "unmapped":
        raise ValueError(f"{location} is unmapped; every Python product row needs a native target")
    if target["status"] in {"implemented", "verified"} and (not target["paths"] or not target["tests"]):
        raise ValueError(f"{location} needs implementation and test paths")


def _verify_target_evidence(
    target: dict[str, Any],
    location: str,
    binding_root: Path,
) -> None:
    if target["status"] != "verified":
        return
    root = binding_root.resolve()
    for key in ("paths", "tests", "documentation"):
        for relative in target[key]:
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ValueError(
                    f"{location}.{key} names evidence outside its binding repository: {relative!r}"
                )
            candidate = (root / relative_path).resolve()
            if not candidate.is_relative_to(root):
                raise ValueError(
                    f"{location}.{key} names evidence outside its binding repository: {relative!r}"
                )
            if not candidate.is_file():
                raise ValueError(
                    f"{location}.{key} names missing evidence {relative!r} under {root}"
                )


def _verify_entry(entry: Any, expected: GitFile, kind: str) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"{expected.path} entry must be an object")
    required = {"path", "sha256", "status", "targets"}
    if kind != "asset":
        required |= {"kind", "pythonImport", "publicSymbols", "responsibility"}
    _require_keys(entry, required, expected.path)
    if entry["path"] != expected.path:
        raise ValueError(f"manifest path ordering drifted at {expected.path}")
    if entry["sha256"] != expected.sha256:
        raise ValueError(f"baseline content hash drifted for {expected.path}")
    if entry["status"] not in ENTRY_STATUSES:
        raise ValueError(f"{expected.path} has invalid status")
    if kind != "asset" and entry["kind"] != kind:
        raise ValueError(f"{expected.path} has wrong kind")
    if kind != "asset":
        expected_import = _package_import(expected.path) if kind == "source" else None
        if entry["pythonImport"] != expected_import:
            raise ValueError(f"{expected.path} has wrong Python import path")
        if entry["publicSymbols"] != _symbols(expected, is_test=kind == "test"):
            raise ValueError(f"{expected.path} public symbol inventory drifted")
        if not isinstance(entry["responsibility"], str) or not entry["responsibility"]:
            raise ValueError(f"{expected.path} needs a responsibility")
    if not isinstance(entry["targets"], dict) or set(entry["targets"]) != set(LANGUAGES):
        raise ValueError(f"{expected.path} must name both language targets")
    for language in LANGUAGES:
        _verify_target(entry["targets"][language], f"{expected.path}.{language}")
    if entry["status"] == "verified":
        for language in LANGUAGES:
            target = entry["targets"][language]
            if target["status"] != "verified":
                raise ValueError(
                    f"{expected.path} cannot be verified until {language} is verified"
                )
            documentation = target["documentation"]
            if not any(path.endswith(".md") and "zh-CN" not in path for path in documentation):
                raise ValueError(f"{expected.path}.{language} needs English documentation")
            if not any("zh-CN" in path for path in documentation):
                raise ValueError(
                    f"{expected.path}.{language} needs Simplified Chinese documentation"
                )


def _verify_entries(entries: Any, expected_paths: list[str], revision: str, kind: str) -> None:
    if not isinstance(entries, list):
        raise ValueError(f"{kind} inventory must be a list")
    paths = [entry.get("path") if isinstance(entry, dict) else None for entry in entries]
    if paths != expected_paths:
        raise ValueError(f"{kind} inventory does not match pinned baseline paths")
    for entry, path in zip(entries, expected_paths, strict=True):
        _verify_entry(entry, _file(revision, path), kind)


def verify_manifest(
    manifest: dict[str, Any],
    revision: str = BASELINE,
    *,
    binding_roots: dict[str, Path] | None = None,
) -> None:
    _require_keys(
        manifest,
        {"schemaVersion", "baseline", "languages", "sourceModules", "testModules", "productAssets"},
        "manifest",
    )
    if manifest["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError("unsupported product manifest schema version")
    baseline = manifest["baseline"]
    if not isinstance(baseline, dict):
        raise ValueError("baseline must be an object")
    _require_keys(baseline, {"repository", "revision", "package", "version"}, "baseline")
    if baseline["revision"] != revision:
        raise ValueError("product manifest baseline revision is not pinned correctly")
    if baseline["repository"] != "CarterShi01/contexture-mcp":
        raise ValueError("unexpected product manifest repository")
    if baseline["package"] != "contexture-mcp" or baseline["version"] != "0.12.0rc1":
        raise ValueError("unexpected product manifest package baseline")
    if manifest["languages"] != list(LANGUAGES):
        raise ValueError("product manifest language order changed")

    _verify_entries(manifest["sourceModules"], source_paths(revision), revision, "source")
    _verify_entries(manifest["testModules"], test_paths(revision), revision, "test")
    _verify_entries(manifest["productAssets"], product_asset_paths(revision), revision, "asset")
    if binding_roots is not None:
        if set(binding_roots) != set(LANGUAGES):
            raise ValueError("evidence verification needs both binding roots")
        for collection in ("sourceModules", "testModules", "productAssets"):
            for entry in manifest[collection]:
                for language in LANGUAGES:
                    _verify_target_evidence(
                        entry["targets"][language],
                        f"{entry['path']}.{language}",
                        binding_roots[language],
                    )


def _load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("product manifest must be a JSON object")
    return value


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=BASELINE, help="pinned Python revision to inventory")
    parser.add_argument("--write", action="store_true", help="write JSON and Markdown inventories")
    parser.add_argument("--check", action="store_true", help="verify checked-in inventory")
    parser.add_argument(
        "--check-evidence",
        action="store_true",
        help="also require every verified TS and Go evidence path in sibling repositories",
    )
    options = parser.parse_args(arguments)

    if int(options.write) + int(options.check) + int(options.check_evidence) != 1:
        parser.error("pass exactly one of --write, --check, or --check-evidence")

    try:
        if options.write:
            manifest = build_manifest(options.revision)
            MANIFEST_PATH.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            MARKDOWN_PATH.write_text(render_markdown(manifest), encoding="utf-8")
            print(f"wrote {MANIFEST_PATH.relative_to(ROOT)} and {MARKDOWN_PATH.relative_to(ROOT)}")
        else:
            binding_roots = None
            if options.check_evidence:
                binding_roots = {
                    language: ROOT.parent / directory
                    for language, directory in BINDING_DIRECTORIES.items()
                }
            verify_manifest(
                _load_manifest(MANIFEST_PATH),
                options.revision,
                binding_roots=binding_roots,
            )
            expected = render_markdown(_load_manifest(MANIFEST_PATH))
            actual = MARKDOWN_PATH.read_text(encoding="utf-8")
            if actual != expected:
                raise ValueError("Markdown view is not generated from the JSON manifest")
            print("product parity manifest inventory is valid")
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as failure:
        print(f"product manifest invalid: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
