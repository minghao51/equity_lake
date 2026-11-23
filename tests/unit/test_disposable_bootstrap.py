"""RS-E01: disposable bootstrap guards, reproducibility and the H00 canary refusal.

Every destination here is a ``tmp_path``/OS-temporary allocation. The operator lake is
only ever *read* (hashed before and after) or monkeypatched to a sentinel; no test in
this file writes into the operator checkout or ``data/lake_real/``.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from equity_lake.core import paths  # type: ignore[import-untyped]
from equity_lake.devtools.bootstrap import (  # type: ignore[import-untyped]
    BootstrapError,
    BootstrapRefused,
    _validate_members,
    _verify_checkout_provenance,
    bootstrap_disposable_lake,
    unavailable_datasets,
)
from equity_lake.devtools.seed_demo import seed_demo  # type: ignore[import-untyped]

pytestmark = pytest.mark.slow

TABLE = "01_bronze/market_data/us_equity"
END = date(2026, 11, 20)


def _snapshot(root: Path) -> dict[str, str]:
    """Every file under *root* mapped to its sha256 (the "unchanged" proof)."""
    if not root.exists():
        return {}
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.rglob("*")) if path.is_file()}


def test_disposable_bootstrap_seeds_only_its_own_checkout(tmp_path: Path) -> None:
    """(a)-(d): fresh allocation, tracked-source checkout, canonical lake, own module."""
    from equity_lake.core.calendar import is_trading_day  # type: ignore[import-untyped]
    from equity_lake.storage.delta import read_delta  # type: ignore[import-untyped]

    assert is_trading_day("us_equity", END)
    allocation = tmp_path / "allocation"
    allocation.mkdir()
    operator_lake = paths.LAKE_DIR
    before = _snapshot(operator_lake)

    result = bootstrap_disposable_lake(seed=42, end_date=END, years=0.1, tickers=["AAPL", "MSFT"], allocation=allocation, keep=True)

    # The operator's lake is untouched, by file list and by content.
    assert _snapshot(operator_lake) == before
    checkout = (allocation / "checkout").resolve()
    assert result["checkout"] == str(checkout)
    assert result["project_root"] == str(checkout)
    assert result["lake_dir"] == str((checkout / "data" / "lake").resolve())
    assert Path(result["lake_dir"]).is_relative_to(allocation.resolve())
    # (d) module provenance is the disposable checkout, not the operator checkout.
    assert Path(result["module"]).is_relative_to(checkout)
    assert not Path(result["module"]).is_relative_to(paths.PROJECT_ROOT / "src")
    assert result["revision"]
    assert result["source"] == "synthetic"
    assert result["rows"] > 0 and result["days"] > 0
    # (b) tracked source only: no dotenv and no real/runtime data came from the archive.
    assert not (checkout / ".env").exists()
    assert not (checkout / "data" / "lake_real").exists()
    assert (checkout / "data" / "catalog.jsonl").is_file()
    assert sorted(path.name for path in (checkout / "data").iterdir()) == [".gitkeep", "catalog.jsonl", "lake"]

    frame = read_delta(TABLE, lake_dir=Path(result["lake_dir"]))
    assert frame["price_provenance"].unique().to_list() == ["synthetic"]
    assert frame["date"].max() == END
    assert sorted(frame["ticker"].unique().to_list()) == ["AAPL", "MSFT"]


def test_same_seed_and_end_date_produce_the_same_bootstrap_digest(tmp_path: Path) -> None:
    """Reproducible bootstrap: two independent allocations, one logical digest."""
    digests = []
    for name in ("first", "second"):
        allocation = tmp_path / name
        allocation.mkdir()
        result = bootstrap_disposable_lake(seed=7, end_date=END, years=0.1, tickers=["AAPL"], allocation=allocation, keep=True)
        digests.append(result["price_digest"])
    assert digests[0] == digests[1]


def test_operator_lake_sentinel_refused_even_with_the_legacy_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(g): an operator lake is never a destination; `overwrite_production_lake` is no authorization."""
    sentinel = tmp_path / "operator" / "data" / "lake"
    (sentinel / "01_bronze").mkdir(parents=True)
    (sentinel / "01_bronze" / "ingested.parquet").write_bytes(b"operator rows")
    (sentinel / "holdings.csv").write_text("private", encoding="utf-8")
    monkeypatch.setattr(paths, "LAKE_DIR", sentinel)
    before = _snapshot(sentinel)

    allocation = tmp_path / "allocation"
    allocation.mkdir()
    with pytest.raises(BootstrapRefused):
        bootstrap_disposable_lake(
            seed=42,
            end_date=END,
            years=0.1,
            tickers=["AAPL"],
            allocation=allocation,
            lake_dir=sentinel,
            overwrite_production_lake=True,
        )

    # Refused before any work: the sentinel is byte-identical and the allocation is empty.
    assert _snapshot(sentinel) == before
    assert list(allocation.iterdir()) == []


