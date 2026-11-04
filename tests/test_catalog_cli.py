"""The structural catalog command is distinct from disclosure inspection."""

from __future__ import annotations

import json

from contexture.cli.main import main


TARGET = "tests.test_introspection:Root"


def test_catalog_json_is_one_clean_standard_snapshot(capsys) -> None:  # type: ignore[no-untyped-def]
    status = main(
        [
            "catalog",
            "--target",
            TARGET,
            "--view",
            "model",
            "--detail",
            "definition",
            "--json",
        ]
    )
    captured = capsys.readouterr()

    assert status == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["schema"] == "contexture.node-catalog/v1"
    assert payload["application"]["detail"] == "definition"
    assert payload["counts"] == {"role": 2, "skill": 1, "tool": 1}


def test_catalog_can_query_one_ref_without_replaying_disclosure(capsys) -> None:  # type: ignore[no-untyped-def]
    status = main(
        [
            "catalog",
            "root/review",
            "--target",
            TARGET,
            "--detail",
            "definition",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert status == 0
    assert len(payload) == 1
    assert payload[0]["ref"] == "root/review"
    assert payload[0]["skill"]["authored_instructions"]


def test_surface_catalog_requires_explicit_selection(capsys) -> None:  # type: ignore[no-untyped-def]
    status = main(
        ["catalog", "--target", TARGET, "--view", "surface", "--json"]
    )
    captured = capsys.readouterr()

    assert status == 1
    assert captured.out == ""
    assert "requires an explicit SurfaceSelection" in captured.err


def test_catalog_unknown_ref_is_a_nonzero_diagnostic(capsys) -> None:  # type: ignore[no-untyped-def]
    status = main(["catalog", "root/absent", "--target", TARGET, "--json"])
    captured = capsys.readouterr()

    assert status == 1
    assert captured.out == ""
    assert "root/absent" in captured.err


def test_surface_catalog_cli_promotes_each_explicit_selection(capsys) -> None:  # type: ignore[no-untyped-def]
    status = main(
        [
            "catalog",
            "--target",
            TARGET,
            "--view",
            "surface",
            "--select",
            "root/review",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert status == 0
    assert payload["roots"] == ["root/review"]
    assert [node["ref"] for node in payload["nodes"]] == ["root/review"]

