"""Fail-closed Bronze/Silver state handling using synthetic local tables only."""

import errno
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from equity_lake.core.config_models import MarketConfig, TickerMetadata  # type: ignore[import-untyped]
from equity_lake.ingestion import bronze_silver, replay  # type: ignore[import-untyped]
from equity_lake.ingestion.replay import ReplayBatchResult, ReplayScope  # type: ignore[import-untyped]

_TRADING_DATE = date(2026, 6, 14)


@pytest.fixture(autouse=True)
def _isolate_replay_auxiliary_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    auxiliary_data = tmp_path / "data"
    monkeypatch.setattr(replay, "DATA_DIR", auxiliary_data)
    monkeypatch.setattr(bronze_silver, "DATA_DIR", auxiliary_data)


def _bronze_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "article_id": ["article-1"],
            "source_type": ["rss"],
            "source_name": ["synthetic"],
            "source_url": ["https://example.invalid/article-1"],
            "title": ["Synthetic article"],
            "body": ["No provider content"],
            "date": [_TRADING_DATE],
        }
    )


def _silver_frame() -> pl.DataFrame:
    return pl.DataFrame({"article_id": ["article-1"], "ticker": ["AAPL"], "date": [_TRADING_DATE]})


def _write_bronze(path: Path) -> None:
    path.mkdir(parents=True)
    _bronze_frame().write_parquet(path / "articles.parquet")


def _write_unrecognized_state(path: Path, layout: str) -> None:
    path.mkdir(parents=True)
    if layout == "unknown_file":
        (path / "unexpected.txt").write_text("synthetic unrecognized state")
    else:
        (path / "unexpected").mkdir()


def _write_delta_log_state(path: Path, state: str) -> None:
    delta_log = path / "_delta_log"
    delta_log.mkdir(parents=True)
    if state == "malformed":
        (delta_log / "00000000000000000000.json").write_text("not valid Delta log JSON")


def test_bronze_read_binds_path_and_date_parameters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "bronze ' quoted"
    _write_bronze(bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)

    result = bronze_silver.read_bronze(_TRADING_DATE, bronze_path)

    assert result["article_id"].to_list() == ["article-1"]


def _run(
    silver_path: Path,
    processor: MagicMock,
    diagnostics: dict[str, object],
) -> bool:
    scope = ReplayScope(
        source="rss",
        trading_date=_TRADING_DATE,
        market="us_equity",
        instruments=("AAPL", bronze_silver.MARKET_WIDE_INSTRUMENT),
    )

    def process_batch(batch: Any, budget: Any) -> ReplayBatchResult:
        result = processor(batch, budget)
        if isinstance(result, pl.DataFrame):
            return ReplayBatchResult(rows=result.to_dicts(), actual_cost=Decimal("0.01"))
        return cast(ReplayBatchResult, result)

    return cast(
        bool,
        bronze_silver.process_unstructured_to_silver(
            _TRADING_DATE,
            scope=scope,
            source_type_filter="rss",
            exclude_source_types=None,
            process_batch=process_batch,
            estimate_batch_cost=lambda batch: Decimal("0.10") * len(batch),
            paid_processing_authorized=True,
            approved_estimate=Decimal("1.00"),
            actual_spend_cap=Decimal("2.00"),
            checkpoint_path=silver_path.parent / f"{silver_path.name}.checkpoint.json",
            silver_path=silver_path,
            silver_table_name="02_silver/processed_articles",
            silver_key_columns=["article_id", "ticker"],
            diagnostics=diagnostics,
        ),
    )


def test_absent_bronze_is_no_work_and_never_calls_processor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "absent-bronze"
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(tmp_path / "silver", process_fn, diagnostics) is False

    assert diagnostics["mode"] == "no_bronze_data"
    process_fn.assert_not_called()


def test_empty_bronze_directory_is_no_work_and_never_calls_processor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "empty-bronze"
    bronze_path.mkdir()
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(tmp_path / "silver", process_fn, diagnostics) is False

    assert diagnostics["mode"] == "no_bronze_data"
    process_fn.assert_not_called()


def test_dangling_bronze_symlink_is_a_read_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "dangling-bronze"
    bronze_path.symlink_to(tmp_path / "missing-bronze-target", target_is_directory=True)
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(tmp_path / "silver", process_fn, diagnostics) is False

    assert diagnostics["mode"] == "bronze_read_failed"
    assert diagnostics["error"]
    process_fn.assert_not_called()


