"""Unit tests for the unified CLI (__main__.py)."""

from unittest.mock import patch

from typer.testing import CliRunner

from equity_lake.cli.__main__ import app

runner = CliRunner()


class TestUnifiedCLI:
    """Test suite for the unified equity CLI."""

    def test_cli_help(self):
        """Test that CLI --help works."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Equity Lake" in result.stdout

    def test_ingest_command_exists(self):
        """Test that ingest command is registered."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "ingest" in result.stdout

    def test_pipeline_command_exists(self):
        """Test that pipeline command is registered."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "pipeline" in result.stdout

    def test_backtest_command_exists(self):
        """Test that backtest command is registered."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "backtest" in result.stdout

    def test_signal_command_exists(self):
        """Test that signal command group is registered."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "signal" in result.stdout

    def test_validate_command_exists(self):
        """Test that validate command group is registered."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "validate" in result.stdout


class TestSignalSubcommands:
    """Test signal subcommand group."""

    def test_signal_scan_help(self):
        """Test signal scan --help works."""
        result = runner.invoke(app, ["signal", "scan", "--help"])
        assert result.exit_code == 0


class TestValidateSubcommands:
    """Test validate subcommand group."""

    def test_validate_check_help(self):
        """Test validate check --help works."""
        result = runner.invoke(app, ["validate", "check", "--help"])
        assert result.exit_code == 0

    def test_validate_profile_help(self):
        """Test validate profile --help works."""
        result = runner.invoke(app, ["validate", "profile", "--help"])
        assert result.exit_code == 0

    def test_validate_drift_help(self):
        """Test validate drift --help works."""
        result = runner.invoke(app, ["validate", "drift", "--help"])
        assert result.exit_code == 0


class TestDashboardSubcommands:
    """Test dashboard subcommand group."""

    def test_dashboard_build_help(self):
        """Test dashboard build --help works."""
        result = runner.invoke(app, ["dashboard", "build", "--help"])
        assert result.exit_code == 0

    def test_dashboard_serve_help(self):
        """Test dashboard serve --help works."""
        result = runner.invoke(app, ["dashboard", "serve", "--help"])
        assert result.exit_code == 0


class TestBootstrapSubcommands:
    """Test bootstrap subcommand group."""

    def test_bootstrap_sample_help(self):
        """Test bootstrap sample --help works."""
        result = runner.invoke(app, ["bootstrap", "sample", "--help"])
        assert result.exit_code == 0


class TestLoaderSubcommands:
    """Test loader subcommand group."""

    def test_loader_list_help(self):
        """Test loader list --help works."""
        result = runner.invoke(app, ["loader", "list", "--help"])
        assert result.exit_code == 0


class TestConfigSubcommands:
    """Test config subcommand group."""

    def test_config_show_help(self):
        """Test config show --help works."""
        result = runner.invoke(app, ["config", "show", "--help"])
        assert result.exit_code == 0

    def test_config_validate_help(self):
        """Test config validate --help works."""
        result = runner.invoke(app, ["config", "validate", "--help"])
        assert result.exit_code == 0


class TestPassthroughFunction:
    """Test _passthrough function behavior."""

    def test_ingest_command_runs(self):
        """Test that ingest command can be invoked."""
        result = runner.invoke(app, ["ingest"])
        assert result.exit_code is not None

    def test_ingest_invokes_legacy_command(self):
        """Test that ingest command invokes legacy CLI."""
        with patch("equity_lake.cli.__main__._run_legacy") as mock_run:
            mock_run.return_value = None
            runner.invoke(app, ["ingest"])
            assert mock_run.called

    def test_ingest_passes_unknown_arguments_through(self):
        """Legacy flags should reach the wrapped argparse command."""
        with patch("equity_lake.cli.__main__._run_legacy") as mock_run:
            mock_run.return_value = None
            runner.invoke(app, ["ingest", "--markets", "us", "--dry-run"])
            mock_run.assert_called_once_with("equity_lake.ingestion.orchestrator:main", ["--markets", "us", "--dry-run"])

    def test_config_show_prefixes_legacy_subcommand(self):
        """Config wrappers should include their argparse subcommand token."""
        with patch("equity_lake.cli.__main__._run_legacy") as mock_run:
            mock_run.return_value = None
            runner.invoke(app, ["config", "show"])
            mock_run.assert_called_once_with("equity_lake.cli.config:main", ["show"])

    def test_signal_scan_prefixes_legacy_subcommand(self):
        """Signal wrappers should include their argparse subcommand token."""
        with patch("equity_lake.cli.__main__._run_legacy") as mock_run:
            mock_run.return_value = None
            runner.invoke(app, ["signal", "scan", "--watchlist", "config/watchlist.yaml"])
            mock_run.assert_called_once_with("equity_lake.cli.signal:main", ["scan", "--watchlist", "config/watchlist.yaml"])
