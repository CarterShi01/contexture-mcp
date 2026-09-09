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
    applicable = [
        entry
        for collection in ("sourceModules", "testModules", "productAssets")
        for entry in manifest[collection]
        if entry["path"] not in product_manifest.NOT_APPLICABLE_PATHS
    ]
    assert all(
        target["status"] == "designed" and target["paths"]
        for entry in applicable
        for target in entry["targets"].values()
    )


def test_oc_goal_exclusion_is_exact_and_generated() -> None:
    manifest = product_manifest.build_manifest()
    excluded = {
        entry["path"]
        for collection in ("sourceModules", "testModules", "productAssets")
        for entry in manifest[collection]
        if entry["status"] == "not-applicable"
    }

    assert excluded == product_manifest.NOT_APPLICABLE_PATHS
    for collection in ("sourceModules", "testModules", "productAssets"):
        for entry in manifest[collection]:
            if entry["path"] not in excluded:
                continue
            assert all(
                target == product_manifest._not_applicable_target()
                for target in entry["targets"].values()
            )


def test_verifier_rejects_exclusions_outside_exact_oc_goal_scope() -> None:
    manifest = product_manifest.build_manifest()
    entry = manifest["sourceModules"][0]
    entry["status"] = "not-applicable"
    entry["targets"] = {
        language: product_manifest._not_applicable_target()
        for language in product_manifest.LANGUAGES
    }

    with pytest.raises(ValueError, match="must have an applicable status"):
        product_manifest.verify_manifest(manifest)


def test_verifier_requires_oc_goal_exclusions() -> None:
    manifest = product_manifest.build_manifest()
    entry = next(
        entry
        for entry in manifest["testModules"]
        if entry["path"] == "tests/test_oc_goal_case_study.py"
    )
    entry["status"] = "missing"

    with pytest.raises(ValueError, match="must have not-applicable"):
        product_manifest.verify_manifest(manifest)


def test_verifier_rejects_pinned_source_hash_drift() -> None:
    manifest = product_manifest.build_manifest()
    changed = copy.deepcopy(manifest)
    changed["sourceModules"][0]["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="content hash drifted"):
        product_manifest.verify_manifest(changed)


def test_verifier_rejects_package_baseline_drift() -> None:
    manifest = product_manifest.build_manifest()
    manifest["baseline"]["version"] = "0.14.0"

    with pytest.raises(ValueError, match="package baseline"):
        product_manifest.verify_manifest(manifest)


def test_verifier_rejects_an_unmapped_product_counterpart() -> None:
    manifest = product_manifest.build_manifest()
    changed = copy.deepcopy(manifest)
    target = changed["sourceModules"][0]["targets"]["typescript"]
    target["status"] = "unmapped"
    target["paths"] = []

    with pytest.raises(ValueError, match="is unmapped"):
        product_manifest.verify_manifest(changed)


def test_verifier_requires_both_languages_and_bilingual_evidence_for_verified_rows() -> None:
    manifest = product_manifest.build_manifest()
    entry = manifest["sourceModules"][0]
    entry["status"] = "verified"
    for target in entry["targets"].values():
        target["status"] = "verified"
        target["tests"] = ["focused.test"]
        target["documentation"] = ["docs/handbook.md", "docs/handbook.zh-CN.md"]
    entry["targets"]["go"]["status"] = "implemented"

    with pytest.raises(ValueError, match="go is verified"):
        product_manifest.verify_manifest(manifest)

    entry["targets"]["go"]["status"] = "verified"
    entry["targets"]["typescript"]["documentation"] = ["docs/handbook.md"]
    with pytest.raises(ValueError, match="Simplified Chinese"):
        product_manifest.verify_manifest(manifest)


def test_verifier_checks_verified_binding_evidence_paths(tmp_path) -> None:
    manifest = product_manifest.build_manifest()
    entry = manifest["sourceModules"][0]
    entry["status"] = "verified"
    roots = {language: tmp_path / language for language in product_manifest.LANGUAGES}
    for language, target in entry["targets"].items():
        target["status"] = "verified"
        target["paths"] = ["src/facade.txt"]
        target["tests"] = ["test/facade.txt"]
        target["documentation"] = ["docs/handbook.md", "docs/handbook.zh-CN.md"]
        for relative in (*target["paths"], *target["tests"], *target["documentation"]):
            candidate = roots[language] / relative
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text("evidence", encoding="utf-8")

    product_manifest.verify_manifest(manifest, binding_roots=roots)
    (roots["typescript"] / "test/facade.txt").unlink()
    with pytest.raises(ValueError, match="missing evidence"):
        product_manifest.verify_manifest(manifest, binding_roots=roots)


@pytest.mark.parametrize("outside", ["../outside.txt", "/outside.txt"])
def test_verifier_rejects_evidence_paths_outside_binding_repository(tmp_path, outside) -> None:
    manifest = product_manifest.build_manifest()
    entry = manifest["sourceModules"][0]
    entry["status"] = "verified"
    roots = {language: tmp_path / language for language in product_manifest.LANGUAGES}
    for language, target in entry["targets"].items():
        target["status"] = "verified"
        target["paths"] = ["src/facade.txt"]
        target["tests"] = ["test/facade.txt"]
        target["documentation"] = ["docs/handbook.md", "docs/handbook.zh-CN.md"]
        for relative in (*target["paths"], *target["tests"], *target["documentation"]):
            candidate = roots[language] / relative
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text("evidence", encoding="utf-8")
    entry["targets"]["typescript"]["paths"] = [outside]

    with pytest.raises(ValueError, match="outside its binding repository"):
        product_manifest.verify_manifest(manifest, binding_roots=roots)


def test_verifier_rejects_evidence_symlinks_outside_binding_repository(tmp_path) -> None:
    manifest = product_manifest.build_manifest()
    entry = manifest["sourceModules"][0]
    entry["status"] = "verified"
    roots = {language: tmp_path / language for language in product_manifest.LANGUAGES}
    for language, target in entry["targets"].items():
        target["status"] = "verified"
        target["paths"] = ["src/facade.txt"]
        target["tests"] = ["test/facade.txt"]
        target["documentation"] = ["docs/handbook.md", "docs/handbook.zh-CN.md"]
        for relative in (*target["paths"], *target["tests"], *target["documentation"]):
            candidate = roots[language] / relative
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text("evidence", encoding="utf-8")
    external = tmp_path / "external.txt"
    external.write_text("outside", encoding="utf-8")
    link = roots["typescript"] / "src/link.txt"
    link.symlink_to(external)
    entry["targets"]["typescript"]["paths"] = ["src/link.txt"]

    with pytest.raises(ValueError, match="outside its binding repository"):
        product_manifest.verify_manifest(manifest, binding_roots=roots)


def test_markdown_view_is_deterministic() -> None:
    first = product_manifest.build_manifest()
    second = product_manifest.build_manifest()

    assert product_manifest.render_markdown(first) == product_manifest.render_markdown(second)
