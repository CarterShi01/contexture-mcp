"""Validate the immutable Python 0.13-0.16 incremental parity ledger."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "spec/porting/PYTHON_0_13_TO_0_16_INCREMENTAL_MANIFEST.json"
VALID_STATUS = {"pending", "implemented", "verified", "not-applicable"}


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def load() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def validate() -> None:
    manifest = load()
    releases = manifest["releases"]
    assert [release["version"] for release in releases] == [
        "0.13.0",
        "0.14.0",
        "0.15.0",
        "0.16.0",
    ]

    previous = manifest["base"]["commit"]
    for release in releases:
        version = release["version"]
        commit = release["commit"]
        assert release["previousCommit"] == previous
        assert git("merge-base", "--is-ancestor", previous, commit) == ""
        assert git("rev-parse", f"{commit}^{{tree}}") == release["tree"]

        metadata = git("show", f"{commit}:pyproject.toml")
        assert f'version = "{version}"' in metadata
        changelog = git("show", f"{commit}:CHANGELOG.md")
        assert f"## [{version}]" in changelog

        actual_paths = git(
            "diff", "--name-status", "--find-renames", previous, commit
        ).splitlines()
        assert actual_paths == release["deltaPaths"], (
            f"{version} changed-path inventory differs from its pinned Git objects"
        )
        assert release["status"] in VALID_STATUS
        assert release["capabilities"]
        for capability in release["capabilities"]:
            assert capability["status"] in VALID_STATUS
            assert capability["pythonSources"]
            assert capability["pythonTests"]
            assert capability["productAssets"]
            assert capability["typescriptTargets"]
            assert capability["goTargets"]
            if capability["status"] == "verified":
                assert capability["typescriptEvidence"]
                assert capability["goEvidence"]
        if release["status"] == "verified":
            assert all(
                capability["status"] in {"verified", "not-applicable"}
                for capability in release["capabilities"]
            )
        previous = commit

    print("incremental parity manifest is valid")


if __name__ == "__main__":
    validate()
