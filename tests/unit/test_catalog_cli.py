"""Tests for the catalog-generate CLI command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from equity_lake.cli.__main__ import app

runner = CliRunner()


class TestCatalogGenerateHelp:
    def test_help_lists_command(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "catalog-generate" in result.stdout

    def test_command_help(self) -> None:
        result = runner.invoke(app, ["catalog-generate", "--help"])
        assert result.exit_code == 0
        assert "Hamilton DAG" in result.stdout


class TestCatalogGenerateOutput:
    def test_custom_output_writes_file(self, tmp_path: Path) -> None:
        out = tmp_path / "catalog.jsonl"
        result = runner.invoke(app, ["catalog-generate", "--output", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        assert out.stat().st_size > 0

    def test_custom_output_contains_datasets(self, tmp_path: Path) -> None:
        out = tmp_path / "catalog.jsonl"
        runner.invoke(app, ["catalog-generate", "--output", str(out)])
        content = out.read_text()
        assert '"type": "dataset"' in content
        assert '"type": "node"' in content
        assert '"type": "edge"' in content

    def test_reports_counts_in_stdout(self, tmp_path: Path) -> None:
        out = tmp_path / "catalog.jsonl"
        result = runner.invoke(app, ["catalog-generate", "--output", str(out)])
        assert result.exit_code == 0
        assert "datasets" in result.stdout
        assert "nodes" in result.stdout
        assert "edges" in result.stdout

    def test_verbose_flag_exits_zero(self, tmp_path: Path) -> None:
        out = tmp_path / "catalog.jsonl"
        result = runner.invoke(app, ["catalog-generate", "--output", str(out), "--verbose"])
        assert result.exit_code == 0
        assert out.exists()


class TestCatalogGenerateDefaultPath:
    def test_default_invokes_writer_with_none(self) -> None:
        """Without --output, write_catalog_jsonl is called with path=None (default location)."""
        with patch("equity_lake.catalog.write_catalog_jsonl", return_value=12345) as mock_write:
            result = runner.invoke(app, ["catalog-generate"])
        assert result.exit_code == 0
        mock_write.assert_called_once()
        assert mock_write.call_args.kwargs.get("path") is None
