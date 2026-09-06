"""Tests for the pinned Python product-parity inventory."""

from __future__ import annotations

import copy

import pytest

from scripts import product_manifest


def test_build_manifest_is_self_consistent() -> None:
    manifest = product_manifest.build_manifest()

    product_manifest.verify_manifest(manifest)

    assert manifest["baseline"]["revision"] == product_manifest.BASELINE
    assert manifest["sourceModules"]
    assert manifest["testModules"]
    assert manifest["productAssets"]
    assert all(
        target["status"] == "designed" and target["paths"]
        for collection in ("sourceModules", "testModules", "productAssets")
        for entry in manifest[collection]
        for target in entry["targets"].values()
    )


def test_verifier_rejects_pinned_source_hash_drift() -> None:
    manifest = product_manifest.build_manifest()
    changed = copy.deepcopy(manifest)
    changed["sourceModules"][0]["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="content hash drifted"):
        product_manifest.verify_manifest(changed)


def test_verifier_rejects_an_unmapped_product_counterpart() -> None:
    manifest = product_manifest.build_manifest()
    changed = copy.deepcopy(manifest)
    target = changed["sourceModules"][0]["targets"]["typescript"]
    target["status"] = "unmapped"
    target["paths"] = []

    with pytest.raises(ValueError, match="is unmapped"):
        product_manifest.verify_manifest(changed)


def test_markdown_view_is_deterministic() -> None:
    first = product_manifest.build_manifest()
    second = product_manifest.build_manifest()

    assert product_manifest.render_markdown(first) == product_manifest.render_markdown(second)
