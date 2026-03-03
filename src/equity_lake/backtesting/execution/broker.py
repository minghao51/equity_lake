"""
Broker simulation for backtesting.

This module simulates order execution with realistic transaction costs,
slippage, and market impact.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import structlog

from equity_lake.backtesting.execution.costs import (
    TransactionCost,
    get_default_cost_model,
)

logger = structlog.get_logger(__name__)


class OrderType(Enum):
    """Order types supported by the broker."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderSide(Enum):
    """Order sides."""

    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Order:
    """
    Order representation.

    Attributes:
        ticker: Stock symbol
        side: Buy or sell
        order_type: Order type
        quantity: Number of shares
        price: Limit price (for LIMIT orders)
        stop_price: Stop price (for STOP orders)
        order_id: Unique order identifier
        created_at: Order creation timestamp
    """

    ticker: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None
    stop_price: float | None = None
    order_id: str | None = None
    created_at: datetime | None = None


@dataclass
class Execution:
    """
    Trade execution result.

    Attributes:
        order_id: Order ID
        ticker: Stock symbol
        side: Buy or sell
        quantity: Number of shares executed
        price: Execution price (after slippage)
        commission: Commission paid
        slippage: Slippage cost
        total_cost: Total transaction cost
        executed_at: Execution timestamp
    """

    order_id: str
    ticker: str
    side: OrderSide
    quantity: float
    price: float
    commission: float
    slippage: float
    total_cost: float
    executed_at: datetime


