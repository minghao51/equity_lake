"""Pydantic configuration models."""

from pydantic import BaseModel, Field, field_validator, model_validator


class TickerMetadata(BaseModel):
    """Metadata for a single ticker."""

    symbol: str
    name: str
    exchange: str
    sector: str
    tags: list[str] = Field(default_factory=list)
    active: bool = True
    priority: int = 5

    @field_validator("symbol")
    @classmethod
    def symbol_must_not_be_empty(cls, value: str) -> str:
        """Ensure symbol is not empty."""
        if not value or not value.strip():
            raise ValueError("Ticker symbol cannot be empty")
        return value.strip()

    @field_validator("exchange")
    @classmethod
    def exchange_must_not_be_empty(cls, value: str) -> str:
        """Ensure exchange is not empty."""
        if not value or not value.strip():
            raise ValueError("Exchange cannot be empty")
        return value.strip()

    @field_validator("priority")
    @classmethod
    def priority_must_be_valid(cls, value: int) -> int:
        """Ensure priority is between 1 and 10."""
        if not 1 <= value <= 10:
            raise ValueError("Priority must be between 1 and 10")
        return value

    @field_validator("tags")
    @classmethod
    def tags_must_be_unique(cls, value: list[str]) -> list[str]:
        """Ensure tags are unique and lowercase."""
        return list({tag.lower().strip() for tag in value if tag.strip()})


class MarketConfig(BaseModel):
    """Configuration for a single market."""

    currency: str
    description: str = ""
    tickers: list[TickerMetadata] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tickers(self) -> "MarketConfig":
        """Validate ticker symbols are unique within a market."""
        symbols = [ticker.symbol for ticker in self.tickers]
        if len(symbols) != len(set(symbols)):
            duplicates = {symbol for symbol in symbols if symbols.count(symbol) > 1}
            raise ValueError(f"Duplicate ticker symbols found: {duplicates}")
        return self


class GroupConfig(BaseModel):
    """Configuration for a ticker group."""

    description: str
    markets: list[str]
    tickers: list[str] | dict[str, list[str]] = Field(default_factory=list)


class ValidationConfig(BaseModel):
    """Configuration for ticker validation rules."""

    market_formats: dict[str, str] = Field(default_factory=dict)
    required_fields: list[str] = Field(default_factory=list)
    valid_exchanges: dict[str, list[str]] = Field(default_factory=dict)
    valid_sectors: list[str] = Field(default_factory=list)
    valid_tags: list[str] = Field(default_factory=list)


class TickerConfigRoot(BaseModel):
    """Root configuration model."""

    version: str = "1.0"
    metadata: dict[str, str] = Field(default_factory=dict)
    markets: dict[str, MarketConfig] = Field(default_factory=dict)
    groups: dict[str, GroupConfig] = Field(default_factory=dict)
    validation: ValidationConfig | None = None


__all__ = [
    "GroupConfig",
    "MarketConfig",
    "TickerConfigRoot",
    "TickerMetadata",
    "ValidationConfig",
]
