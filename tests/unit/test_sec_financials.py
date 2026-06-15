"""Tests for SEC XBRL structured financials fetcher."""

from datetime import date
from unittest.mock import MagicMock, patch

from equity_lake.core.schemas import SEC_FINANCIAL_COLUMNS
from equity_lake.sources.sec_financials import SECFinancialsFetcher, _safe_div, _safe_float


class TestSafeHelpers:
    def test_safe_float_none(self):
        assert _safe_float(None) is None

    def test_safe_float_valid(self):
        assert _safe_float(42) == 42.0
        assert _safe_float("3.14") == 3.14

    def test_safe_float_invalid(self):
        assert _safe_float("not a number") is None

    def test_safe_div_none(self):
        assert _safe_div(None, 100) is None
        assert _safe_div(100, None) is None

    def test_safe_div_zero(self):
        assert _safe_div(100, 0) is None

    def test_safe_div_valid(self):
        assert _safe_div(100, 200) == 0.5


class TestSECFinancialsFetcherInit:
    def test_init_with_defaults(self):
        fetcher = SECFinancialsFetcher(tickers=["AAPL"])
        assert fetcher.tickers == ["AAPL"]
        assert fetcher.market == "us_sec_financials"
        assert fetcher.lookback_days > 0

    def test_init_empty_tickers(self):
        fetcher = SECFinancialsFetcher(tickers=[])
        assert fetcher.tickers == []


class TestSECFinancialsFetch:
    def test_fetch_empty_tickers_returns_empty(self):
        fetcher = SECFinancialsFetcher(tickers=[])
        df = fetcher.fetch(date(2024, 6, 15))
        assert df.is_empty()

    def test_fetch_handles_ticker_error(self):
        fetcher = SECFinancialsFetcher(tickers=["AAPL", "MSFT"])

        with patch.object(fetcher, "_fetch_ticker", side_effect=Exception("network error")):
            df = fetcher.fetch(date(2024, 6, 15))

        assert df.is_empty()

    def test_fetch_returns_correct_schema(self):
        mock_row = {
            "ticker": "AAPL",
            "date": date(2024, 1, 15),
            "filing_type": "10-K",
            "fiscal_year": "2023",
            "fiscal_period": "FY",
            "revenue": 383285000000.0,
            "net_income": 96995000000.0,
            "operating_income": 114301000000.0,
            "total_assets": 352755000000.0,
            "total_liabilities": 290437000000.0,
            "stockholders_equity": 62318000000.0,
            "total_debt": 111088000000.0,
            "cash_and_equivalents": 29965000000.0,
            "operating_cash_flow": 110543000000.0,
            "capex": -10939000000.0,
            "shares_outstanding": 15550061000.0,
            "eps": 6.24,
            "roe": 1.5564,
            "roa": 0.2749,
            "debt_to_equity": 1.7825,
            "net_margin": 0.2531,
            "operating_margin": 0.2982,
            "fetched_at": None,
        }

        fetcher = SECFinancialsFetcher(tickers=["AAPL"])
        with patch.object(fetcher, "_fetch_ticker", return_value=[mock_row]):
            df = fetcher.fetch(date(2024, 6, 15))

        assert not df.is_empty()
        assert set(SEC_FINANCIAL_COLUMNS).issubset(set(df.columns))
        row = df.row(0, named=True)
        assert row["ticker"] == "AAPL"
        assert row["revenue"] == 383285000000.0
        assert row["roe"] is not None

    def test_fetch_multiple_tickers(self):
        mock_rows_aapl = [{"ticker": "AAPL", "date": date(2024, 1, 15), "filing_type": "10-K"}]
        mock_rows_msft = [{"ticker": "MSFT", "date": date(2024, 1, 30), "filing_type": "10-K"}]

        fetcher = SECFinancialsFetcher(tickers=["AAPL", "MSFT"])

        with patch.object(fetcher, "_fetch_ticker", side_effect=[mock_rows_aapl, mock_rows_msft]):
            df = fetcher.fetch(date(2024, 6, 15))

        assert not df.is_empty()
        assert df.height == 2
        assert set(df["ticker"].to_list()) == {"AAPL", "MSFT"}


class TestExtractFinancials:
    def test_calculated_ratios(self):
        fetcher = SECFinancialsFetcher(tickers=["AAPL"])

        mock_filing = MagicMock()
        mock_xbrl = MagicMock()
        mock_statements = MagicMock()
        mock_filing.xbrl.return_value = mock_xbrl
        mock_xbrl.statements = mock_statements
        mock_filing.filing_year = "2023"
        mock_filing.fiscal_period = "FY"

        with patch.object(fetcher, "_get_metric") as mock_metric:

            def metric_side_effect(stmts, stmt_type, concepts):
                values = {
                    ("income_statement", "Revenues"): 100000.0,
                    ("income_statement", "NetIncomeLoss"): 20000.0,
                    ("income_statement", "OperatingIncomeLoss"): 30000.0,
                    ("balance_sheet", "Assets"): 200000.0,
                    ("balance_sheet", "Liabilities"): 120000.0,
                    ("balance_sheet", "StockholdersEquity"): 80000.0,
                    ("balance_sheet", "LongTermDebt"): 40000.0,
                    ("balance_sheet", "CashAndCashEquivalentsAtCarryingValue"): 10000.0,
                    ("cashflow_statement", "NetCashProvidedByUsedInOperatingActivities"): 50000.0,
                    ("cashflow_statement", "PaymentsToAcquirePropertyPlantAndEquipment"): -5000.0,
                }
                for concept in concepts:
                    if (stmt_type, concept) in values:
                        return values[(stmt_type, concept)]
                return None

            mock_metric.side_effect = metric_side_effect

            with patch.object(fetcher, "_get_shares", return_value=10000.0):
                row = fetcher._extract_financials(mock_filing, "AAPL", "10-K", date(2024, 1, 15), None)

        assert row is not None
        assert row["revenue"] == 100000.0
        assert row["net_income"] == 20000.0
        assert row["roe"] == 0.25  # 20000/80000
        assert row["roa"] == 0.1  # 20000/200000
        assert row["debt_to_equity"] == 0.5  # 40000/80000
        assert row["net_margin"] == 0.2  # 20000/100000
        assert row["operating_margin"] == 0.3  # 30000/100000
        assert row["eps"] == 2.0  # 20000/10000
