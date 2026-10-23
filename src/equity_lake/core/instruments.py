"""Canonical instrument identity and explicit provenance helpers (ADR-0016).

Ticker text is never interpreted to infer a market or venue. Exchange labels are
resolved only through the finite mapping below; historical rows still need
identity stored with the row or a separately supplied trusted provenance map.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import quote

import polars as pl
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from equity_lake.core.paths import canonical_market

IDENTITY_SCHEMA_VERSION = 1
IDENTITY_COLUMNS: tuple[str, ...] = ("market", "mic", "symbol")
IDENTITY_DATE_COLUMNS: tuple[str, ...] = (*IDENTITY_COLUMNS, "date")

# These values mirror explicit exchange labels used by config/tickers.yaml and
# source records. Unknown labels fail; no ticker-suffix or market-name guesses.
EXCHANGE_TO_MIC: dict[str, str] = {
    "NYSE": "XNYS",
    "NEW YORK STOCK EXCHANGE": "XNYS",
    "NASDAQ": "XNAS",
    "NASDAQ GS": "XNAS",
    "NASDAQ GLOBAL SELECT": "XNAS",
    "SSE": "XSHG",
    "SHANGHAI STOCK EXCHANGE": "XSHG",
    "SZSE": "XSHE",
    "SHENZHEN STOCK EXCHANGE": "XSHE",
    "HKEX": "XHKG",
    "HONG KONG EXCHANGES AND CLEARING": "XHKG",
    "SGX": "XSES",
    "SINGAPORE EXCHANGE": "XSES",
    "TSE": "XTKS",
    "JPX": "XTKS",
    "TOKYO STOCK EXCHANGE": "XTKS",
    "KRX": "XKRX",
    "KOREA EXCHANGE": "XKRX",
    "XNYS": "XNYS",
    "XNAS": "XNAS",
    "XSHG": "XSHG",
    "XSHE": "XSHE",
    "XHKG": "XHKG",
    "XSES": "XSES",
    "XTKS": "XTKS",
    "XKRX": "XKRX",
}

MARKET_MICS: dict[str, frozenset[str]] = {
    "us_equity": frozenset({"XNYS", "XNAS"}),
    "cn_ashare": frozenset({"XSHG", "XSHE"}),
    "hk_sg_equity": frozenset({"XHKG", "XSES"}),
    "jpx_equity": frozenset({"XTKS"}),
    "krx_equity": frozenset({"XKRX"}),
}


class InstrumentIdentityError(ValueError):
    """Identity is absent, malformed, or inconsistent with the requested scope."""


class LegacyIdentityUnavailableError(InstrumentIdentityError):
    """Legacy identity has no unique, explicitly trusted historical provenance."""


class InstrumentKey(BaseModel):
    """Canonical `(market, MIC, symbol)` instrument key (ADR-0016)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    mic: str
    symbol: str

    @field_validator("market")
    @classmethod
    def canonicalize_market(cls, value: str) -> str:
        return canonical_market(value.strip())

    @field_validator("mic")
    @classmethod
    def normalize_mic(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("MIC cannot be empty")
        return normalized

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Instrument symbol cannot be empty")
        return normalized

    @model_validator(mode="after")
    def mic_must_belong_to_market(self) -> InstrumentKey:
        supported = MARKET_MICS[self.market]
        if self.mic not in supported:
            raise ValueError(f"MIC {self.mic!r} is not supported for {self.market!r}; expected one of {sorted(supported)}")
        return self

    @classmethod
    def from_exchange(cls, market: str, exchange: str, symbol: str) -> InstrumentKey:
        """Construct from a source/config exchange label using explicit mapping."""
        mic = EXCHANGE_TO_MIC.get(exchange.strip().upper())
        if mic is None:
            raise InstrumentIdentityError(f"Exchange label {exchange!r} has no configured MIC mapping")
        return cls(market=market, mic=mic, symbol=symbol)

    @property
    def tuple(self) -> tuple[str, str, str]:
        return (self.market, self.mic, self.symbol)

    @property
    def artifact_token(self) -> str:
        """Opaque deterministic token for safe, collision-resistant filenames."""
        payload = json.dumps(self.tuple, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @property
    def display_key(self) -> str:
        return f"{self.market}/{self.mic}/{quote(self.symbol, safe='')}"

    def as_columns(self) -> dict[str, str]:
        """Identity fields plus the legacy ticker alias used by existing readers."""
        return {"market": self.market, "mic": self.mic, "symbol": self.symbol, "ticker": self.symbol}


def mic_for_exchange(exchange: str) -> str:
    """Resolve a configured/source exchange label; reject unknown spellings."""
    mic = EXCHANGE_TO_MIC.get(exchange.strip().upper())
    if mic is None:
        raise InstrumentIdentityError(f"Exchange label {exchange!r} has no configured MIC mapping")
    return mic


def instrument_from_record(record: Mapping[str, Any]) -> InstrumentKey:
    """Resolve identity only from explicit fields on a row/source record."""
    market = record.get("market")
    symbol = record.get("symbol", record.get("ticker"))
    if not isinstance(market, str) or not isinstance(symbol, str):
        raise InstrumentIdentityError("Record must include explicit market and symbol/ticker fields")
    mic_value = record.get("mic")
    if isinstance(mic_value, str) and mic_value.strip():
        mic = mic_value
    else:
        exchange = record.get("exchange")
        if not isinstance(exchange, str) or not exchange.strip():
            raise LegacyIdentityUnavailableError("Record has no stored MIC or explicit exchange provenance")
        mic = mic_for_exchange(exchange)
    try:
        return InstrumentKey(market=market, mic=mic, symbol=symbol)
    except (ValueError, TypeError) as exc:
        raise InstrumentIdentityError(str(exc)) from exc


def resolve_trusted_legacy_identity(symbol: str, trusted_provenance: Iterable[InstrumentKey]) -> InstrumentKey:
    """Resolve exactly one identity from an explicitly trusted historical map.

    This helper deliberately does not consult current ticker configuration. The
    caller owns provenance vetting; absent and ambiguous mappings fail closed.
    """
    candidates = {item.tuple: item for item in trusted_provenance if item.symbol == symbol}
    if not candidates:
        raise LegacyIdentityUnavailableError(f"No trusted historical identity for legacy symbol {symbol!r}")
    if len(candidates) != 1:
        raise LegacyIdentityUnavailableError(f"Ambiguous trusted historical identity for legacy symbol {symbol!r}")
    return next(iter(candidates.values()))


def normalize_identity_frame(
    frame: pl.DataFrame,
    *,
    trusted_legacy_provenance: Mapping[str, Sequence[InstrumentKey]] | None = None,
    deduplicate: bool = False,
) -> pl.DataFrame:
    """Validate/canonicalize identity columns, optionally resolving legacy rows.

    A legacy row is filled only by the provided historical provenance mapping,
    never from the current ticker config or from ticker spelling. When
    ``deduplicate`` is true, canonical rows win over matching legacy rows.
    """
    if frame.is_empty():
        return frame
    ticker_col = "symbol" if "symbol" in frame.columns else "ticker"
    if ticker_col not in frame.columns:
        raise InstrumentIdentityError("Frame must contain symbol or legacy ticker")
    out = frame
    if "symbol" not in out.columns:
        out = out.with_columns(pl.col(ticker_col).cast(pl.String).alias("symbol"))
    if "ticker" in out.columns and "symbol" in out.columns:
        mismatch = out.filter(pl.col("ticker").is_not_null() & pl.col("symbol").is_not_null() & (pl.col("ticker") != pl.col("symbol")))
        if mismatch.height:
            raise InstrumentIdentityError("ticker compatibility alias differs from canonical symbol")
    if "market" not in out.columns:
        out = out.with_columns(pl.lit(None, dtype=pl.String).alias("market"))
    if "mic" not in out.columns:
        out = out.with_columns(pl.lit(None, dtype=pl.String).alias("mic"))
    out = out.with_columns(pl.col("market").cast(pl.String), pl.col("mic").cast(pl.String), pl.col("symbol").cast(pl.String))

    rows = out.to_dicts()
    normalized: list[dict[str, Any]] = []
    for row in rows:
        symbol = row.get("symbol")
        market = row.get("market")
        mic = row.get("mic")
        if isinstance(symbol, str) and isinstance(market, str) and isinstance(mic, str) and market.strip() and mic.strip():
            identity = InstrumentKey(market=market, mic=mic, symbol=symbol)
        else:
            provenance = (trusted_legacy_provenance or {}).get(str(symbol), ())
            identity = resolve_trusted_legacy_identity(str(symbol), provenance)
            if isinstance(market, str) and market.strip() and market.strip() != identity.market:
                raise LegacyIdentityUnavailableError(
                    f"Trusted historical identity market {identity.market!r} conflicts with stored market {market!r}"
                )
            if isinstance(mic, str) and mic.strip() and mic.strip().upper() != identity.mic:
                raise LegacyIdentityUnavailableError(f"Trusted historical identity MIC {identity.mic!r} conflicts with stored MIC {mic!r}")
        if isinstance(market, str) and market.strip() and canonical_market(market.strip()) != identity.market:
            raise InstrumentIdentityError(f"Resolved identity market {identity.market!r} conflicts with stored market {market!r}")
        if isinstance(mic, str) and mic.strip() and mic.strip().upper() != identity.mic:
            raise InstrumentIdentityError(f"Resolved identity MIC {identity.mic!r} conflicts with stored MIC {mic!r}")
        exchange = row.get("exchange")
        if isinstance(exchange, str) and exchange.strip() and mic_for_exchange(exchange) != identity.mic:
            raise InstrumentIdentityError(f"Resolved identity MIC {identity.mic!r} conflicts with stored exchange {exchange!r}")
        row.update(identity.as_columns())
        normalized.append(row)
    out = pl.DataFrame(normalized, infer_schema_length=None)
    schema_casts = [pl.col(column).cast(dtype, strict=False) for column, dtype in frame.schema.items() if column in out.columns and dtype != pl.Null]
    if schema_casts:
        out = out.with_columns(schema_casts)
    if deduplicate and "date" in out.columns:
        # Prefer rows that carried identity in storage over provenance-enriched
        # legacy rows; neither form can multiply one logical key on dual read.
        canonical_flags = (
            frame.with_columns(
                pl.when(pl.col("market").is_not_null() & pl.col("mic").is_not_null() & (pl.col("market").cast(pl.String).str.len_chars() > 0))
                .then(1)
                .otherwise(0)
                .alias("__identity_priority")
            )["__identity_priority"]
            if {"market", "mic"}.issubset(frame.columns)
            else pl.Series("__identity_priority", [0] * frame.height)
        )
        out = (
            out.with_columns(canonical_flags)
            .sort("__identity_priority", descending=True)
            .unique(subset=[*IDENTITY_DATE_COLUMNS], keep="first", maintain_order=True)
            .drop("__identity_priority")
        )
    return out


def prepare_source_identity_frame(frame: pl.DataFrame, market: str) -> pl.DataFrame:
    """Validate new price rows and attach their explicit ingestion identity.

    ``market`` is the current source route, not historical provenance. Venue
    identity must still be present as a MIC or explicit exchange label on each
    row. Existing identity metadata is preserved and checked; this helper never
    consults today's ticker configuration to rewrite old stored rows.
    """
    if frame.is_empty():
        return frame
    canonical_route = canonical_market(market)
    ticker_col = "symbol" if "symbol" in frame.columns else "ticker"
    if ticker_col not in frame.columns:
        raise InstrumentIdentityError("New price rows must contain symbol or ticker")
    if "symbol" in frame.columns and "ticker" in frame.columns:
        mismatch = frame.filter(pl.col("ticker").is_not_null() & pl.col("symbol").is_not_null() & (pl.col("ticker") != pl.col("symbol")))
        if mismatch.height:
            raise InstrumentIdentityError("ticker compatibility alias differs from canonical symbol")

    prepared: list[dict[str, Any]] = []
    for row in frame.to_dicts():
        stored_market = row.get("market")
        if isinstance(stored_market, str) and stored_market.strip() and canonical_market(stored_market.strip()) != canonical_route:
            raise InstrumentIdentityError(f"Price row market {stored_market!r} conflicts with source route {canonical_route!r}")
        row["market"] = canonical_route
        symbol = row.get("symbol", row.get("ticker"))
        if not isinstance(symbol, str) or not symbol.strip():
            raise InstrumentIdentityError("New price row has no non-empty symbol/ticker")
        mic = row.get("mic")
        exchange = row.get("exchange")
        if (
            isinstance(mic, str)
            and mic.strip()
            and isinstance(exchange, str)
            and exchange.strip()
            and mic_for_exchange(exchange) != mic.strip().upper()
        ):
            raise InstrumentIdentityError(f"Source MIC {mic!r} conflicts with exchange label {exchange!r}")
        if not (isinstance(mic, str) and mic.strip()) and not (isinstance(exchange, str) and exchange.strip()):
            raise LegacyIdentityUnavailableError(
                f"New price row {symbol!r} has no explicit MIC or exchange provenance; refusing unidentified ingestion"
            )
        identity = instrument_from_record(row)
        if identity.market != canonical_route:
            raise InstrumentIdentityError(f"Price row identity market {identity.market!r} conflicts with source route {canonical_route!r}")
        version = row.get("identity_schema_version")
        if version is not None:
            try:
                parsed_version = int(version)
            except (TypeError, ValueError) as exc:
                raise InstrumentIdentityError(f"Invalid identity schema version on new price row: {version!r}") from exc
            if parsed_version != IDENTITY_SCHEMA_VERSION:
                raise InstrumentIdentityError(f"Unsupported identity schema version on new price row: {version!r}")
        provenance = row.get("identity_provenance")
        if provenance is None:
            provenance = "explicit_mic" if isinstance(mic, str) and mic.strip() else "explicit_exchange"
        elif provenance not in {"explicit_mic", "explicit_exchange", "provider_mic", "provider_exchange", "current_ticker_config"}:
            raise InstrumentIdentityError(f"Unknown identity provenance label {provenance!r}")
        row.update(identity.as_columns())
        row["identity_schema_version"] = IDENTITY_SCHEMA_VERSION
        row["identity_provenance"] = provenance
        prepared.append(row)
    return pl.DataFrame(prepared, infer_schema_length=None)


def instrument_identity(frame: pl.DataFrame) -> InstrumentKey:
    """Return the sole canonical instrument in a frame or fail on ambiguity."""
    normalized = normalize_identity_frame(frame)
    identities = normalized.select(*IDENTITY_COLUMNS).unique()
    if identities.height != 1:
        raise InstrumentIdentityError(f"Expected exactly one instrument identity, found {identities.height}")
    record = identities.row(0, named=True)
    return InstrumentKey(**record)


def identity_key_values(frame: pl.DataFrame) -> list[tuple[str, str, str]]:
    """Return validated, distinct canonical keys from a non-legacy frame."""
    normalized = normalize_identity_frame(frame)
    return [tuple(row) for row in normalized.select(*IDENTITY_COLUMNS).unique().iter_rows()]


__all__ = [
    "EXCHANGE_TO_MIC",
    "IDENTITY_COLUMNS",
    "IDENTITY_DATE_COLUMNS",
    "InstrumentIdentityError",
    "InstrumentKey",
    "LegacyIdentityUnavailableError",
    "MARKET_MICS",
    "identity_key_values",
    "instrument_from_record",
    "instrument_identity",
    "mic_for_exchange",
    "normalize_identity_frame",
    "prepare_source_identity_frame",
    "resolve_trusted_legacy_identity",
]
