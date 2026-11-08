"""Source-pinned differential execution of a candidate primitive binding subset."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contexture.core.graph.canonical import canonical_json  # noqa: E402


def fingerprint(root: Path, directories: tuple[str, ...], files: tuple[str, ...]) -> dict[str, str]:
    paths = {root / name for name in files}
    for directory in directories:
        paths.update(path for path in (root / directory).rglob("*")
                     if path.is_file() and path.suffix in {".py", ".ts", ".go"})
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--typescript-root", type=Path, default=ROOT.parent / "contexture-mcp-typescript")
    parser.add_argument("--go-root", type=Path, default=ROOT.parent / "contexture-mcp-go")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    corpus = ROOT / "spec/managed/referencevectors/portable-binding-primitives.json"
    snapshot = "conformance/managed/portable-binding-primitives.json"
    roots = {"python": ROOT, "typescript": args.typescript_root.resolve(), "go": args.go_root.resolve()}

    def sources() -> dict[str, dict[str, str]]:
        return {
            "python": fingerprint(ROOT, ("contexture",), (
                "tests/test_managed_portable_binding_primitives.py",
                "scripts/verify_managed_binding_primitives.py", str(corpus.relative_to(ROOT)),
                "pyproject.toml", "uv.lock")),
            "typescript": fingerprint(roots["typescript"], ("src",), (
                "test/managed-portable-binding-primitives.test.ts", snapshot,
                "package.json", "package-lock.json", "tsconfig.json", ".prettierignore")),
            "go": fingerprint(roots["go"], ("core", "server"), (
                "go.mod", "go.sum", snapshot)),
        }

    before = sources()
    raw = corpus.read_bytes()
    for language in ("typescript", "go"):
        if (roots[language] / snapshot).read_bytes() != raw:
            raise ValueError(f"{language} candidate corpus snapshot differs from reference")
    data = json.loads(raw)
    expected = canonical_json([{"id": case["id"], "observation": case["expected"]}
                               for case in data["cases"]])
    commands = {
        "python": [sys.executable, "-m", "pytest", "-q", "-ra",
                   "tests/test_managed_portable_binding_primitives.py"],
        "typescript": ["node", "--import", "tsx", "--test",
                       "test/managed-portable-binding-primitives.test.ts"],
        "go": ["go", "test", "-race", "-count=1", "./core/graph",
               "-run", "^TestPortableBindingPrimitiveObservations$"],
    }
    receipt = {
        "schema": "contexture.portable-binding-differential/candidate-v1",
        "contract_id": data["contract_id"], "cases": len(data["cases"]),
        "corpus_sha256": hashlib.sha256(raw).hexdigest(), "source_sha256_before": before,
        "python": sys.version, "python_executable": sys.executable,
        "python_dependencies": {name: importlib.metadata.version(name)
                                for name in ("jsonschema", "rfc8785", "pytest")},
        "steps": [], "passed": False,
        "qualification": "Finite candidate data subset only; no accepted ABI/revision, permission, "
                         "production identity or installed-worker/adoption claim.",
    }
    for language, command in commands.items():
        output = args.output / f"{language}.observations.json"
        env = os.environ | {"CONTEXTURE_BINDING_OBSERVATIONS": str(output.resolve())}
        started = time.monotonic()
        result = subprocess.run(command, cwd=roots[language], env=env, capture_output=True,
                                timeout=180, check=False)
        (args.output / f"{language}.stdout").write_bytes(result.stdout)
        (args.output / f"{language}.stderr").write_bytes(result.stderr)
        observed = output.read_bytes() if output.is_file() else None
        step = {"language": language, "command": command, "exit": result.returncode,
                "seconds": time.monotonic() - started,
                "observations_equal": observed == expected,
                "observations_sha256": hashlib.sha256(observed).hexdigest() if observed else None,
                "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
                "passed": result.returncode == 0 and observed == expected}
        receipt["steps"].append(step)
        print(language, json.dumps(step), flush=True)
        (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    after = sources()
    receipt.update(source_sha256_after=after, sources_unchanged=before == after,
                   passed=before == after and all(step["passed"] for step in receipt["steps"]))
    (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    raise SystemExit(0 if receipt["passed"] else 1)


if __name__ == "__main__":
    main()
