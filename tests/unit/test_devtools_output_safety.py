"""Guards: devtool data generators must not write into the canonical lake (data/lake/**)."""

from __future__ import annotations

from equity_lake.core.paths import DATA_DIR, LAKE_DIR
from equity_lake.devtools.test_data import MARKET_CONFIGS, TEST_DATA_SANDBOX_DIR


def test_test_data_outputs_live_in_sandbox() -> None:
    """Every test_data market writes under data/sandbox/test_data/, never data/lake/."""
    for market, config in MARKET_CONFIGS.items():
        out = config["output_dir"]
        assert out == TEST_DATA_SANDBOX_DIR / market
        assert out.is_relative_to(DATA_DIR)
        assert not out.is_relative_to(LAKE_DIR)


def test_test_data_module_has_no_canonical_dir_imports() -> None:
    """The redirected generator must not carry the canonical market dir constants."""
    import equity_lake.devtools.test_data as test_data_module

    for name in ("US_EQUITY_DIR", "CN_ASHARE_DIR", "HK_SG_EQUITY_DIR"):
        assert not hasattr(test_data_module, name)
