"""Destructive-target contracts for `make reset-lake` (handoff R7 / decision D3).

Every test runs against a temporary lake/models root: the canonical repository lake
is never reset here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from equity_lake.core.paths import BRONZE_DIR, GOLD_DIR, LAKE_DIR, MODELS_DIR, PLATINUM_PREDICTIONS_DIR, SILVER_DIR
from equity_lake.devtools.reset_lake import RESETTABLE_STATES, main, reset_lake, reset_targets
from equity_lake.storage.delta import write_delta

_DOCUMENTED_LABELS = ["01_bronze", "02_silver", "03_gold", "04_platinum/predictions", "models"]


def _bronze_price_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market": ["us_equity", "us_equity"],
            "mic": ["XNAS", "XNAS"],
            "symbol": ["AAPL", "MSFT"],
            "ticker": ["AAPL", "MSFT"],
            "date": [date(2024, 12, 2), date(2024, 12, 2)],
            "close": [100.0, 200.0],
            "identity_provenance": ["current_ticker_config", "current_ticker_config"],
        }
    )


def _synthetic_feature_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": ["AAPL"],
            "ticker": ["AAPL"],
            "date": [date(2024, 12, 2)],
            "action_coverage_source": ["synthetic_demo_seed"],
        }
    )


def _unmarked_feature_rows() -> pl.DataFrame:
    return _synthetic_feature_rows().with_columns(pl.lit("vendor_actions").alias("action_coverage_source"))


def _news_sentiment_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "article_id": ["a1", "a2"],
            "date": [date(2024, 12, 2), date(2024, 12, 12)],
            "sentiment_score": [0.4, -0.2],
        }
    )


def _macro_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "series": ["CPIAUCSL", "GDPC1"],
            "date": [date(2024, 12, 2), date(2024, 12, 12)],
            "value": [310.0, 23000.0],
        }
    )


def _populate_lake(root: Path, *, synthetic: bool) -> tuple[Path, Path]:
    """Create every documented target plus a sibling that must survive a reset."""
    lake = root / "lake"
    models = root / "models"
    for relative in ("01_bronze/market_data/us_equity", "02_silver/news_sentiment", "03_gold/features"):
        (lake / relative).mkdir(parents=True, exist_ok=True)
        (lake / relative / "sentinel.txt").write_text(relative, encoding="utf-8")
    (lake / "04_platinum/predictions").mkdir(parents=True)
    (lake / "04_platinum/predictions" / "sentinel.txt").write_text("prediction", encoding="utf-8")
    # Sibling of the predictions subpath: a reset must never remove the whole
    # 04_platinum layer (signal history lives there).
    (lake / "04_platinum/signals").mkdir(parents=True)
    (lake / "04_platinum/signals" / "sentinel.txt").write_text("signals", encoding="utf-8")
    models.mkdir(parents=True)
    (models / "model.pkl").write_text("model", encoding="utf-8")
    write_delta(_synthetic_feature_rows() if synthetic else _unmarked_feature_rows(), "03_gold/features", lake_dir=lake)
    return lake, models


def _snapshot(*paths: Path) -> dict[str, tuple[bool, int]]:
    return {str(path): (path.exists(), len(list(path.rglob("*"))) if path.is_dir() else 0) for path in paths}


def test_reset_targets_match_the_documented_artifact_set() -> None:
    targets = reset_targets()

    assert [target.label for target in targets] == _DOCUMENTED_LABELS
    assert [target.path for target in targets] == [BRONZE_DIR, SILVER_DIR, GOLD_DIR, PLATINUM_PREDICTIONS_DIR, MODELS_DIR]
    assert PLATINUM_PREDICTIONS_DIR.parent == LAKE_DIR / "04_platinum"
    assert PLATINUM_PREDICTIONS_DIR not in (BRONZE_DIR, SILVER_DIR, GOLD_DIR)


def test_dry_run_lists_every_target_and_writes_nothing(tmp_path: Path) -> None:
    lake, models = _populate_lake(tmp_path, synthetic=True)
    targets = reset_targets(lake_dir=lake, models_dir=models)
    before = _snapshot(*(target.path for target in targets))

    payload = reset_lake(lake_dir=lake, models_dir=models, dry_run=True)

    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert [entry["label"] for entry in payload["targets"]] == _DOCUMENTED_LABELS
    assert payload["removed"] == []
    assert payload["synthetic_provenance"] == "synthetic"
    assert "nothing was removed" in payload["message"]
    assert _snapshot(*(target.path for target in targets)) == before


def test_synthetic_lake_reset_removes_exactly_the_documented_targets(tmp_path: Path) -> None:
    lake, models = _populate_lake(tmp_path, synthetic=True)

    payload = reset_lake(lake_dir=lake, models_dir=models, assume_yes=True)

    assert payload["ok"] is True
    assert payload["synthetic_provenance"] == "synthetic"
    assert payload["removed"] == [str(target.path) for target in reset_targets(lake_dir=lake, models_dir=models)]
    for path in (lake / "01_bronze", lake / "02_silver", lake / "03_gold", lake / "04_platinum/predictions", models):
        assert not path.exists()
    # The rest of 04_platinum is not part of the artifact set.
    assert (lake / "04_platinum/signals/sentinel.txt").is_file()


def test_reset_refuses_an_unmarked_populated_lake_and_deletes_nothing(tmp_path: Path) -> None:
    """`unknown` (data present, no marker) is never treated as a synthetic lake."""
    lake, models = _populate_lake(tmp_path, synthetic=False)
    targets = reset_targets(lake_dir=lake, models_dir=models)
    before = _snapshot(*(target.path for target in targets))

    payload = reset_lake(lake_dir=lake, models_dir=models, assume_yes=True)

    assert payload["ok"] is False
    assert payload["synthetic_provenance"] == "unknown"
    assert payload["synthetic_provenance"] != "real"
    assert payload["removed"] == []
    assert "refused" in payload["message"]
    assert _snapshot(*(target.path for target in targets)) == before


def test_reset_refuses_a_bronze_only_lake_and_deletes_nothing(tmp_path: Path) -> None:
    """R7: `equity ingest` leaves Bronze rows before coverage/Gold; never resettable."""
    lake = tmp_path / "lake"
    models = tmp_path / "models"
    bronze = lake / "01_bronze/market_data/us_equity"
    assert write_delta(_bronze_price_rows(), "01_bronze/market_data/us_equity", lake_dir=lake)
    bronze_parquet = sorted(bronze.rglob("*.parquet"))
    assert bronze_parquet
    targets = reset_targets(lake_dir=lake, models_dir=models)
    before = _snapshot(*(target.path for target in targets))

    payload = reset_lake(lake_dir=lake, models_dir=models, assume_yes=True)

    assert payload["ok"] is False
    assert payload["synthetic_provenance"] == "unknown"
    assert payload["synthetic_provenance"] != "empty"
    assert payload["synthetic_provenance"] != "real"
    assert "refused" in payload["message"]
    assert "Bronze" in payload["synthetic_provenance_reason"]
    assert payload["removed"] == []
    # The ingested rows survive: the Bronze directory and its parquet file still exist.
    assert bronze.is_dir()
    for path in bronze_parquet:
        assert path.is_file()
    assert _snapshot(*(target.path for target in targets)) == before


def test_reset_main_exits_non_zero_when_the_lake_is_unknown_with_data(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lake, models = _populate_lake(tmp_path, synthetic=False)
    targets = reset_targets(lake_dir=lake, models_dir=models)
    before = _snapshot(*(target.path for target in targets))

    exit_code = main(["--lake-dir", str(lake), "--models-dir", str(models), "--yes"])

    assert exit_code == 1
    assert _snapshot(*(target.path for target in targets)) == before
    assert '"ok": false' in capsys.readouterr().out


def test_empty_lake_is_a_no_op_success(tmp_path: Path) -> None:
    payload = reset_lake(lake_dir=tmp_path / "lake", models_dir=tmp_path / "models", assume_yes=True)

    assert payload["ok"] is True
    assert payload["synthetic_provenance"] == "empty"
    assert payload["removed"] == []
    assert "nothing to remove" in payload["message"]


def test_rowless_lake_directories_report_empty_and_reset_without_refusal(tmp_path: Path) -> None:
    """Created-but-empty table directories hold nothing, so they are resettable."""
    lake = tmp_path / "lake"
    models = tmp_path / "models"
    for relative in (
        "01_bronze/market_data/us_equity",
        "02_silver/corporate_action_coverage/us_equity",
        "03_gold/features",
        "04_platinum/predictions",
    ):
        (lake / relative).mkdir(parents=True)

    payload = reset_lake(lake_dir=lake, models_dir=models, assume_yes=True)

    assert payload["ok"] is True
    assert payload["synthetic_provenance"] == "empty"
    assert not (lake / "01_bronze").exists()
    assert not (lake / "04_platinum/predictions").exists()


def test_reset_refuses_a_lake_whose_only_data_is_non_delta(tmp_path: Path) -> None:
    """R7/D3: uncountable bare-Parquet rows are never treated as an empty lake."""
    lake = tmp_path / "lake"
    models = tmp_path / "models"
    stray = lake / "02_silver/news_sentiment"
    stray.mkdir(parents=True)
    data_file = stray / "part-0.parquet"
    data_file.write_bytes(b"not-a-real-parquet-but-a-data-file")

    payload = reset_lake(lake_dir=lake, models_dir=models, assume_yes=True)

    assert payload["synthetic_provenance"] == "unknown"
    assert payload["ok"] is False
    assert payload["removed"] == []
    assert data_file.exists()


def test_reset_refuses_a_lake_holding_only_unenumerated_silver_rows(tmp_path: Path) -> None:
    """R7: rows only in `02_silver/news_sentiment` are rows a reset would delete.

    Before the generic dataset discovery this lake reported `empty` and was wiped.
    """
    lake = tmp_path / "lake"
    models = tmp_path / "models"
    news = lake / "02_silver/news_sentiment"
    assert write_delta(_news_sentiment_rows(), "02_silver/news_sentiment", lake_dir=lake)
    news_parquet = sorted(news.rglob("*.parquet"))
    assert news_parquet
    targets = reset_targets(lake_dir=lake, models_dir=models)
    before = _snapshot(*(target.path for target in targets))

    payload = reset_lake(lake_dir=lake, models_dir=models, assume_yes=True)

    # State first: on the pre-fix behaviour this is `empty` (and the lake was wiped).
    assert payload["synthetic_provenance"] == "unknown"
    assert payload["synthetic_provenance"] != "empty"
    assert payload["ok"] is False
    assert payload["removed"] == []
    assert "refused" in payload["message"]
    assert "02_silver/news_sentiment" in payload["synthetic_provenance_reason"]
    assert news.is_dir()
    for path in news_parquet:
        assert path.is_file()
    assert _snapshot(*(target.path for target in targets)) == before


def test_reset_refuses_a_lake_holding_only_bronze_macro_rows(tmp_path: Path) -> None:
    """R7: `01_bronze/macro` rows are equally real rows a reset must not delete."""
    lake = tmp_path / "lake"
    models = tmp_path / "models"
    macro = lake / "01_bronze/macro"
    assert write_delta(_macro_rows(), "01_bronze/macro", lake_dir=lake)
    macro_parquet = sorted(macro.rglob("*.parquet"))
    assert macro_parquet
    targets = reset_targets(lake_dir=lake, models_dir=models)
    before = _snapshot(*(target.path for target in targets))

    payload = reset_lake(lake_dir=lake, models_dir=models, assume_yes=True)

    # State first: on the pre-fix behaviour this is `empty` (and the lake was wiped).
    assert payload["synthetic_provenance"] == "unknown"
    assert payload["synthetic_provenance"] != "empty"
    assert payload["ok"] is False
    assert payload["removed"] == []
    assert "refused" in payload["message"]
    assert macro.is_dir()
    for path in macro_parquet:
        assert path.is_file()
    assert _snapshot(*(target.path for target in targets)) == before


def test_reset_refuses_a_symlinked_target_and_leaves_the_external_dir_intact(tmp_path: Path) -> None:
    """A symlinked artifact directory is refused, never followed, and never removed."""
    lake, models = _populate_lake(tmp_path, synthetic=True)
    external = tmp_path / "external-gold"
    (lake / "03_gold").rename(external)
    (lake / "03_gold").symlink_to(external, target_is_directory=True)
    external_files = sorted(str(path.relative_to(external)) for path in external.rglob("*"))
    targets = reset_targets(lake_dir=lake, models_dir=models)
    others = [target for target in targets if target.label != "03_gold"]
    before = _snapshot(*(target.path for target in others))

    payload = reset_lake(lake_dir=lake, models_dir=models, assume_yes=True)

    assert payload["ok"] is False
    assert "symlink" in payload["message"]
    assert str(lake / "03_gold") in payload["message"]
    assert payload["removed"] == []
    # The link and its external target both survive, and no other target was touched.
    assert (lake / "03_gold").is_symlink()
    assert external.is_dir()
    assert sorted(str(path.relative_to(external)) for path in external.rglob("*")) == external_files
    assert _snapshot(*(target.path for target in others)) == before


def test_synthetic_lake_with_bronze_rows_is_still_resettable(tmp_path: Path) -> None:
    """R7: the seeded lake holds Bronze rows too; the marker still wins."""
    lake, models = _populate_lake(tmp_path, synthetic=True)
    assert write_delta(_bronze_price_rows(), "01_bronze/market_data/us_equity", lake_dir=lake)

    payload = reset_lake(lake_dir=lake, models_dir=models, assume_yes=True)

    assert payload["ok"] is True
    assert payload["synthetic_provenance"] == "synthetic"
    assert not (lake / "01_bronze").exists()
    assert (lake / "04_platinum/signals/sentinel.txt").is_file()


def test_reset_requires_confirmation_without_yes(tmp_path: Path) -> None:
    lake, models = _populate_lake(tmp_path, synthetic=True)
    targets = reset_targets(lake_dir=lake, models_dir=models)
    before = _snapshot(*(target.path for target in targets))

    declined = reset_lake(lake_dir=lake, models_dir=models, assume_yes=False, confirm=lambda _prompt: "n")
    interrupted = reset_lake(lake_dir=lake, models_dir=models, assume_yes=False, confirm=_closed_stdin)

    assert declined["ok"] is False
    assert interrupted["ok"] is False
    assert "aborted" in declined["message"]
    assert declined["removed"] == interrupted["removed"] == []
    assert _snapshot(*(target.path for target in targets)) == before


def test_refusal_states_are_the_complement_of_the_resettable_states() -> None:
    """`real` is refused too, even though today's markers cannot produce it."""
    assert {"synthetic", "empty"} == RESETTABLE_STATES
    assert {"real", "unknown"}.isdisjoint(RESETTABLE_STATES)


def _closed_stdin(_prompt: str) -> str:
    raise EOFError
