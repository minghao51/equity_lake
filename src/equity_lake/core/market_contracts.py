"""Per-market contract declarations with an explicit ``unknown`` state (RS-M01..RS-M04).

Residual plan §2.9 asks, per non-US price market, for the §2.9 contract set to be
either delivered or declared absent, with the exact input packet that would close
it. This module is the in-code half of that record: every price market declares a
:class:`Capability` for each name in :data:`CONTRACT_CAPABILITIES`, holding either

- ``present`` plus the ``file:line`` evidence that proves it, or
- ``unknown`` plus the input packet (source, licence, vintage/clock, coverage)
  that would close the gap.

It deliberately states no market rule. A board-lot size, a price-limit band, a
settlement cycle or a rights term that cannot be verified offline stays
``unknown``, and :func:`require_capability` fails closed on it, so a consumer can
never mistake an absent rule for a satisfied one.

Currency is not declared here: it already lives in ``config/tickers.yaml``
(``markets.<key>.currency``) and is read through
``core.config_models.MarketConfig``. Repeating it here would create a second
source of truth to drift from.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from equity_lake.core.instruments import MARKET_MICS
from equity_lake.core.paths import PRICE_MARKETS, canonical_market

CapabilityState = Literal["present", "unknown"]

#: Contract names every declared price market answers. They are the §2.9
#: families with no data model in the repository; itemising them keeps the same
#: names in the per-market feasibility packs under
#: ``docs/developer/architecture/market-*-feasibility.md``.
CONTRACT_CAPABILITIES: Final[tuple[str, ...]] = (
    "corporate_actions",
    "volume_lot",
    "price_limit_band",
    "settlement_cycle",
    "rights",
)


class MarketContractUnavailable(RuntimeError):
    """A required market contract is not verified present (fail closed)."""


@dataclass(frozen=True, slots=True)
class Capability:
    """One contract family for one market.

    Attributes:
        state: ``present`` when the repository proves the contract, else
            ``unknown``.
        evidence: ``file:line`` proof for either state.
        input_packet: The exact input needed to close an ``unknown`` contract;
            empty when the contract is present.
    """

    state: CapabilityState
    evidence: str
    input_packet: str = ""


@dataclass(frozen=True, slots=True)
class MarketContract:
    """Declared contracts plus the registry facts a market already holds."""

    market: str
    mics: frozenset[str]
    calendar_exchanges: tuple[str, ...]
    capabilities: Mapping[str, Capability]

    def capability(self, name: str) -> Capability:
        """Declared capability *name*; ``KeyError`` when the name is not declared."""
        try:
            return self.capabilities[name]
        except KeyError:
            raise KeyError(f"{self.market!r} declares no {name!r} capability; declared: {sorted(self.capabilities)}") from None


_VOLUME_LOT_UNKNOWN: Final[Capability] = Capability(
    state="unknown",
    evidence=(
        "core/schemas.py:95 lists `volume` as a plain integer column and validation/schemas.py:82 "
        '(`col_vals_ge(columns="volume", value=0)`) is the only volume assertion; no board lot, tradable unit, '
        "rounding or unit-change rule exists anywhere under src/"
    ),
    input_packet=(
        "venue trading-rule extract giving the board lot size / tradable unit per board (and any change inside the "
        "fixture window) with effective dates, issuing document id, licence and vintage"
    ),
)

_PRICE_LIMIT_UNKNOWN: Final[Capability] = Capability(
    state="unknown",
    evidence=(
        "No price-limit, band or halt rule is modelled: validation/schemas.py:61 `PriceDataSchema` asserts only "
        "positive OHLC, high>=low/open/close and non-negative volume, and core/schemas.py:95 has no band column"
    ),
    input_packet=(
        "venue rule extract for daily price limits / dynamic bands / halts with effective dates, document id, "
        "licence and vintage, plus how a limit-locked session is represented"
    ),
)

_SETTLEMENT_UNKNOWN: Final[Capability] = Capability(
    state="unknown",
    evidence=(
        "No settlement cycle is modelled: core/calendar.py:116 `instrument_session_close` yields the scheduled close "
        "only, and no settlement-date, T+N or ex-date entitlement rule exists under src/"
    ),
    input_packet=(
        "venue/CSD settlement-cycle evidence (cycle length, effective dates, document id, licence) if any consumer "
        "needs a settlement date; until then no settlement column is added"
    ),
)

_RIGHTS_UNKNOWN: Final[Capability] = Capability(
    state="unknown",
    evidence=(
        "core/schemas.py:166 defines CORPORATE_ACTION_TYPES as ('dividend', 'split') only and "
        "validation/schemas.py:261 restricts `action` to that set; core/schemas.py:154 CORPORATE_ACTION_COLUMNS has no "
        "rights, allotment, entitlement or subscription term, and none exists anywhere else under src/"
    ),
    input_packet=(
        "rights/allotment event feed with terms (ratio, subscription price, currency, entitlement cut-off), a "
        "source-issued publication clock, coverage start/end evidence and a licence"
    ),
)

_GAP_CAPABILITIES: Final[Mapping[str, Capability]] = MappingProxyType(
    {
        "volume_lot": _VOLUME_LOT_UNKNOWN,
        "price_limit_band": _PRICE_LIMIT_UNKNOWN,
        "settlement_cycle": _SETTLEMENT_UNKNOWN,
        "rights": _RIGHTS_UNKNOWN,
    }
)

_US_CORPORATE_ACTIONS: Final[Capability] = Capability(
    state="present",
    evidence=(
        "ingestion/corporate_actions.py:207 admits only `CORPORATE_ACTION_MARKETS` (the `us_equity` route), which writes "
        "bronze+silver action rows "
        "with a source-scoped coverage proof (ADR-0011, ADR-0032)"
    ),
)


def _unsupported_corporate_actions(market: str) -> Capability:
    """Declared absence for a market whose action ingestion is refused (ADR-0011 v1)."""
    return Capability(
        state="unknown",
        evidence=(
            "ingestion/corporate_actions.py:207 refuses every market outside `CORPORATE_ACTION_MARKETS` (currently `us_equity`), so "
            f"`{market}` has neither a dividend/split event table nor a source-scoped coverage proof"
        ),
        input_packet=(
            f"`{market}` corporate-action feed naming the source, its licence, per-row ex-date semantics, a "
            "source-issued publication/vintage clock (never an ingest-time substitute) and coverage start/end evidence"
        ),
    )


def _contract(market: str, corporate_actions: Capability) -> MarketContract:
    entry = PRICE_MARKETS[market]
    return MarketContract(
        market=market,
        mics=MARKET_MICS[market],
        calendar_exchanges=entry.exchanges,
        capabilities=MappingProxyType({"corporate_actions": corporate_actions, **_GAP_CAPABILITIES}),
    )


#: Declared contracts, one row per market whose feasibility pack exists. A market
#: absent from this mapping has no declaration yet and must not be assumed
#: present: :func:`market_contract` raises rather than returning a default.
MARKET_CONTRACTS: Final[Mapping[str, MarketContract]] = MappingProxyType(
    {
        "us_equity": _contract("us_equity", _US_CORPORATE_ACTIONS),
        "cn_ashare": _contract("cn_ashare", _unsupported_corporate_actions("cn_ashare")),
        "hk_sg_equity": _contract("hk_sg_equity", _unsupported_corporate_actions("hk_sg_equity")),
    }
)

#: Price markets whose corporate-action ingestion is implemented (ADR-0011 v1).
#: Derived from the declarations above so the route guard and the record cannot drift.
CORPORATE_ACTION_MARKETS: Final[frozenset[str]] = frozenset(
    market for market, contract in MARKET_CONTRACTS.items() if contract.capability("corporate_actions").state == "present"
)


def market_contract(market: str) -> MarketContract:
    """Declared contract for a price market (long key or deprecated alias)."""
    canonical = canonical_market(market)
    try:
        return MARKET_CONTRACTS[canonical]
    except KeyError:
        raise KeyError(f"No contract declared for {canonical!r}; declared: {sorted(MARKET_CONTRACTS)}") from None


def require_capability(market: str, name: str) -> Capability:
    """Return a capability that must be ``present``, else fail closed.

    Raises:
        MarketContractUnavailable: the contract is ``unknown`` — the message
            names the missing evidence and the input packet that would close it.
    """
    capability = market_contract(market).capability(name)
    if capability.state != "present":
        raise MarketContractUnavailable(
            f"{market!r} has no verified {name!r} contract. Evidence: {capability.evidence}. Required input packet: {capability.input_packet}"
        )
    return capability


__all__ = [
    "CONTRACT_CAPABILITIES",
    "CORPORATE_ACTION_MARKETS",
    "MARKET_CONTRACTS",
    "Capability",
    "CapabilityState",
    "MarketContract",
    "MarketContractUnavailable",
    "market_contract",
    "require_capability",
]
