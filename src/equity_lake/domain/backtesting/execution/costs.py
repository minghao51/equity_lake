"""
Transaction cost models for backtesting.

This module provides realistic transaction cost models for different markets,
including commissions, slippage, and market impact.
"""

from abc import ABC, abstractmethod

import structlog

logger = structlog.get_logger(__name__)


class CommissionModel(ABC):
    """Abstract base class for commission models."""

    @abstractmethod
    def calculate(self, price: float, shares: float) -> float:
        """
        Calculate commission for a trade.

        Args:
            price: Execution price per share
            shares: Number of shares

        Returns:
            Commission amount
        """
        pass


class FixedPerShareCommission(CommissionModel):
    """
    Fixed commission per share traded.

    Parameters:
        commission_per_share: Commission per share (default: 0.005 = $0.005/share)

    Example:
        >>> model = FixedPerShareCommission(commission_per_share=0.005)
        >>> commission = model.calculate(price=100.0, shares=100)
        >>> print(commission)  # 0.005 * 100 = 0.50
    """

    def __init__(self, commission_per_share: float = 0.005):
        self.commission_per_share = commission_per_share

    def calculate(self, price: float, shares: float) -> float:
        """Calculate fixed per-share commission."""
        return abs(shares) * self.commission_per_share


class PercentageCommission(CommissionModel):
    """
    Percentage-based commission on trade value.

    Parameters:
        commission_rate: Commission rate as percentage (default: 0.001 = 0.1%)
        min_commission: Minimum commission per trade (default: 1.0)

    Example:
        >>> model = PercentageCommission(commission_rate=0.001)
        >>> commission = model.calculate(price=100.0, shares=100)
        >>> print(commission)  # 0.001 * 100 * 100 = 10.0
    """

    def __init__(self, commission_rate: float = 0.001, min_commission: float = 1.0):
        self.commission_rate = commission_rate
        self.min_commission = min_commission

    def calculate(self, price: float, shares: float) -> float:
        """Calculate percentage commission."""
        trade_value = abs(price * shares)
        commission = trade_value * self.commission_rate
        return max(commission, self.min_commission)


class TieredCommission(CommissionModel):
    """
    Tiered commission based on trade volume.

    Parameters:
        tiers: List of (volume_threshold, commission_rate) tuples
        Example: [(0, 0.001), (10000, 0.0008), (50000, 0.0005)]
            means: 0.1% for first $10k, 0.08% for $10k-$50k, 0.05% for >$50k

    Example:
        >>> tiers = [(0, 0.001), (10000, 0.0008), (50000, 0.0005)]
        >>> model = TieredCommission(tiers=tiers)
        >>> commission = model.calculate(price=100.0, shares=1000)
        >>> # Trade value = $100,000, so rate = 0.0005
        >>> # Commission = $100,000 * 0.0005 = $50
    """

    def __init__(self, tiers: list[tuple[float, float]]):
        # Sort tiers by threshold
        self.tiers = sorted(tiers, key=lambda x: x[0])

    def calculate(self, price: float, shares: float) -> float:
        """Calculate tiered commission."""
        trade_value = abs(price * shares)

        # Find applicable tier
        applicable_rate = self.tiers[0][1]  # Default to first tier
        for threshold, rate in self.tiers:
            if trade_value >= threshold:
                applicable_rate = rate

        return trade_value * applicable_rate


class MarketSpecificCommission(CommissionModel):
    """
    Market-specific commission model.

    Provides realistic commission structures for different markets:
    - US: Per-share commission
    - CN: Stamp duty (sell only) + commission
    - HK/SG: Percentage commission

    Parameters:
        market: Market label ('us', 'cn', 'hk_sg')

    Example:
        >>> us_model = MarketSpecificCommission(market='us')
        >>> cn_model = MarketSpecificCommission(market='cn')
    """

    # Market-specific cost structures
    COST_STRUCTURES: dict[str, dict[str, float | str]] = {
        "us": {
            "type": "per_share",
            "commission_per_share": 0.005,
        },
        "cn": {
            "type": "percentage",
            "commission_rate": 0.0003,  # 0.03% commission
            "stamp_duty_rate": 0.001,  # 0.1% stamp duty (sell only)
        },
        "hk_sg": {
            "type": "percentage",
            "commission_rate": 0.001,  # 0.1% commission
        },
    }

    def __init__(self, market: str):
        """
        Initialize market-specific commission model.

        Args:
            market: Market label ('us', 'cn', 'hk_sg')

        Raises:
            ValueError: If market is not supported
        """
        if market not in self.COST_STRUCTURES:
            raise ValueError(f"Unsupported market: {market}. Supported markets: {list(self.COST_STRUCTURES.keys())}")

        self.market = market
        config = self.COST_STRUCTURES[market]
        self.model: CommissionModel

        if config["type"] == "per_share":
            self.model = FixedPerShareCommission(float(config["commission_per_share"]))
        elif config["type"] == "percentage":
            self.model = PercentageCommission(float(config["commission_rate"]))
        else:
            raise ValueError(f"Unknown commission type: {config['type']}")

        # Store additional costs (e.g., stamp duty)
        self.stamp_duty_rate: float = float(config.get("stamp_duty_rate", 0))

    def calculate(self, price: float, shares: float) -> float:
        """Calculate total commission including additional costs."""
        commission = self.model.calculate(price, shares)

        # Add stamp duty if applicable (CN market, sell only)
        if self.stamp_duty_rate > 0 and shares < 0:  # Selling
            stamp_duty = abs(price * shares) * self.stamp_duty_rate
            commission += stamp_duty

        return commission