def test_reused_nonempty_temporary_target_refuses(tmp_path: Path) -> None:
    """(f): a temporary target that already holds anything is not disposable."""
    allocation = tmp_path / "allocation"
    allocation.mkdir()
    (allocation / "earlier-run.txt").write_text("stale", encoding="utf-8")

    with pytest.raises(BootstrapRefused, match="not empty"):
        bootstrap_disposable_lake(seed=42, end_date=END, years=0.1, tickers=["AAPL"], allocation=allocation)

    assert sorted(path.name for path in allocation.iterdir()) == ["earlier-run.txt"]


def test_symlinked_destination_alias_refuses(tmp_path: Path) -> None:
    """(e): a symlink alias that only *looks* like it lives beneath the allocation."""
    allocation = tmp_path / "allocation"
    allocation.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(allocation, target_is_directory=True)

    with pytest.raises(BootstrapRefused, match="symlink alias"):
        bootstrap_disposable_lake(seed=42, end_date=END, years=0.1, tickers=["AAPL"], allocation=allocation, lake_dir=alias / "data" / "lake")

    assert list(allocation.iterdir()) == []


def test_import_resolving_to_the_operator_checkout_refuses(tmp_path: Path) -> None:
    """(d): a child that imported the operator checkout is refused, not accepted."""
    allocation = tmp_path / "allocation"
    allocation.mkdir()
    checkout = allocation / "checkout"
    destination = checkout / "data" / "lake"
    operator_module = paths.PROJECT_ROOT / "src" / "equity_lake" / "core" / "paths.py"

    with pytest.raises(BootstrapError, match="imported equity_lake from the operator checkout"):
        _verify_checkout_provenance(
            {"module": str(operator_module), "project_root": str(checkout), "lake_dir": str(destination)},
            allocation=allocation,
            checkout=checkout,
            destination=destination,
            operator_root=paths.PROJECT_ROOT,
        )
    with pytest.raises(BootstrapError, match="outside the disposable allocation"):
        _verify_checkout_provenance(
            {
                "module": str(checkout / "src" / "equity_lake" / "core" / "paths.py"),
                "project_root": str(paths.PROJECT_ROOT),
                "lake_dir": str(destination),
            },
            allocation=allocation,
            checkout=checkout,
            destination=destination,
            operator_root=paths.PROJECT_ROOT,
        )


def test_tracked_source_validation_refuses_dotenv_and_runtime_data() -> None:
    """(b): the extracted tree can carry neither a dotenv file nor ignored runtime data."""
    for name in (".env", "data/lake/01_bronze/market_data/us_equity/part-0.parquet", "data/lake_real/x"):
        member = tarfile.TarInfo(name)
        member.size = 1
        with pytest.raises(BootstrapRefused):
            _validate_members([member])


