from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "incremental_manifest.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("incremental_manifest", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_incremental_manifest_matches_pinned_python_git_objects() -> None:
    _load_validator().validate()
