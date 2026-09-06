"""Language-neutral fixture files must remain valid, reviewable JSON."""

from __future__ import annotations

import json
from pathlib import Path


FIXTURES = Path(__file__).parent.parent / "spec" / "fixtures"


def test_every_language_neutral_fixture_is_valid_json() -> None:
    files = sorted(FIXTURES.glob("*.json"))
    assert files
    for path in files:
        value = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(value, dict), path


def test_latest_surface_boundaries_have_fixtures() -> None:
    assert {path.name for path in FIXTURES.glob("*.json")} >= {
        "reference-application.json",
        "prompt-roots-application.json",
        "root-selections.json",
        "disclosure-only-application.json",
        "rest-routes.json",
    }