def test_unavailable_datasets_are_reported_with_a_reason_and_never_stubbed(tmp_path: Path) -> None:
    """Unavailable stays unavailable: a reason per absent optional/provider dataset."""
    lake_root = tmp_path / "lake"
    (lake_root / "01_bronze" / "market_data" / "us_equity").mkdir(parents=True)
    entries = {entry["dataset"]: entry for entry in unavailable_datasets(lake_root)}

    for dataset in paths.OPTIONAL_ENRICHMENT_MARKETS:
        assert entries[dataset]["available"] is False
        assert entries[dataset]["reason"]
    for route in ("03_gold/features", "04_platinum/predictions"):
        assert entries[route]["available"] is False
        assert entries[route]["reason"]
    assert entries["us_equity"]["available"] is True
    # Never stubbed: nothing was created for an unavailable dataset.
    assert sorted(path.name for path in lake_root.iterdir()) == ["01_bronze"]
    assert not (lake_root / "03_gold").exists()
    assert not (lake_root / "04_platinum").exists()


# ---------------------------------------------------------------------------
# H00 sanitization: canary refusal without editing dashboard/h00.py
# ---------------------------------------------------------------------------


def _sandbox_lake(tmp_path: Path) -> Path:
    lake = tmp_path / "data" / "lake"
    seed_demo(years=0.1, tickers=["AAPL"], seed=3, end_date=END, lake_dir=lake)
    return lake


def test_clean_synthetic_lake_builds_exactly_the_three_allowlisted_files(tmp_path: Path) -> None:
    from equity_lake.dashboard.h00 import ARTIFACT_FILENAMES, build_sanitized_page  # type: ignore[import-untyped]

    artifact = build_sanitized_page(lake_dir=_sandbox_lake(tmp_path), output_dir=tmp_path / "artifact", allow_sandbox_root=True)

    assert sorted(path.name for path in artifact.directory.iterdir()) == sorted(ARTIFACT_FILENAMES)
    report = json.loads(artifact.report_path.read_text(encoding="utf-8"))
    assert report["root"] == "demo_lake_only"
    assert report["provenance"] == "synthetic"
    assert report["passed"] is True


def test_canary_path_and_secret_in_the_lake_are_refused_by_the_scan(tmp_path: Path) -> None:
    """The existing negative scan refuses composed bytes that carry a path/value canary."""
    from equity_lake.dashboard.h00 import H00BuildError, build_sanitized_page  # type: ignore[import-untyped]
    from equity_lake.storage.delta import read_delta, write_delta  # type: ignore[import-untyped]

    lake = _sandbox_lake(tmp_path)
    frame = read_delta(TABLE, lake_dir=lake)
    path_canary = f"{paths.PROJECT_ROOT}/.env"
    value_canary = "token=CANARY-SECRET-VALUE-do-not-publish"
    canary = frame.with_columns(
        pl.lit(path_canary, dtype=pl.String).alias(path_canary),
        pl.lit(value_canary, dtype=pl.String).alias(value_canary),
    )
    write_delta(canary, TABLE, mode="overwrite", lake_dir=lake, schema_mode="overwrite")

    output = tmp_path / "artifact"
    with pytest.raises(H00BuildError, match="sanitization scan failed closed"):
        build_sanitized_page(lake_dir=lake, output_dir=output, allow_sandbox_root=True)
    assert not output.exists()


def test_canary_dotenv_value_is_caught_by_the_absence_needles(tmp_path: Path) -> None:
    """The `.env`-value class is a real needle (proved with a private temporary dotenv)."""
    from equity_lake.dashboard.h00 import forbidden_needles, scan_contents  # type: ignore[import-untyped]

    dotenv = tmp_path / ".env"
    dotenv.write_text("PROVIDER_API_KEY=CANARY-DOTENV-VALUE-1234\n", encoding="utf-8")
    needles = forbidden_needles(tmp_path / "artifact", env_path=dotenv)

    assert scan_contents({"payload.json": '{"note": "CANARY-DOTENV-VALUE-1234"}'}, needles) == {"secrets": 1}
    assert scan_contents({"payload.json": '{"note": "synthetic demo"}'}, needles) == {}