class SlippageModel(ABC):
    """Abstract base class for slippage models."""

    @abstractmethod
    def calculate(self, price: float, shares: float, volume: float | None = None) -> float:
        """
        Calculate slippage for a trade.

        Args:
            price: Intended execution price
            shares: Number of shares to trade
            volume: Available volume (for market impact calculation)

        Returns:
            Adjusted price (including slippage)
        """
        pass


class FixedSlippage(SlippageModel):
    """
    Fixed percentage slippage.

    Parameters:
        slippage_rate: Slippage rate (default: 0.0001 = 0.01%)
            Positive value = worse price for trader

    Example:
        >>> model = FixedSlippage(slippage_rate=0.0001)
        >>> adjusted_price = model.calculate(price=100.0, shares=100)
        >>> # For buy: 100.0 * 1.0001 = 100.01
        >>> # For sell: 100.0 * 0.9999 = 99.99
    """

    def __init__(self, slippage_rate: float = 0.0001):
        self.slippage_rate = slippage_rate

    def calculate(self, price: float, shares: float, volume: float | None = None) -> float:
        """Calculate fixed slippage."""
        if shares >= 0:  # Buying
            return price * (1 + self.slippage_rate)
        else:  # Selling
            return price * (1 - self.slippage_rate)


class VolumeShareSlippage(SlippageModel):
    """
    Volume-based slippage (market impact).

    Slippage increases with trade size relative to available volume.

    Parameters:
        base_slippage: Base slippage rate (default: 0.0001)
        impact_factor: Market impact coefficient (default: 0.1)

    Example:
        >>> model = VolumeShareSlippage()
        >>> adjusted_price = model.calculate(
        ...     price=100.0,
        ...     shares=1000,
        ...     volume=100000  # 1% of volume
        ... )
    """

    def __init__(self, base_slippage: float = 0.0001, impact_factor: float = 0.1):
        self.base_slippage = base_slippage
        self.impact_factor = impact_factor

    def calculate(self, price: float, shares: float, volume: float | None = None) -> float:
        """Calculate volume-based slippage."""
        # Base slippage
        total_slippage = self.base_slippage

        # Add market impact if volume provided
        if volume is not None and volume > 0:
            volume_share = abs(shares) / volume
            impact = self.impact_factor * (volume_share**2)
            total_slippage += impact

        # Apply slippage
        if shares >= 0:  # Buying
            return price * (1 + total_slippage)
        else:  # Selling
            return price * (1 - total_slippage)


class TransactionCost:
    """
    Combined transaction cost model including commission and slippage.

    Parameters:
        commission_model: Commission model instance
        slippage_model: Slippage model instance

    Example:
        >>> commission = PercentageCommission(commission_rate=0.001)
        >>> slippage = FixedSlippage(slippage_rate=0.0001)
        >>> cost_model = TransactionCost(commission, slippage)
        >>>
        >>> total_cost = cost_model.calculate_total(
        ...     price=100.0,
        ...     shares=100
        ... )
    """

    def __init__(
        self,
        commission_model: CommissionModel,
        slippage_model: SlippageModel,
    ):
        self.commission_model = commission_model
        self.slippage_model = slippage_model

    def calculate_total(
        self,
        price: float,
        shares: float,
        volume: float | None = None,
    ) -> dict[str, float]:
        """
        Calculate total transaction cost breakdown.

        Args:
            price: Execution price
            shares: Number of shares (positive for buy, negative for sell)
            volume: Available volume (for market impact)

        Returns:
            Dictionary with cost breakdown:
            - commission: Commission amount
            - slippage: Slippage amount (price difference)
            - total: Total cost (commission + slippage)
        """
        # Calculate commission
        commission = self.commission_model.calculate(price, shares)

        # Calculate adjusted price with slippage
        adjusted_price = self.slippage_model.calculate(price, shares, volume)

        # Calculate slippage cost
        slippage_cost = abs(adjusted_price - price) * abs(shares)

        # Total cost
        total_cost = commission + slippage_cost

        return {
            "commission": commission,
            "slippage": slippage_cost,
            "adjusted_price": adjusted_price,
            "total": total_cost,
        }


def get_default_cost_model(market: str = "us") -> TransactionCost:
    """
    Get default transaction cost model for a market.

    Args:
        market: Market label ('us', 'cn', 'hk_sg')

    Returns:
        TransactionCost instance with market-default parameters

    Example:
        >>> us_costs = get_default_cost_model("us")
        >>> cn_costs = get_default_cost_model("cn")
    """
    commission = MarketSpecificCommission(market=market)
    slippage = FixedSlippage(slippage_rate=0.0001)  # 0.01% slippage

    return TransactionCost(commission, slippage)


__all__ = [
    "CommissionModel",
    "FixedPerShareCommission",
    "PercentageCommission",
    "TieredCommission",
    "MarketSpecificCommission",
    "SlippageModel",
    "FixedSlippage",
    "VolumeShareSlippage",
    "TransactionCost",
    "get_default_cost_model",
]
