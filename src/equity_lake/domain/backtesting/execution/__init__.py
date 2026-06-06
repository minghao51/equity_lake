"""
Execution layer for backtesting.

This package provides realistic order execution simulation with
transaction costs, slippage, and portfolio management.
"""

from equity_lake.domain.backtesting.execution.broker import (
    Broker,
    Execution,
    Order,
    OrderSide,
    OrderType,
)
from equity_lake.domain.backtesting.execution.costs import (
    CommissionModel,
    FixedPerShareCommission,
    FixedSlippage,
    MarketSpecificCommission,
    PercentageCommission,
    SlippageModel,
    TieredCommission,
    TransactionCost,
    get_default_cost_model,
)
from equity_lake.domain.backtesting.execution.portfolio import (
    Portfolio,
    PortfolioSnapshot,
    Position,
)

__all__ = [
    # Broker
    "Broker",
    "Execution",
    "Order",
    "OrderSide",
    "OrderType",
    # Costs
    "CommissionModel",
    "FixedPerShareCommission",
    "FixedSlippage",
    "MarketSpecificCommission",
    "PercentageCommission",
    "SlippageModel",
    "TieredCommission",
    "TransactionCost",
    "get_default_cost_model",
    # Portfolio
    "Portfolio",
    "PortfolioSnapshot",
    "Position",
]
