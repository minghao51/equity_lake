"""SEC filings loader."""

from __future__ import annotations

from datetime import date
from typing import cast
from xml.etree import ElementTree

import pandas as pd
import requests

from equity_lake.loaders.base import BaseDataLoader, LoaderMetadata, LoadResult

SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"


class SECFilingsLoader(BaseDataLoader):
    """Load SEC filing metadata and parse recent Form 4 transactions when available."""

    metadata = LoaderMetadata(
        name="sec_filings",
        description="SEC EDGAR filings and basic insider trade parsing.",
        supported_markets=["US"],
        data_types=["filings", "insider_trades"],
    )

    def _validate_config(self) -> None:
        self.user_agent = self.config.get(
            "user_agent",
            "Equity Lake contact@example.com",
        )
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    def load(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> LoadResult:
        records: list[dict[str, object]] = []
        errors: list[str] = []
        ticker_map = self._load_ticker_map()

        for symbol in symbols:
            cik = ticker_map.get(symbol.upper())
            if cik is None:
                errors.append(f"Unknown SEC ticker: {symbol}")
                continue
            try:
                records.extend(self._load_symbol_filings(symbol, cik, start_date, end_date))
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")

        frame = pd.DataFrame.from_records(records)
        return LoadResult(
            success=not errors,
            data=frame,
            records_count=len(frame),
            errors=errors,
        )

    def _load_ticker_map(self) -> dict[str, int]:
        response = self.session.get(SEC_TICKER_URL, timeout=30)
        response.raise_for_status()
        payload = response.json()
        return {entry["ticker"].upper(): int(entry["cik_str"]) for entry in payload.values()}

    def _load_symbol_filings(
        self,
        symbol: str,
        cik: int,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, object]]:
        padded_cik = f"{cik:010d}"
        response = self.session.get(
            f"https://data.sec.gov/submissions/CIK{padded_cik}.json",
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        recent = payload.get("filings", {}).get("recent", {})

        filing_dates = recent.get("filingDate", [])
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        records: list[dict[str, object]] = []
        for filing_date, form, accession, primary_doc in zip(
            filing_dates,
            forms,
            accessions,
            primary_docs,
            strict=False,
        ):
            filing_day = date.fromisoformat(filing_date)
            if not (start_date <= filing_day <= end_date):
                continue

            record = {
                "ticker": symbol,
                "date": filing_day,
                "filing_type": form,
                "accession_number": accession,
                "document_url": (f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{primary_doc}"),
            }

            if form == "4":
                record.update(self._parse_form4_if_possible(record["document_url"]))

            records.append(record)

        return records

    def _parse_form4_if_possible(self, document_url: str) -> dict[str, object]:
        try:
            response = self.session.get(str(document_url), timeout=30)
            response.raise_for_status()
            tree = ElementTree.fromstring(response.text)
        except Exception:
            return {}

        owner = tree.find(".//rptOwnerName")
        transaction = tree.find(".//nonDerivativeTransaction")
        if transaction is None:
            return {"insider_name": owner.text if owner is not None else ""}

        shares = transaction.findtext(".//transactionShares/value")
        price = transaction.findtext(".//transactionPricePerShare/value")
        return {
            "insider_name": owner.text if owner is not None else "",
            "transaction_shares": float(shares) if shares else 0.0,
            "transaction_price": float(price) if price else 0.0,
        }

    def get_available_symbols(self) -> list[str]:
        return []

    def validate_connection(self) -> bool:
        try:
            response: requests.Response = self.session.get(SEC_TICKER_URL, timeout=10)
        except Exception:
            return False
        return cast(bool, response.status_code == 200)


__all__ = ["SECFilingsLoader"]
