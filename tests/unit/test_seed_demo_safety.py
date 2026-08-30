"""Safety-rail tests for devtools/seed_demo (P1: protect the canonical lake)."""

from __future__ import annotations

import pytest

from equity_lake.core import paths
from equity_lake.devtools.seed_demo import seed_demo

pytestmark = pytest.mark.slow

DEMO_TABLE = "01_bronze/market_data/us_equity"


def test_default_target_is_sample_lake(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Without --lake the seed goes to the auxiliary sample lake, never data/lake."""
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    summary = seed_demo(years=0.1, tickers=["AAA"], verbose=False)
    assert summary["path"] == str(tmp_path / "sample")
    assert (tmp_path / "sample" / DEMO_TABLE).exists()
    assert summary["dry_run"] is False


def test_refuses_canonical_lake_without_override(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Targeting LAKE_DIR (or a path under it) without authorization raises."""
    fake_lake = tmp_path / "lake"
    monkeypatch.setattr(paths, "LAKE_DIR", fake_lake)
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        seed_demo(years=0.1, tickers=["AAA"], lake_dir=fake_lake)
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        seed_demo(years=0.1, tickers=["AAA"], lake_dir=fake_lake / "copy")
    assert not fake_lake.exists()


def test_refuses_canonical_lake_via_case_variant_path(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """A differently-cased spelling of LAKE_DIR must not bypass the guard.

    The symlink simulates a case-insensitive filesystem where both spellings
    exist and resolve to the same underlying directory (os.path.samefile).
    """
    fake_lake = tmp_path / "lake"
    fake_lake.mkdir()
    monkeypatch.setattr(paths, "LAKE_DIR", fake_lake)
    variant = tmp_path / "LAKE"
    variant.symlink_to(fake_lake, target_is_directory=True)

    with pytest.raises(ValueError, match="Refusing to overwrite"):
        seed_demo(years=0.1, tickers=["AAA"], lake_dir=variant)
    assert not (fake_lake / DEMO_TABLE).exists()


def test_dry_run_writes_nothing(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    summary = seed_demo(years=0.1, tickers=["AAA"], dry_run=True)
    assert summary["dry_run"] is True
    assert summary["rows"] > 0
    assert not (tmp_path / "sample").exists()


def test_authorized_production_overwrite_writes(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    fake_lake = tmp_path / "lake"
    monkeypatch.setattr(paths, "LAKE_DIR", fake_lake)
    summary = seed_demo(years=0.1, tickers=["AAA"], lake_dir=fake_lake, overwrite_production_lake=True)
    assert summary["path"] == str(fake_lake)
    assert (fake_lake / DEMO_TABLE).exists()
