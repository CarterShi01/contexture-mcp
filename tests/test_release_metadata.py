"""Release metadata that must stay aligned for the stable 1.x package."""

from __future__ import annotations

import tomllib
from pathlib import Path

import contexture

from contexture.core.constants import PACKAGE_VERSION


ROOT = Path(__file__).resolve().parents[1]


def test_stable_release_version_and_classifier_are_aligned() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    assert project["version"] == PACKAGE_VERSION == contexture.__version__ == "1.0.0"
    assert "Development Status :: 5 - Production/Stable" in project["classifiers"]
