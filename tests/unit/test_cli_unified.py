"""Unit tests for the unified CLI (__main__.py)."""

from datetime import date
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from equity_lake.cli.__main__ import app

runner = CliRunner()


class TestUnifiedCLI:
    def test_cli_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Equity Lake" in result.stdout

    def test_ingest_command_exists(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "ingest" in result.stdout

    def test_pipeline_command_exists(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "pipeline" in result.stdout

    def test_backtest_command_exists(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "backtest" in result.stdout

    def test_signal_command_exists(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "signal" in result.stdout

    def test_validate_command_exists(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "validate" in result.stdout


class TestSignalSubcommands:
    def test_signal_scan_help(self):
        result = runner.invoke(app, ["signal", "scan", "--help"])
        assert result.exit_code == 0


class TestValidateSubcommands:
    def test_validate_check_help(self):
        result = runner.invoke(app, ["validate", "check", "--help"])
        assert result.exit_code == 0

    def test_validate_profile_help(self):
        result = runner.invoke(app, ["validate", "profile", "--help"])
        assert result.exit_code == 0

    def test_validate_drift_help(self):
        result = runner.invoke(app, ["validate", "drift", "--help"])
        assert result.exit_code == 0


class TestDashboardSubcommands:
    def test_dashboard_build_help(self):
        result = runner.invoke(app, ["dashboard", "build", "--help"])
        assert result.exit_code == 0

    def test_dashboard_serve_help(self):
        result = runner.invoke(app, ["dashboard", "serve", "--help"])
        assert result.exit_code == 0


class TestBootstrapSubcommands:
    def test_bootstrap_sample_help(self):
        result = runner.invoke(app, ["bootstrap", "sample", "--help"])
        assert result.exit_code == 0


class TestLoaderSubcommands:
    def test_loader_list_help(self):
        result = runner.invoke(app, ["loader", "list", "--help"])
        assert result.exit_code == 0


class TestConfigSubcommands:
    def test_config_show_help(self):
        result = runner.invoke(app, ["config", "show", "--help"])
        assert result.exit_code == 0

    def test_config_validate_help(self):
        result = runner.invoke(app, ["config", "validate", "--help"])
        assert result.exit_code == 0


class TestNativeCommands:
    def test_ingest_command_invokes_business_logic(self):
        with patch("equity_lake.ingestion.orchestrator.run_daily_ingestion", return_value={}) as mock_ingest:
            result = runner.invoke(app, ["ingest", "--date", "2024-01-01", "--dry-run"])
            assert result.exit_code == 0
            mock_ingest.assert_called_once()

    def test_config_show_outputs_settings(self):
        with patch("equity_lake.core.config.load_settings") as mock_load:
            mock_settings = MagicMock()
            mock_settings.model_dump.return_value = {"project": {"name": "test"}}
            mock_load.return_value = mock_settings
            result = runner.invoke(app, ["config", "show"])
            assert result.exit_code == 0

    def test_config_validate_outputs_valid(self):
        with patch("equity_lake.config.validators.validate_tickers", return_value=[]):
            result = runner.invoke(app, ["config", "validate"])
        assert result.exit_code == 0
        assert "passed" in result.stdout.lower()

    def test_config_validate_all_validates_signals(self):
        with (
            patch("equity_lake.config.validators.validate_tickers", return_value=[]),
            patch("equity_lake.config.validators.validate_watchlist", return_value=[]),
            patch("equity_lake.config.validators.validate_signals", return_value=["boom"]),
        ):
            result = runner.invoke(app, ["config", "validate", "--all"])
        assert result.exit_code == 1
        assert "boom" in result.stdout

    def test_pipeline_command_exits_nonzero_on_stage_failure(self):
        with patch("equity_lake.pipeline.execute_eod_pipeline", return_value={"features": {"success": False, "error": "boom"}}):
            result = runner.invoke(app, ["pipeline", "--date", "2024-01-01"])
            assert result.exit_code == 1

    def test_pipeline_command_exits_zero_on_stage_success(self):
        with patch(
            "equity_lake.pipeline.execute_eod_pipeline",
            return_value={"ingestion": {"us": True}, "features": {"success": True, "rows": 1}, "ml": {"success": True, "results": {}}},
        ):
            result = runner.invoke(app, ["pipeline", "--date", "2024-01-01"])
            assert result.exit_code == 0

    def test_news_command_requires_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = runner.invoke(app, ["news"])
            assert result.exit_code == 1

    def test_sentiment_command_requires_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = runner.invoke(app, ["sentiment"])
            assert result.exit_code == 1

    def test_sync_iterates_all_equity_markets(self):
        """Regression test: sync must not hardcode us_equity only (P0).

        Verifies S3Syncer is instantiated once per equity market (us, cn,
        hk_sg, jpx, krx) with the correct medallion target_dir.
        """
        instances = []

        class FakeSyncer:
            def __init__(self, bucket, target_dir, workers=16, dry_run=False, tool="auto"):
                self.bucket = bucket
                self.target_dir = target_dir
                instances.append(self)

            def sync(self):
                return None

        with (
            patch("equity_lake.storage.s3_sync.S3Syncer", FakeSyncer),
            patch.dict("os.environ", {"S3_BUCKET": "s3://test-bucket"}),
        ):
            result = runner.invoke(app, ["sync", "--dry-run"])

        assert result.exit_code == 0
        assert len(instances) == 5
        synced_dirs = {str(i.target_dir) for i in instances}
        expected = {
            "data/lake/01_bronze/market_data/us_equity",
            "data/lake/01_bronze/market_data/cn_ashare",
            "data/lake/01_bronze/market_data/hk_sg_equity",
            "data/lake/01_bronze/market_data/jpx_equity",
            "data/lake/01_bronze/market_data/krx_equity",
        }
        assert synced_dirs == expected

    def test_forecast_train_prints_training_summary(self):
        class FakeForecaster:
            def __init__(self, model_dir=None, model_mode="v1_direction"):
                self.model_dir = model_dir
                self.model_mode = model_mode

            def train_model(self, ticker, start_date, end_date, tune_hyperparams=False, validate=True):
                assert ticker == "AAPL"
                assert start_date == date(2024, 1, 1)
                assert end_date == date(2024, 2, 1)
                assert validate is True

            def last_training_summary(self):
                return {
                    "ticker": "AAPL",
                    "trained_on": "2024-02-01",
                    "model_mode": "v2_meta_label",
                    "status": "trained",
                    "train_rows": 50,
                    "validation_rows": 10,
                    "validation_fold_count": 2,
                    "mean_accuracy": 0.61,
                    "mean_precision": 0.58,
                    "mean_recall": 0.63,
                    "barrier_settings": {
                        "vertical_barrier_days": 5,
                        "pt_mult": 1.5,
                        "sl_mult": 1.0,
                        "meta_label_threshold": 0.55,
                    },
                }

            def close(self):
                return None

        with patch("equity_lake.ml.forecasting.PriceForecaster", FakeForecaster):
            result = runner.invoke(
                app,
                [
                    "forecast",
                    "--mode",
                    "train",
                    "--ticker",
                    "AAPL",
                    "--start",
                    "2024-01-01",
                    "--end",
                    "2024-02-01",
                    "--model-mode",
                    "v2_meta_label",
                ],
            )

        assert result.exit_code == 0
        assert "Forecast training complete for AAPL" in result.stdout
        assert "Mean accuracy: 0.610" in result.stdout
        assert "Meta-label threshold: 0.55" in result.stdout
