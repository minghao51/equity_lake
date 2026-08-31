"""Tests for monitoring.alerting adapters."""

from unittest.mock import MagicMock, patch

from equity_lake.monitoring.alerting import (
    CompositeAlerter,
    ConsoleAlerter,
    WebhookAlerter,
    build_alerter,
)


class TestConsoleAlerter:
    def test_send_alert_prints(self, capsys) -> None:
        alerter = ConsoleAlerter()
        alerter.send_alert(["test alert"], severity="warning")
        captured = capsys.readouterr()
        assert "[WARNING] test alert" in captured.out


class TestWebhookAlerter:
    def test_send_alert_posts_json(self, mock_httpx_client) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        with patch("equity_lake.monitoring.alerting.httpx.Client", return_value=mock_httpx_client):
            alerter = WebhookAlerter(url="https://example.com/webhook")
            alerter.send_alert(["alert1", "alert2"], severity="critical")

        mock_httpx_client.post.assert_called_once()
        call_kwargs = mock_httpx_client.post.call_args
        assert call_kwargs.kwargs["json"]["severity"] == "critical"
        assert call_kwargs.kwargs["json"]["alerts"] == ["alert1", "alert2"]

    def test_send_alert_retries_failed_delivery(self, mock_httpx_client) -> None:
        """Delivery goes through core/retry.py tenacity: 3 attempts, then a loud error log."""
        mock_httpx_client.post.side_effect = Exception("connection error")

        with (
            patch("equity_lake.monitoring.alerting.httpx.Client", return_value=mock_httpx_client),
            patch("equity_lake.monitoring.alerting.logger") as mock_logger,
        ):
            alerter = WebhookAlerter(url="https://example.com/webhook")
            alerter.send_alert(["alert"], severity="info")  # must not raise

        assert mock_httpx_client.post.call_count == 3  # tenacity retries, then gives up
        mock_logger.error.assert_called_once()
        assert mock_logger.error.call_args.args[0] == "webhook_alert_delivery_failed"

    def test_send_alert_recovers_on_retry(self, mock_httpx_client) -> None:
        """A transient failure on attempt 1 is retried and delivers successfully."""
        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.raise_for_status = MagicMock()
        mock_httpx_client.post.side_effect = [Exception("transient"), ok_response]

        with (
            patch("equity_lake.monitoring.alerting.httpx.Client", return_value=mock_httpx_client),
            patch("equity_lake.monitoring.alerting.logger") as mock_logger,
        ):
            alerter = WebhookAlerter(url="https://example.com/webhook")
            alerter.send_alert(["alert"], severity="warning")

        assert mock_httpx_client.post.call_count == 2
        mock_logger.info.assert_called_once()
        assert mock_logger.info.call_args.args[0] == "webhook_alert_sent"


class TestCompositeAlerter:
    def test_dispatches_to_all_alerters(self) -> None:
        mock1 = MagicMock()
        mock2 = MagicMock()
        composite = CompositeAlerter([mock1, mock2])

        composite.send_alert(["test"], severity="info")

        mock1.send_alert.assert_called_once_with(["test"], severity="info", metrics=None)
        mock2.send_alert.assert_called_once_with(["test"], severity="info", metrics=None)

    def test_continues_on_alerter_failure(self) -> None:
        mock1 = MagicMock()
        mock1.send_alert.side_effect = Exception("fail")
        mock2 = MagicMock()
        composite = CompositeAlerter([mock1, mock2])

        composite.send_alert(["test"], severity="info")

        mock2.send_alert.assert_called_once()


class TestBuildAlerter:
    def test_default_is_composite_with_console(self) -> None:
        alerter = build_alerter()
        assert isinstance(alerter, CompositeAlerter)

    def test_with_webhook_adds_webhook_alerter(self) -> None:
        alerter = build_alerter(webhook_url="https://example.com/hook")
        assert isinstance(alerter, CompositeAlerter)
        assert len(alerter.alerters) == 2
        assert isinstance(alerter.alerters[0], ConsoleAlerter)
        assert isinstance(alerter.alerters[1], WebhookAlerter)
