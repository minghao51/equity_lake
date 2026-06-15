"""SEC XBRL structured financials fetcher for US equities.

Uses ``edgartools`` to extract balance sheet, income statement, and cash
flow data from 10-K/10-Q filings. Produces structured rows with key
financial metrics and calculated ratios.

Output is written to the ``us_sec_financials`` Delta table. Unlike
``sec_fulltext.py`` (which stores raw text for LLM processing), this
module produces structured numeric data — no LLM needed.

Rate limit: SEC EDGAR allows 10 req/s. ``edgartools`` handles rate
limiting internally via ``httpxthrottlecache``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl
import structlog

from equity_lake.core.schemas import SEC_FINANCIAL_COLUMNS
from equity_lake.sources.base import MarketDataFetcher, _empty_frame

logger = structlog.get_logger()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


class SECFinancialsFetcher(MarketDataFetcher):
    """Fetch structured XBRL financials from SEC EDGAR via ``edgartools``.

    Each filing produces one row with key financial metrics and calculated
    ratios. No LLM processing — this is pure structured data extraction.

    Args:
        tickers: List of ticker symbols.
        lookback_days: Look back period for finding recent filings.
    """

    market = "us_sec_financials"

    def __init__(
        self,
        tickers: list[str] | None = None,
        lookback_days: int = 120,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
    ):
        super().__init__(retry_attempts, retry_delay)
        self.tickers = tickers or []
        self.lookback_days = lookback_days

    def fetch(self, trading_date: date) -> pl.DataFrame:
        if not self.tickers:
            logger.warning("No tickers configured for SEC financials")
            return _empty_frame()

        cutoff_date = trading_date - timedelta(days=self.lookback_days)
        now = datetime.now(UTC).replace(tzinfo=None)

        all_rows: list[dict[str, Any]] = []
        for ticker in self.tickers:
            try:
                rows = self._fetch_ticker(ticker, trading_date, cutoff_date, now)
                all_rows.extend(rows)
            except Exception as exc:
                logger.error("sec_financials_fetch_failed", ticker=ticker, error=str(exc))

        if not all_rows:
            logger.info("No SEC financials found")
            return _empty_frame()

        df = pl.DataFrame(all_rows)
        for col in SEC_FINANCIAL_COLUMNS:
            if col not in df.columns:
                df = df.with_columns(pl.lit(None).alias(col))

        df = df.select(SEC_FINANCIAL_COLUMNS)
        logger.info("Fetched SEC financials", count=df.height)
        return df

    def _fetch_ticker(
        self,
        ticker: str,
        trading_date: date,
        cutoff_date: date,
        now: datetime,
    ) -> list[dict[str, Any]]:
        from edgar import Company

        company = Company(ticker)
        rows: list[dict[str, Any]] = []

        for form_type in ("10-K", "10-Q"):
            try:
                filings = company.get_filings(form=form_type)
                if not filings:
                    continue

                latest = filings[0]
                filing_date_str = getattr(latest, "filing_date", None)
                if filing_date_str is None:
                    continue

                filing_day = filing_date_str if isinstance(filing_date_str, date) else date.fromisoformat(str(filing_date_str)[:10])

                if filing_day < cutoff_date or filing_day > trading_date:
                    continue

                row = self._extract_financials(latest, ticker, form_type, filing_day, now)
                if row:
                    rows.append(row)
            except Exception as exc:
                logger.warning("sec_financials_form_failed", ticker=ticker, form=form_type, error=str(exc))

        return rows

    def _extract_financials(
        self,
        filing: Any,
        ticker: str,
        form_type: str,
        filing_date: date,
        now: datetime,
    ) -> dict[str, Any] | None:
        try:
            xbrl = filing.xbrl()
            if xbrl is None:
                return None

            financials = xbrl.statements

            revenue = _safe_float(self._get_metric(financials, "income_statement", ["Revenues", "Revenue", "TotalRevenue", "SalesRevenueNet"]))
            net_income = _safe_float(self._get_metric(financials, "income_statement", ["NetIncomeLoss"]))
            operating_income = _safe_float(self._get_metric(financials, "income_statement", ["OperatingIncomeLoss"]))
            total_assets = _safe_float(self._get_metric(financials, "balance_sheet", ["Assets", "TotalAssets"]))
            total_liabilities = _safe_float(self._get_metric(financials, "balance_sheet", ["Liabilities", "TotalLiabilities"]))
            stockholders_equity = _safe_float(self._get_metric(financials, "balance_sheet", ["StockholdersEquity", "TotalStockholdersEquity"]))
            total_debt = _safe_float(self._get_metric(financials, "balance_sheet", ["LongTermDebt", "DebtLongtermAndShorttermCombinedAmount"]))
            cash = _safe_float(
                self._get_metric(financials, "balance_sheet", ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestricted"])
            )
            operating_cash_flow = _safe_float(self._get_metric(financials, "cashflow_statement", ["NetCashProvidedByUsedInOperatingActivities"]))
            capex = _safe_float(self._get_metric(financials, "cashflow_statement", ["PaymentsToAcquirePropertyPlantAndEquipment"]))
            shares = _safe_float(self._get_shares(xbrl))
            eps = _safe_div(net_income, shares) if shares else None

            roe = _safe_div(net_income, stockholders_equity)
            roa = _safe_div(net_income, total_assets)
            debt_to_equity = _safe_div(total_debt, stockholders_equity)
            net_margin = _safe_div(net_income, revenue)
            operating_margin = _safe_div(operating_income, revenue)

            fiscal_year = getattr(filing, "fiscal_year", str(filing_date.year))
            fiscal_period = getattr(filing, "fiscal_period", "")

            return {
                "ticker": ticker,
                "date": filing_date,
                "filing_type": form_type,
                "fiscal_year": str(fiscal_year),
                "fiscal_period": str(fiscal_period),
                "revenue": revenue,
                "net_income": net_income,
                "operating_income": operating_income,
                "total_assets": total_assets,
                "total_liabilities": total_liabilities,
                "stockholders_equity": stockholders_equity,
                "total_debt": total_debt,
                "cash_and_equivalents": cash,
                "operating_cash_flow": operating_cash_flow,
                "capex": capex,
                "shares_outstanding": shares,
                "eps": eps,
                "roe": roe,
                "roa": roa,
                "debt_to_equity": debt_to_equity,
                "net_margin": net_margin,
                "operating_margin": operating_margin,
                "fetched_at": now,
            }
        except Exception as exc:
            logger.warning("sec_financials_extract_failed", ticker=ticker, error=str(exc))
            return None

    def _get_metric(self, statements: Any, statement_type: str, concept_names: list[str]) -> Any:
        try:
            if statement_type == "income_statement":
                stmt = statements.income_statement()
            elif statement_type == "balance_sheet":
                stmt = statements.balance_sheet()
            elif statement_type == "cashflow_statement":
                stmt = statements.cashflow_statement()
            else:
                return None

            df = stmt.to_dataframe()
            for concept in concept_names:
                if concept in df.columns:
                    values = df[concept].dropna()
                    if len(values) > 0:
                        return values.iloc[0]
        except Exception:
            pass
        return None

    def _get_shares(self, xbrl: Any) -> Any:
        try:
            facts = xbrl.data
            for concept in ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"]:
                if concept in facts:
                    return facts[concept].value
        except Exception:
            pass
        return None


__all__ = ["SECFinancialsFetcher"]
