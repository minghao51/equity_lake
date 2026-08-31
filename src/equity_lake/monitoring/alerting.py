"""Alerting adapters for pipeline health notifications."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

from equity_lake.core.retry import build_retry_decorator

logger = structlog.get_logger()


@build_retry_decorator(attempts=3, wait_multiplier=0.5, wait_min=0.25, wait_max=5.0, log=logger)
def _deliver_webhook(url: str, payload: dict[str, Any], timeout: float) -> httpx.Response:
    """POST one alert payload; delivery is retried by tenacity (3 attempts, exponential backoff)."""
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload, headers={"Content-Type": "application/json"})
        response.raise_for_status()
        return response


@runtime_checkable
class Alerter(Protocol):
    def send_alert(self, alerts: list[str], *, severity: str, metrics: dict[str, Any] | None = None) -> None: ...


class ConsoleAlerter:
    def send_alert(self, alerts: list[str], *, severity: str, metrics: dict[str, Any] | None = None) -> None:
        for alert in alerts:
            print(f"[{severity.upper()}] {alert}")


class WebhookAlerter:
    """POST alerts as JSON to a configurable webhook URL."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        self.url = url
        self.timeout = timeout

    def send_alert(self, alerts: list[str], *, severity: str, metrics: dict[str, Any] | None = None) -> None:
        payload = {
            "severity": severity,
            "alerts": alerts,
            "metrics": metrics or {},
        }
        try:
            response = _deliver_webhook(self.url, payload, self.timeout)
            logger.info("webhook_alert_sent", url=self.url, status=response.status_code, alert_count=len(alerts))
        except Exception as exc:
            # Delivery already retried by tenacity; log the outcome loudly so a
            # lost alert is visible instead of silently swallowed.
            logger.error("webhook_alert_delivery_failed", url=self.url, error=str(exc), alert_count=len(alerts))


class CompositeAlerter:
    """Dispatch alerts to multiple alerter instances."""

    def __init__(self, alerters: list[Alerter]) -> None:
        self.alerters = alerters

    def send_alert(self, alerts: list[str], *, severity: str, metrics: dict[str, Any] | None = None) -> None:
        for alerter in self.alerters:
            try:
                alerter.send_alert(alerts, severity=severity, metrics=metrics)
            except Exception as exc:
                logger.warning("alerter_dispatch_failed", alerter=type(alerter).__name__, error=str(exc))


def build_alerter(webhook_url: str | None = None) -> Alerter:
    alerters: list[Alerter] = [ConsoleAlerter()]
    if webhook_url:
        alerters.append(WebhookAlerter(webhook_url))
    return CompositeAlerter(alerters)
