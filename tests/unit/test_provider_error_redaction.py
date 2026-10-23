"""Finnhub fetch failures must not serialize provider exception messages."""

import importlib
from datetime import date
from threading import Lock
from types import ModuleType
from typing import Any
from unittest.mock import patch

import httpx
import pytest

fetch_items_parallel = importlib.import_module("equity_lake.ingestion.parallel").fetch_items_parallel
analyst_source = importlib.import_module("equity_lake.sources.analyst_ratings")
news_source = importlib.import_module("equity_lake.sources.news")
sentiment_source = importlib.import_module("equity_lake.sources.sentiment")
AnalystRatingFetcher = analyst_source.AnalystRatingFetcher
FinnhubNewsFetcher = news_source.FinnhubNewsFetcher
FinnhubSocialSentimentFetcher = sentiment_source.FinnhubSocialSentimentFetcher


@pytest.mark.parametrize(
    ("source_module", "fetcher_type", "fetch_method"),
    [
        (news_source, FinnhubNewsFetcher, "_fetch_news_for_ticker"),
        (sentiment_source, FinnhubSocialSentimentFetcher, "_fetch_sentiment_for_ticker"),
    ],
)
def test_finnhub_request_error_logs_only_safe_diagnostics(
    source_module: ModuleType,
    fetcher_type: type[Any],
    fetch_method: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "synthetic-finnhub-token-unique"

    def fail_request(*args: Any, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", f"https://finnhub.example.test/endpoint?token={secret}")
        return httpx.Response(503, request=request)

    monkeypatch.setattr(source_module.httpx, "get", fail_request)
    with (
        patch("equity_lake.sources.base.throttle"),
        patch.object(source_module, "logger") as logger,
    ):
        fetcher = fetcher_type(api_key=secret, tickers=["AAPL"], retry_attempts=1, retry_delay=0)
        result = getattr(fetcher, fetch_method)("AAPL", date(2024, 1, 2))

    assert result == []
    logger.error.assert_called_once()
    log_call = logger.error.call_args
    assert log_call.kwargs["error_type"] == "TransientError"
    assert log_call.kwargs["status_code"] == 503
    assert secret not in repr(log_call)


def test_finnhub_json_error_is_redacted_through_parallel_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "synthetic-finnhub-json-token-unique"
    endpoint = f"{news_source.FINNHUB_BASE_URL}/company-news"
    request_urls: list[str] = []

    class JsonErrorResponse(httpx.Response):
        def json(self, **kwargs: Any) -> Any:
            raise ValueError(f"synthetic JSON parse failure at {self.request.url}")

    def fail_request(url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", url, params=kwargs["params"])
        request_urls.append(str(request.url))
        return JsonErrorResponse(200, request=request)

    monkeypatch.setattr(news_source.httpx, "get", fail_request)
    with (
        patch("equity_lake.sources.base.throttle"),
        patch.object(news_source, "logger") as news_logger,
        patch("equity_lake.ingestion.parallel.logger") as parallel_logger,
    ):
        fetcher = FinnhubNewsFetcher(api_key=secret, tickers=["AAPL", "MSFT"], retry_attempts=1, retry_delay=0, max_workers=2)
        result = fetcher.fetch(date(2024, 1, 2))

    assert result.is_empty()
    assert len(request_urls) == 2
    assert all(secret in request_url and endpoint in request_url for request_url in request_urls)

    captured_logs = repr((news_logger.mock_calls, parallel_logger.mock_calls))
    assert secret not in captured_logs
    assert endpoint not in captured_logs
    assert news_logger.error.call_count == 2
    for log_call in news_logger.error.call_args_list:
        assert log_call.args[0] == "finnhub_news_request_failed"
        assert log_call.kwargs["error_type"] == "ValueError"
        assert log_call.kwargs["status_code"] == 200
    parallel_logger.error.assert_not_called()


@pytest.mark.parametrize(
    ("failure", "expected_error_type", "expected_status_code"),
    [("status", "HTTPStatusError", 503), ("json", "ValueError", 200)],
)
def test_analyst_rating_fetch_redacts_request_errors_through_parallel_seam(
    failure: str,
    expected_error_type: str,
    expected_status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "synthetic-analyst-token-unique"
    request_urls: list[str] = []

    class JsonErrorResponse(httpx.Response):
        def json(self, **kwargs: Any) -> Any:
            raise ValueError(f"synthetic JSON parse failure at {self.request.url}")

    class MockClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        def __enter__(self) -> Any:
            return self

        def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
            return None

        def get(self, url: str, params: dict[str, str]) -> httpx.Response:
            request = httpx.Request("GET", url, params=params)
            request_urls.append(str(request.url))
            response_type = JsonErrorResponse if failure == "json" else httpx.Response
            return response_type(200 if failure == "json" else 503, request=request)

    monkeypatch.setattr(analyst_source.httpx, "Client", MockClient)

    def fetch_request_failure_for_ticker(ticker: str, trading_date: date) -> Any:
        fetcher = AnalystRatingFetcher(api_key=secret, tickers=[ticker], retry_attempts=1, retry_delay=0)
        return fetcher.fetch(trading_date).to_dicts()

    with (
        patch("equity_lake.sources.base.throttle"),
        patch.object(analyst_source, "logger") as analyst_logger,
        patch("equity_lake.ingestion.parallel.logger") as parallel_logger,
        patch("equity_lake.sources.base.logger") as base_logger,
    ):
        result = fetch_items_parallel(
            ["AAPL", "MSFT"],
            fetch_request_failure_for_ticker,
            date(2024, 1, 2),
            max_workers=2,
        )

    assert result == []
    assert len(request_urls) == 2
    assert all(secret in request_url and "/stock/recommendation" in request_url for request_url in request_urls)

    captured_logs = repr((analyst_logger.mock_calls, parallel_logger.mock_calls, base_logger.mock_calls))
    assert secret not in captured_logs
    assert analyst_source.FINNHUB_BASE_URL not in captured_logs
    assert f"/stock/recommendation?symbol=AAPL&token={secret}" not in captured_logs
    assert f"/stock/recommendation?symbol=MSFT&token={secret}" not in captured_logs
    assert analyst_logger.error.call_count == 2
    for log_call in analyst_logger.error.call_args_list:
        assert log_call.args[0] == "analyst_rating_request_failed"
        assert log_call.kwargs["error_type"] == expected_error_type
        assert log_call.kwargs["status_code"] == expected_status_code
    parallel_logger.error.assert_not_called()


def test_analyst_rating_shape_error_logs_safe_response_type_via_parallel_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "synthetic-analyst-shape-token-unique"
    request_urls: list[str] = []

    class MockClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        def __enter__(self) -> Any:
            return self

        def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
            return None

        def get(self, url: str, params: dict[str, str]) -> httpx.Response:
            request = httpx.Request("GET", url, params=params)
            request_urls.append(str(request.url))
            return httpx.Response(200, request=request, json={"unexpected": "shape"})

    monkeypatch.setattr(analyst_source.httpx, "Client", MockClient)

    def fetch_for_ticker(ticker: str, trading_date: date) -> Any:
        fetcher = AnalystRatingFetcher(api_key=secret, tickers=[ticker], retry_attempts=1, retry_delay=0)
        return fetcher.fetch(trading_date).to_dicts()

    with (
        patch("equity_lake.sources.base.throttle"),
        patch.object(analyst_source, "logger") as analyst_logger,
        patch("equity_lake.ingestion.parallel.logger") as parallel_logger,
    ):
        result = fetch_items_parallel(["AAPL", "MSFT"], fetch_for_ticker, date(2024, 1, 2), max_workers=2)

    assert result == []
    assert len(request_urls) == 4 and all(secret in url for url in request_urls)
    captured_logs = repr((analyst_logger.mock_calls, parallel_logger.mock_calls))
    assert secret not in captured_logs
    assert analyst_source.FINNHUB_BASE_URL not in captured_logs
    warning_events = [call.args[0] for call in analyst_logger.warning.call_args_list]
    assert warning_events.count("analyst_rating_invalid_recommendation_response") == 2
    assert warning_events.count("No analyst ratings fetched") == 2
    parallel_logger.error.assert_not_called()


def test_analyst_rating_normalization_error_is_sanitized_for_parallel_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "synthetic-analyst-normalize-token-unique"
    unsafe_message = f"failed at https://finnhub.io/api/v1?token={secret}"

    def fail_normalization(*args: Any, **kwargs: Any) -> Any:
        raise ValueError(unsafe_message)

    monkeypatch.setattr(analyst_source, "ensure_columns", fail_normalization)
    monkeypatch.setattr(AnalystRatingFetcher, "_get_recommendation", lambda fetcher, ticker: [{"strongBuy": 1}])
    monkeypatch.setattr(AnalystRatingFetcher, "_get_price_target", lambda fetcher, ticker: {})

    def fail_fetch(ticker: str, trading_date: date) -> Any:
        fetcher = AnalystRatingFetcher(api_key=secret, tickers=[ticker], retry_attempts=1, retry_delay=0)
        return fetcher.fetch(trading_date).to_dicts()

    with (
        patch("equity_lake.sources.base.throttle"),
        patch.object(analyst_source, "logger") as analyst_logger,
        patch("equity_lake.ingestion.parallel.logger") as parallel_logger,
    ):
        result = fetch_items_parallel(["AAPL", "MSFT"], fail_fetch, date(2024, 1, 2), max_workers=2)

    assert result == []
    captured_logs = repr((analyst_logger.mock_calls, parallel_logger.mock_calls))
    assert secret not in captured_logs
    assert "https://finnhub.io/api/v1" not in captured_logs
    assert analyst_logger.error.call_count == 2
    for log_call in analyst_logger.error.call_args_list:
        assert log_call.args[0] == "analyst_ratings_normalization_failed"
        assert log_call.kwargs["error_type"] == "ValueError"
        assert log_call.kwargs["status_code"] is None
    assert parallel_logger.error.call_count == 2
    for log_call in parallel_logger.error.call_args_list:
        assert log_call.kwargs["error"] == "Analyst ratings normalization failed"


@pytest.mark.parametrize(
    ("failure_kind", "expected_status_code"),
    [("connect", None), ("read_timeout", None), ("503", 503), ("408", 408), ("429", 429)],
)
def test_analyst_transient_request_failures_retry_and_decode_success(
    failure_kind: str,
    expected_status_code: int | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "synthetic-analyst-retry-token-unique"
    counts = {ticker: {"recommendation": 0, "price-target": 0} for ticker in ("AAPL", "MSFT")}
    request_urls: list[str] = []
    state_lock = Lock()

    class MockClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        def __enter__(self) -> Any:
            return self

        def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
            return None

        def get(self, url: str, params: dict[str, str]) -> httpx.Response:
            request = httpx.Request("GET", url, params=params)
            ticker = params["symbol"]
            endpoint = url.rsplit("/", maxsplit=1)[-1]
            with state_lock:
                counts[ticker][endpoint] += 1
                attempt = counts[ticker][endpoint]
                request_urls.append(str(request.url))

            if endpoint == "recommendation" and attempt <= 2:
                unsafe_message = f"synthetic provider failure token={secret} at {request.url}"
                if failure_kind == "connect":
                    raise httpx.ConnectError(unsafe_message, request=request)
                if failure_kind == "read_timeout":
                    raise httpx.ReadTimeout(unsafe_message, request=request)
                return httpx.Response(int(failure_kind), request=request)

            if endpoint == "recommendation":
                return httpx.Response(
                    200,
                    request=request,
                    json=[{"period": "2024-01", "strongBuy": 2, "buy": 1, "hold": 1, "sell": 0, "strongSell": 0}],
                )
            return httpx.Response(200, request=request, json={"targetMean": 123.45, "targetMedian": 120.0, "numberOfAnalysts": 4})

    monkeypatch.setattr(analyst_source.httpx, "Client", MockClient)

    def fetch_for_ticker(ticker: str, trading_date: date) -> Any:
        fetcher = AnalystRatingFetcher(api_key=secret, tickers=[ticker], retry_attempts=3, retry_delay=0)
        return fetcher.fetch(trading_date).to_dicts()

    with (
        patch("equity_lake.sources.base.throttle"),
        patch.object(analyst_source, "logger") as analyst_logger,
        patch("equity_lake.sources.base.logger") as base_logger,
        patch("equity_lake.ingestion.parallel.logger") as parallel_logger,
    ):
        result = fetch_items_parallel(["AAPL", "MSFT"], fetch_for_ticker, date(2024, 1, 2), max_workers=2)

    assert len(result) == 2
    assert {row["ticker"] for row in result} == {"AAPL", "MSFT"}
    assert all(row["consensus_label"] == "strong_buy" and row["consensus_score"] == 1.25 for row in result)
    assert all(row["price_target_mean"] == 123.45 for row in result)
    assert all(values == {"recommendation": 3, "price-target": 1} for values in counts.values())
    assert len(request_urls) == 8
    assert all(secret in url and "/stock/" in url for url in request_urls)

    assert base_logger.warning.call_count == 4
    retry_calls = base_logger.warning.call_args_list
    assert all(call.args[0] == "retrying" for call in retry_calls)
    assert all(call.kwargs["error_type"] == analyst_source._TransientAnalystRequestError.__name__ for call in retry_calls)
    assert all(call.kwargs["status_code"] == expected_status_code for call in retry_calls)
    assert sorted(call.kwargs["attempt"] for call in retry_calls) == [1, 1, 2, 2]
    analyst_logger.error.assert_not_called()
    assert sum(call.args[0] == "Fetched analyst ratings" for call in analyst_logger.info.call_args_list) == 2
    parallel_logger.error.assert_not_called()

    captured_logs = repr((base_logger.mock_calls, analyst_logger.mock_calls, parallel_logger.mock_calls))
    assert secret not in captured_logs
    assert analyst_source.FINNHUB_BASE_URL not in captured_logs
    assert "synthetic provider failure" not in captured_logs


@pytest.mark.parametrize(("failure_kind", "expected_requests_per_ticker"), [("401", 1), ("shape", 2)])
def test_analyst_nontransient_failures_are_not_retried(
    failure_kind: str,
    expected_requests_per_ticker: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "synthetic-analyst-no-retry-token-unique"
    counts = {ticker: {"recommendation": 0, "price-target": 0} for ticker in ("AAPL", "MSFT")}
    request_urls: list[str] = []
    state_lock = Lock()

    class MockClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        def __enter__(self) -> Any:
            return self

        def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
            return None

        def get(self, url: str, params: dict[str, str]) -> httpx.Response:
            request = httpx.Request("GET", url, params=params)
            ticker = params["symbol"]
            endpoint = url.rsplit("/", maxsplit=1)[-1]
            with state_lock:
                counts[ticker][endpoint] += 1
                request_urls.append(str(request.url))
            if endpoint == "recommendation" and failure_kind == "401":
                return httpx.Response(401, request=request)
            if endpoint == "recommendation" and failure_kind == "shape":
                return httpx.Response(200, request=request, json={"unexpected": "shape"})
            return httpx.Response(200, request=request, json={"targetMean": 123.45})

    monkeypatch.setattr(analyst_source.httpx, "Client", MockClient)

    def fetch_for_ticker(ticker: str, trading_date: date) -> Any:
        fetcher = AnalystRatingFetcher(api_key=secret, tickers=[ticker], retry_attempts=3, retry_delay=0)
        return fetcher.fetch(trading_date).to_dicts()

    with (
        patch("equity_lake.sources.base.throttle"),
        patch.object(analyst_source, "logger") as analyst_logger,
        patch("equity_lake.sources.base.logger") as base_logger,
        patch("equity_lake.ingestion.parallel.logger") as parallel_logger,
    ):
        result = fetch_items_parallel(["AAPL", "MSFT"], fetch_for_ticker, date(2024, 1, 2), max_workers=2)

    assert result == []
    assert all(sum(values.values()) == expected_requests_per_ticker for values in counts.values())
    assert all(values["recommendation"] == 1 for values in counts.values())
    assert all(secret in url and "/stock/" in url for url in request_urls)
    assert sum("/stock/recommendation" in url for url in request_urls) == 2
    assert sum("/stock/price-target" in url for url in request_urls) == (0 if failure_kind == "401" else 2)
    base_logger.warning.assert_not_called()
    assert analyst_logger.error.call_count == (2 if failure_kind == "401" else 0)
    if failure_kind == "401":
        assert all(call.kwargs["error_type"] == "HTTPStatusError" for call in analyst_logger.error.call_args_list)
        assert all(call.kwargs["status_code"] == 401 for call in analyst_logger.error.call_args_list)
    else:
        assert sum(call.args[0] == "analyst_rating_invalid_recommendation_response" for call in analyst_logger.warning.call_args_list) == 2
    parallel_logger.error.assert_not_called()

    captured_logs = repr((base_logger.mock_calls, analyst_logger.mock_calls, parallel_logger.mock_calls))
    assert secret not in captured_logs
    assert analyst_source.FINNHUB_BASE_URL not in captured_logs


def test_analyst_request_exception_string_has_no_raw_message_or_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "synthetic-analyst-exception-token-unique"

    class MockClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        def __enter__(self) -> Any:
            return self

        def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
            return None

        def get(self, url: str, params: dict[str, str]) -> httpx.Response:
            request = httpx.Request("GET", url, params=params)
            raise httpx.ConnectError(f"unsafe synthetic cause token={secret} at {request.url}", request=request)

    monkeypatch.setattr(analyst_source.httpx, "Client", MockClient)
    fetcher = AnalystRatingFetcher(api_key=secret, tickers=["AAPL"], retry_attempts=1, retry_delay=0)

    with pytest.raises(analyst_source._TransientAnalystRequestError) as caught:
        fetcher._get_json("AAPL", "recommendation")

    assert str(caught.value) == "Finnhub analyst ratings request failed"
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