def test_missing_table_descendant_behind_dangling_symlink_is_unreadable(tmp_path: Path) -> None:
    dangling_parent = tmp_path / "dangling-parent"
    dangling_parent.symlink_to(tmp_path / "missing-parent-target", target_is_directory=True)
    table_path = dangling_parent / "nested" / "silver"

    with pytest.raises(RuntimeError, match="Dangling symlink"):
        bronze_silver._table_has_data(table_path)


def test_valid_bronze_table_symlink_remains_supported(tmp_path: Path) -> None:
    bronze_target = tmp_path / "bronze-target"
    _write_bronze(bronze_target)
    bronze_link = tmp_path / "bronze-link"
    bronze_link.symlink_to(bronze_target, target_is_directory=True)

    assert bronze_silver._table_has_data(bronze_link) is True


def test_regular_file_ancestor_of_bronze_path_fails_before_processor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    blocked_ancestor = tmp_path / "bronze-file-ancestor"
    blocked_ancestor.write_text("not a directory")
    bronze_path = blocked_ancestor / "bronze"
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(tmp_path / "silver", process_fn, diagnostics) is False

    assert diagnostics["mode"] == "bronze_read_failed"
    assert diagnostics["error"]
    process_fn.assert_not_called()


@pytest.mark.parametrize("layout", ["unknown_file", "unknown_nested_directory"])
def test_unrecognized_bronze_state_fails_closed_before_processor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, layout: str) -> None:
    bronze_path = tmp_path / "unrecognized-bronze"
    _write_unrecognized_state(bronze_path, layout)
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(tmp_path / "silver", process_fn, diagnostics) is False

    assert diagnostics["mode"] == "bronze_read_failed"
    assert diagnostics["error"]
    process_fn.assert_not_called()


@pytest.mark.parametrize("state", ["empty", "malformed"])
def test_corrupt_bronze_delta_log_fails_closed_before_processor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str) -> None:
    bronze_path = tmp_path / "corrupt-bronze-delta"
    bronze_path.mkdir()
    _write_delta_log_state(bronze_path, state)
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(tmp_path / "silver", process_fn, diagnostics) is False

    assert diagnostics["mode"] == "bronze_read_failed"
    assert diagnostics["error"]
    process_fn.assert_not_called()


def test_corrupt_bronze_fails_closed_before_processor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "corrupt-bronze"
    bronze_path.mkdir()
    (bronze_path / "part.parquet").write_bytes(b"not a parquet file")
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(tmp_path / "silver", process_fn, diagnostics) is False

    assert diagnostics["mode"] == "bronze_read_failed"
    assert diagnostics["error"]
    process_fn.assert_not_called()


def test_absent_silver_is_empty_history_and_allows_mocked_processing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "bronze"
    _write_bronze(bronze_path)
    silver_path = tmp_path / "absent-silver"
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}
    durable_ids: set[str] = set()

    def write_silver(frame: pl.DataFrame, _table: str, _keys: list[str]) -> bool:
        durable_ids.update(frame["article_id"].to_list())
        return True

    monkeypatch.setattr(bronze_silver, "_get_processed_ids", lambda *_: set(durable_ids))
    monkeypatch.setattr(bronze_silver, "_write_silver_generic", write_silver)
    result = _run(silver_path, process_fn, diagnostics)

    assert result is True
    assert diagnostics["mode"] == "processed"
    assert diagnostics["rows"] == 1
    assert durable_ids == {"article-1"}
    process_fn.assert_called_once()


def test_empty_silver_directory_is_empty_history_and_allows_mocked_processing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "bronze"
    _write_bronze(bronze_path)
    silver_path = tmp_path / "empty-silver"
    silver_path.mkdir()
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}
    durable_ids: set[str] = set()

    def write_silver(frame: pl.DataFrame, _table: str, _keys: list[str]) -> bool:
        durable_ids.update(frame["article_id"].to_list())
        return True

    monkeypatch.setattr(bronze_silver, "_get_processed_ids", lambda *_: set(durable_ids))
    monkeypatch.setattr(bronze_silver, "_write_silver_generic", write_silver)
    result = _run(silver_path, process_fn, diagnostics)

    assert result is True
    assert diagnostics["mode"] == "processed"
    assert diagnostics["rows"] == 1
    assert durable_ids == {"article-1"}
    process_fn.assert_called_once()


