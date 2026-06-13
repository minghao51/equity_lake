"""Alerting adapters for pipeline health notifications."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

logger = structlog.get_logger()


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
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(self.url, json=payload, headers={"Content-Type": "application/json"})
                response.raise_for_status()
            logger.info("webhook_alert_sent", url=self.url, status=response.status_code, alert_count=len(alerts))
        except Exception as exc:
            logger.warning("webhook_alert_failed", url=self.url, error=str(exc))


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
