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
    @patch("equity_lake.monitoring.alerting.httpx.Client")
    def test_send_alert_posts_json(self, mock_client_cls) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        alerter = WebhookAlerter(url="https://example.com/webhook")
        alerter.send_alert(["alert1", "alert2"], severity="critical")

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["json"]["severity"] == "critical"
        assert call_kwargs.kwargs["json"]["alerts"] == ["alert1", "alert2"]

    @patch("equity_lake.monitoring.alerting.httpx.Client")
    def test_send_alert_handles_failure(self, mock_client_cls) -> None:
        mock_client = MagicMock()
        mock_client.post.side_effect = Exception("connection error")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        alerter = WebhookAlerter(url="https://example.com/webhook")
        alerter.send_alert(["alert"], severity="info")


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