def test_dangling_silver_symlink_fails_closed_before_processor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "bronze"
    _write_bronze(bronze_path)
    silver_path = tmp_path / "dangling-silver"
    silver_path.symlink_to(tmp_path / "missing-silver-target", target_is_directory=True)
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(silver_path, process_fn, diagnostics) is False

    assert diagnostics["mode"] == "silver_read_failed"
    assert diagnostics["error"]
    process_fn.assert_not_called()


def test_regular_file_ancestor_of_silver_path_fails_before_processor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "bronze"
    _write_bronze(bronze_path)
    blocked_ancestor = tmp_path / "silver-file-ancestor"
    blocked_ancestor.write_text("not a directory")
    silver_path = blocked_ancestor / "silver"
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(silver_path, process_fn, diagnostics) is False

    assert diagnostics["mode"] == "silver_read_failed"
    assert diagnostics["error"]
    process_fn.assert_not_called()


def test_silver_stat_permission_error_fails_before_processor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "bronze"
    _write_bronze(bronze_path)
    silver_path = tmp_path / "silver"
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    original_stat = Path.stat

    def deny_silver_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == silver_path:
            raise PermissionError(errno.EACCES, "synthetic permission denied", str(path))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", deny_silver_stat)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(silver_path, process_fn, diagnostics) is False

    assert diagnostics["mode"] == "silver_read_failed"
    assert "synthetic permission denied" in str(diagnostics["error"])
    process_fn.assert_not_called()


@pytest.mark.parametrize("layout", ["unknown_file", "unknown_nested_directory"])
def test_unrecognized_silver_state_fails_closed_before_processor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, layout: str) -> None:
    bronze_path = tmp_path / "bronze"
    _write_bronze(bronze_path)
    silver_path = tmp_path / "unrecognized-silver"
    _write_unrecognized_state(silver_path, layout)
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(silver_path, process_fn, diagnostics) is False

    assert diagnostics["mode"] == "silver_read_failed"
    assert diagnostics["error"]
    process_fn.assert_not_called()


@pytest.mark.parametrize("state", ["empty", "malformed"])
def test_corrupt_silver_delta_log_fails_closed_before_processor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str) -> None:
    bronze_path = tmp_path / "bronze"
    _write_bronze(bronze_path)
    silver_path = tmp_path / "corrupt-silver-delta"
    silver_path.mkdir()
    _write_delta_log_state(silver_path, state)
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(silver_path, process_fn, diagnostics) is False

    assert diagnostics["mode"] == "silver_read_failed"
    assert diagnostics["error"]
    process_fn.assert_not_called()


def test_initialized_delta_table_is_recognized(tmp_path: Path) -> None:
    import pyarrow as pa
    from deltalake import write_deltalake

    silver_path = tmp_path / "initialized-delta"
    write_deltalake(silver_path, pa.table({"article_id": ["synthetic"]}))

    assert bronze_silver._table_has_data(silver_path) is True


def test_corrupt_silver_fails_closed_before_processor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "bronze"
    _write_bronze(bronze_path)
    silver_path = tmp_path / "corrupt-silver"
    silver_path.mkdir()
    (silver_path / "part.parquet").write_bytes(b"not a parquet file")
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    assert _run(silver_path, process_fn, diagnostics) is False

    assert diagnostics["mode"] == "silver_read_failed"
    assert diagnostics["error"]
    process_fn.assert_not_called()


def test_processing_error_stays_distinct_from_read_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "bronze"
    _write_bronze(bronze_path)
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    process_fn = MagicMock(side_effect=RuntimeError("synthetic processor failure"))
    diagnostics: dict[str, object] = {}

    assert _run(tmp_path / "absent-silver", process_fn, diagnostics) is False

    assert diagnostics["mode"] == "processing_failed"
    assert "synthetic processor failure" in str(diagnostics["error"])
    process_fn.assert_called_once()


def test_write_error_stays_distinct_from_read_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bronze_path = tmp_path / "bronze"
    _write_bronze(bronze_path)
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    process_fn = MagicMock(return_value=_silver_frame())
    diagnostics: dict[str, object] = {}

    with patch.object(bronze_silver, "_write_silver_generic", return_value=False):
        assert _run(tmp_path / "absent-silver", process_fn, diagnostics) is False

    assert diagnostics["mode"] == "write_failed"
    process_fn.assert_called_once()