class Broker:
    """
    Order execution simulator.

    Simulates realistic order execution with:
    - Transaction costs (commission, slippage)
    - Partial fills
    - Market impact
    - Different order types

    Attributes:
        cash: Available cash
        positions: Current positions (ticker -> shares)
        cost_model: Transaction cost model
        pending_orders: Orders awaiting execution
        execution_history: History of all executions

    Example:
        >>> broker = Broker(initial_cash=100_000, market="us")
        >>>
        >>> # Place market order
        >>> order = Order(
        ...     ticker="AAPL",
        ...     side=OrderSide.BUY,
        ...     order_type=OrderType.MARKET,
        ...     quantity=100
        ... )
        >>>
        >>> execution = broker.execute_order(order, price=150.0)
        >>> print(f"Executed: {execution.quantity} shares at ${execution.price}")
    """

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        market: str = "us",
        cost_model: TransactionCost | None = None,
    ):
        """
        Initialize broker.

        Args:
            initial_cash: Starting cash balance
            market: Market label for default cost model
            cost_model: Custom cost model (if None, uses market default)
        """
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: dict[str, float] = {}
        self.cost_model = cost_model or get_default_cost_model(market)
        self.pending_orders: list[Order] = []
        self.execution_history: list[Execution] = []

        logger.info(
            "Broker initialized",
            initial_cash=initial_cash,
            market=market,
        )

    def execute_order(
        self,
        order: Order,
        price: float,
        volume: float | None = None,
        allow_partial_fill: bool = False,
    ) -> Execution:
        """
        Execute an order.

        Args:
            order: Order to execute
            price: Current market price
            volume: Available volume (for market impact calculation)
            allow_partial_fill: Allow partial fills (default: False)

        Returns:
            Execution object with execution details

        Raises:
            ValueError: If insufficient funds or shares
        """
        # Generate order ID if not provided
        if order.order_id is None:
            order.order_id = (
                f"{order.ticker}_{order.side.value}_{datetime.now().timestamp()}"
            )

        logger.debug(
            "Executing order",
            order_id=order.order_id,
            ticker=order.ticker,
            side=order.side.value,
            quantity=order.quantity,
            market_price=price,
        )

        # Calculate transaction costs
        cost_breakdown = self.cost_model.calculate_total(
            price=price,
            shares=order.quantity,
            volume=volume,
        )

        adjusted_price = cost_breakdown["adjusted_price"]
        commission = cost_breakdown["commission"]
        slippage = cost_breakdown["slippage"]
        total_cost = cost_breakdown["total"]

        # Validate order
        if order.side == OrderSide.BUY:
            # Check sufficient cash
            required_cash = (adjusted_price * order.quantity) + commission

            if not allow_partial_fill:
                if required_cash > self.cash:
                    # Calculate max shares we can buy
                    available_for_shares = self.cash - commission
                    max_shares = int(available_for_shares / adjusted_price)

                    if max_shares <= 0:
                        raise ValueError(
                            f"Insufficient funds: need ${required_cash:.2f}, "
                            f"have ${self.cash:.2f}"
                        )

                    logger.warning(
                        "Order size reduced due to insufficient funds",
                        original_quantity=order.quantity,
                        adjusted_quantity=max_shares,
                    )
                    order.quantity = max_shares
                    required_cash = (adjusted_price * order.quantity) + commission
            else:
                if required_cash > self.cash:
                    # Partial fill
                    available_for_shares = self.cash - commission
                    max_shares = int(available_for_shares / adjusted_price)
                    order.quantity = max(0, max_shares)
                    required_cash = (adjusted_price * order.quantity) + commission

            # Update cash and positions
            self.cash -= required_cash
            self.positions[order.ticker] = (
                self.positions.get(order.ticker, 0) + order.quantity
            )

        else:  # SELL
            # Check sufficient shares
            current_position = self.positions.get(order.ticker, 0)

            if abs(order.quantity) > current_position:
                raise ValueError(
                    f"Insufficient shares: trying to sell {abs(order.quantity)}, "
                    f"have {current_position}"
                )

            # Update cash and positions
            proceeds = (adjusted_price * abs(order.quantity)) - commission
            self.cash += proceeds
            self.positions[order.ticker] = current_position - abs(order.quantity)

        # Create execution record
        execution = Execution(
            order_id=order.order_id,
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            price=adjusted_price,
            commission=commission,
            slippage=slippage,
            total_cost=total_cost,
            executed_at=datetime.now(),
        )

        self.execution_history.append(execution)

        logger.info(
            "Order executed",
            order_id=order.order_id,
            ticker=order.ticker,
            quantity=order.quantity,
            price=adjusted_price,
            commission=commission,
            cash_remaining=self.cash,
        )

        return execution

    def get_position(self, ticker: str) -> float:
        """
        Get current position in a ticker.

        Args:
            ticker: Stock symbol

        Returns:
            Number of shares held (can be negative for short positions)
        """
        return self.positions.get(ticker, 0)

    def get_portfolio_value(
        self,
        prices: dict[str, float],
    ) -> float:
        """
        Calculate total portfolio value.

        Args:
            prices: Current prices for all held positions

        Returns:
            Total portfolio value (cash + positions)
        """
        total_value = self.cash

        for ticker, shares in self.positions.items():
            if ticker in prices and shares != 0:
                total_value += shares * prices[ticker]

        return total_value

    def get_open_orders(self) -> list[Order]:
        """Get list of pending orders."""
        return self.pending_orders.copy()

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a pending order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if order was cancelled, False if not found
        """
        for i, order in enumerate(self.pending_orders):
            if order.order_id == order_id:
                self.pending_orders.pop(i)
                logger.info("Order cancelled", order_id=order_id)
                return True

        logger.warning("Order not found for cancellation", order_id=order_id)
        return False

    def reset(self):
        """Reset broker to initial state."""
        self.cash = self.initial_cash
        self.positions.clear()
        self.pending_orders.clear()
        self.execution_history.clear()
        logger.info("Broker reset")

    def get_summary(self) -> dict[str, object]:
        """
        Get broker summary.

        Returns:
            Dictionary with broker state summary
        """
        return {
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "positions": dict(self.positions),
            "num_executions": len(self.execution_history),
            "total_commission": sum(e.commission for e in self.execution_history),
            "total_slippage": sum(e.slippage for e in self.execution_history),
        }


__all__ = [
    "OrderType",
    "OrderSide",
    "Order",
    "Execution",
    "Broker",
]
