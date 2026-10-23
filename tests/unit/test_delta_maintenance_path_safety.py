"""Temporary-filesystem contracts for Delta-maintenance CLI path containment."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import equity_lake.cli.commands.data as data_commands  # type: ignore[import-untyped]
from equity_lake.cli.__main__ import app  # type: ignore[import-untyped]

runner = CliRunner()


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("us", "01_bronze/market_data/us_equity"),
        ("us_equity", "01_bronze/market_data/us_equity"),
        ("cn", "01_bronze/market_data/cn_ashare"),
        ("hk_sg", "01_bronze/market_data/hk_sg_equity"),
        ("hk_sg_equity", "01_bronze/market_data/hk_sg_equity"),
        ("jpx", "01_bronze/market_data/jpx_equity"),
        ("krx", "01_bronze/market_data/krx_equity"),
        ("krx_equity", "01_bronze/market_data/krx_equity"),
        ("features", "03_gold/features"),
        ("predictions", "04_platinum/predictions"),
        ("us_news", "02_silver/news_sentiment"),
        ("01_bronze/market_data/us_equity", "01_bronze/market_data/us_equity"),
        ("operator/archive/table", "operator/archive/table"),
    ],
)
def test_resolves_named_routes_and_safe_lake_relative_paths(identifier: str, expected: str, tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    lake_root.mkdir()

    assert data_commands._resolve_dataset_paths([f" {identifier} "], lake_root) == [expected]


def test_preserves_every_mapped_dataset_identifier(tmp_path: Path) -> None:
    from equity_lake.ingestion.types import MARKET_DIR_MAP  # type: ignore[import-untyped]

    lake_root = tmp_path / "lake"
    lake_root.mkdir()
    for identifier, route in MARKET_DIR_MAP.items():
        assert data_commands._resolve_dataset_paths([identifier], lake_root) == [route]


def test_rejects_empty_absolute_and_traversal_identifiers(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    lake_root.mkdir()

    for identifier in ("", "  ", "/tmp/outside", "../outside", "a/../b"):
        with pytest.raises(ValueError, match="target|identifier"):
            data_commands._resolve_dataset_paths([identifier], lake_root)

    with pytest.raises(ValueError, match="At least one"):
        data_commands._resolve_dataset_paths([], lake_root)


def test_rejects_symlink_resolving_outside_lake(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    lake_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (lake_root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside the configured lake root"):
        data_commands._resolve_dataset_paths(["escape"], lake_root)


def test_allows_in_lake_symlink_and_returns_effective_route(tmp_path: Path) -> None:
    lake_root = tmp_path / "lake"
    effective_target = lake_root / "03_gold" / "features_archive"
    effective_target.mkdir(parents=True)
    (lake_root / "03_gold" / "features").symlink_to(effective_target, target_is_directory=True)

    assert data_commands._resolve_dataset_paths(["features"], lake_root) == ["03_gold/features_archive"]


def test_resolves_configured_symlink_root_and_nonexistent_targets(tmp_path: Path) -> None:
    actual_lake = tmp_path / "actual-lake"
    actual_lake.mkdir()
    configured_lake = tmp_path / "configured-lake"
    configured_lake.symlink_to(actual_lake, target_is_directory=True)

    assert data_commands._resolve_dataset_paths(["new/noncatalog/table"], configured_lake) == ["new/noncatalog/table"]


@pytest.mark.parametrize("command", ["delta-vacuum", "delta-compact", "delta-migrate"])
def test_invalid_later_target_prevents_every_maintenance_call(command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake_root = tmp_path / "lake"
    lake_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (lake_root / "escape").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(data_commands, "LAKE_DIR", lake_root)

    with (
        patch("equity_lake.storage.delta.vacuum_delta") as vacuum,
        patch("equity_lake.storage.delta.compact_delta") as compact,
        patch("equity_lake.storage.delta.migrate_parquet_to_delta") as migrate,
    ):
        args = [command, "--markets", "us, predictions, escape"]
        if command == "delta-vacuum":
            args.append("--no-dry-run")
        result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "resolves outside the configured lake root" in result.stdout
    vacuum.assert_not_called()
    compact.assert_not_called()
    migrate.assert_not_called()


def test_cli_preserves_comma_whitespace_routes_and_safe_unknown_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake_root = tmp_path / "lake"
    lake_root.mkdir()
    monkeypatch.setattr(data_commands, "LAKE_DIR", lake_root)

    with patch("equity_lake.storage.delta.compact_delta", return_value={}) as compact:
        result = runner.invoke(
            app,
            [
                "delta-compact",
                "--markets",
                " us , features , predictions , 01_bronze/market_data/jpx_equity , operator/archive/table ",
            ],
        )

    assert result.exit_code == 0, result.output
    assert [call.args[0] for call in compact.call_args_list] == [
        "01_bronze/market_data/us_equity",
        "03_gold/features",
        "04_platinum/predictions",
        "01_bronze/market_data/jpx_equity",
        "operator/archive/table",
    ]


@pytest.mark.parametrize(
    ("command", "operation", "return_value", "expected_dry_run"),
    [
        ("delta-vacuum", "vacuum_delta", [], True),
        ("delta-migrate", "migrate_parquet_to_delta", True, False),
    ],
)
def test_maintenance_dry_run_defaults_and_help_are_preserved(
    command: str, operation: str, return_value: object, expected_dry_run: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lake_root = tmp_path / "lake"
    lake_root.mkdir()
    monkeypatch.setattr(data_commands, "LAKE_DIR", lake_root)

    with patch(f"equity_lake.storage.delta.{operation}", return_value=return_value) as maintenance:
        result = runner.invoke(app, [command, "--markets", "us"])

    assert result.exit_code == 0, result.output
    assert maintenance.call_args.args[0] == "01_bronze/market_data/us_equity"
    assert maintenance.call_args.kwargs["dry_run"] is expected_dry_run

    help_result = runner.invoke(app, [command, "--help"])
    assert help_result.exit_code == 0
    assert "--dry-run" in help_result.stdout
    if command == "delta-vacuum":
        assert "--no-dry-run" in help_result.stdout


def test_cli_rejects_empty_late_target_before_vacuum_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake_root = tmp_path / "lake"
    lake_root.mkdir()
    monkeypatch.setattr(data_commands, "LAKE_DIR", lake_root)

    with patch("equity_lake.storage.delta.vacuum_delta") as vacuum:
        result = runner.invoke(app, ["delta-vacuum", "--markets", "us, ", "--no-dry-run"])

    assert result.exit_code == 1
    assert "must not be empty" in result.stdout
    vacuum.assert_not_called()