def test_real_article_processor_requires_paid_authorization_before_provider_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from equity_lake.ingestion.bronze_silver import process_bronze_to_silver  # type: ignore[import-untyped]

    bronze_path = tmp_path / "bronze"
    _write_bronze(bronze_path)
    silver_path = tmp_path / "silver"
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "SILVER_PROCESSED_ARTICLES_DIR", silver_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    scope = ReplayScope(
        source="rss",
        trading_date=_TRADING_DATE,
        market="us_equity",
        instruments=(bronze_silver.MARKET_WIDE_INSTRUMENT, "AAPL"),
    )
    diagnostics: dict[str, object] = {}

    with patch("equity_lake.ingestion.llm_processor.run_llm_processing", side_effect=AssertionError("provider call forbidden")) as provider:
        assert (
            process_bronze_to_silver(
                _TRADING_DATE,
                scope=scope,
                checkpoint_path=tmp_path / "article-checkpoint.json",
                diagnostics=diagnostics,
            )
            is False
        )

    assert diagnostics["mode"] == "paid_processing_disabled"
    provider.assert_not_called()
    assert not silver_path.exists()


def test_sec_processor_uses_shared_zero_paid_replay_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import json

    import equity_lake.core.paths as paths  # type: ignore[import-untyped]
    from equity_lake.ingestion.sec_processor import process_sec_bronze_to_silver  # type: ignore[import-untyped]

    bronze_path = tmp_path / "sec-bronze"
    bronze_path.mkdir()
    frame = _bronze_frame().with_columns(pl.lit("sec_filing").alias("source_type"))
    frame = frame.with_columns(pl.lit(json.dumps({"ticker": "AAPL"})).alias("source_metadata"))
    frame.write_parquet(bronze_path / "articles.parquet")
    silver_path = tmp_path / "sec-silver"
    monkeypatch.setattr(bronze_silver, "BRONZE_RAW_ARTICLES_DIR", bronze_path)
    monkeypatch.setattr(bronze_silver, "ensure_delta_extension", lambda _: None)
    monkeypatch.setattr(paths, "SEC_EXTRACTIONS_DIR", silver_path)
    scope = ReplayScope(
        source="sec_filing",
        trading_date=_TRADING_DATE,
        market="us_equity",
        instruments=("AAPL",),
    )
    diagnostics: dict[str, object] = {}

    with patch("equity_lake.ingestion.sec_processor.SECSectionProcessor") as provider:
        assert (
            process_sec_bronze_to_silver(
                _TRADING_DATE,
                scope=scope,
                checkpoint_path=tmp_path / "sec-checkpoint.json",
                diagnostics=diagnostics,
            )
            is False
        )

    assert diagnostics["mode"] == "paid_processing_disabled"
    provider.assert_not_called()
    assert not silver_path.exists()


def test_pipeline_dry_run_does_not_enter_ingestion_or_paid_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    from equity_lake import pipeline

    us_market = MarketConfig(
        currency="USD",
        tickers=[TickerMetadata(symbol="AAPL", name="Apple", exchange="NASDAQ", sector="Technology", active=True)],
    )

    class SyntheticTickerConfig:
        def get_market_info(self, market: str) -> MarketConfig | None:
            assert market == "us"
            return us_market

        def get_tickers_for_market(self, market: str, active_only: bool = False) -> list[str]:
            assert market == "us"
            return [ticker.symbol for ticker in us_market.tickers if ticker.active or not active_only]

        def get_ticker_metadata(self, symbol: str, market: str | None = None) -> TickerMetadata | None:
            assert market == "us"
            return next((ticker for ticker in us_market.tickers if ticker.symbol == symbol), None)

    monkeypatch.setattr(pipeline, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(pipeline, "classify_market_sessions", lambda markets, _: (markets, []))
    paid_processor = MagicMock()
    ingestion = MagicMock(side_effect=lambda *_args, **_kwargs: paid_processor())
    features = MagicMock()
    inference = MagicMock()
    monkeypatch.setattr(pipeline, "_run_ingestion_stage", ingestion)
    monkeypatch.setattr(pipeline, "_run_feature_stage", features)
    monkeypatch.setattr(pipeline, "_run_ml_stage", inference)

    result = pipeline.execute_eod_pipeline(
        _TRADING_DATE,
        markets=["us_equity"],
        tickers=["AAPL"],
        dry_run=True,
        ticker_config=cast(Any, SyntheticTickerConfig()),
    )

    assert result["ingestion"]["reason"] == "dry_run"
    assert result["features"]["reason"] == "dry_run"
    assert result["ml"]["reason"] == "dry_run"
    ingestion.assert_not_called()
    features.assert_not_called()
    inference.assert_not_called()
    paid_processor.assert_not_called()
