from __future__ import annotations

from datetime import date
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Predictor(Protocol):
    def predict(self, ticker: str, date: date) -> dict[str, Any]: ...
