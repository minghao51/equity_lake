"""Exit-code and dry-run contract tests for devtools/seed_transcripts."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import polars as pl

from equity_lake.devtools.seed_transcripts import main, seed_transcripts_silver


def _hf_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["AAPL", "MSFT"],
            "quarter": [1, 2],
            "year": [2023, 2023],
            "date": ["2023-01-01 00:00:00", "2023-04-01 00:00:00"],
            "content": ["call a", "call b"],
            "company_name": ["Apple Inc.", "Microsoft Corp."],
        }
    )


class TestMainExitCodes:
    def _run(self, monkeypatch, bronze_ok: bool, silver_ok: bool, argv: list[str]) -> int:  # type: ignore[no-untyped-def]
        monkeypatch.setattr("sys.argv", ["seed_transcripts", *argv])
        return main()

    def test_main_returns_zero_when_all_steps_ok(self, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
        monkeypatch.setattr("equity_lake.devtools.seed_transcripts._ensure_cached", lambda force: tmp_path / "hf.parquet")
        monkeypatch.setattr("equity_lake.devtools.seed_transcripts._load_hf", lambda path: _hf_frame())
        with (
            patch("equity_lake.devtools.seed_transcripts.seed_transcripts_bronze", return_value={"rows": 2, "ok": True}),
            patch("equity_lake.devtools.seed_transcripts.seed_transcripts_silver", return_value={"rows": 2, "ok": True}),
        ):
            assert self._run(monkeypatch, True, True, []) == 0

    def test_main_returns_one_when_bronze_fails(self, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
        monkeypatch.setattr("equity_lake.devtools.seed_transcripts._ensure_cached", lambda force: tmp_path / "hf.parquet")
        monkeypatch.setattr("equity_lake.devtools.seed_transcripts._load_hf", lambda path: _hf_frame())
        with (
            patch("equity_lake.devtools.seed_transcripts.seed_transcripts_bronze", return_value={"rows": 0, "ok": False}),
            patch("equity_lake.devtools.seed_transcripts.seed_transcripts_silver", return_value={"rows": 2, "ok": True}),
        ):
            assert self._run(monkeypatch, False, True, []) == 1

    def test_main_returns_one_when_silver_fails(self, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
        monkeypatch.setattr("equity_lake.devtools.seed_transcripts._ensure_cached", lambda force: tmp_path / "hf.parquet")
        monkeypatch.setattr("equity_lake.devtools.seed_transcripts._load_hf", lambda path: _hf_frame())
        with (
            patch("equity_lake.devtools.seed_transcripts.seed_transcripts_bronze", return_value={"rows": 2, "ok": True}),
            patch("equity_lake.devtools.seed_transcripts.seed_transcripts_silver", return_value={"rows": 0, "ok": False}),
        ):
            assert self._run(monkeypatch, True, False, []) == 1

    def test_main_dry_run_plumbs_flag(self, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
        monkeypatch.setattr("equity_lake.devtools.seed_transcripts._ensure_cached", lambda force: tmp_path / "hf.parquet")
        monkeypatch.setattr("equity_lake.devtools.seed_transcripts._load_hf", lambda path: _hf_frame())
        bronze = MagicMock(return_value={"rows": 2, "ok": True, "dry_run": True})
        silver = MagicMock(return_value={"rows": 1, "ok": True, "dry_run": True})
        monkeypatch.setattr("equity_lake.devtools.seed_transcripts.seed_transcripts_bronze", bronze)
        monkeypatch.setattr("equity_lake.devtools.seed_transcripts.seed_transcripts_silver", silver)
        assert self._run(monkeypatch, True, True, ["--dry-run"]) == 0
        assert bronze.call_args.kwargs["dry_run"] is True
        assert silver.call_args.kwargs["dry_run"] is True


class TestSilverDryRun:
    def test_silver_dry_run_spends_no_llm_tokens(self):  # type: ignore[no-untyped-def]
        with patch("equity_lake.ingestion.llm_processor.run_llm_processing") as llm:
            summary = seed_transcripts_silver(_hf_frame(), ["AAPL"], datetime.now(UTC).replace(tzinfo=None), dry_run=True)
        llm.assert_not_called()
        assert summary["ok"] is True
        assert summary["dry_run"] is True
        assert summary["rows"] == 1

    def test_silver_dry_run_empty_scope_fails(self):  # type: ignore[no-untyped-def]
        summary = seed_transcripts_silver(_hf_frame(), ["NVDA"], datetime.now(UTC).replace(tzinfo=None), dry_run=True)
        assert summary["ok"] is False
        assert summary["rows"] == 0
