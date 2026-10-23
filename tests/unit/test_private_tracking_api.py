"""Private tracking API loopback boundary tests."""

from __future__ import annotations

from importlib import import_module

import pytest
from fastapi import HTTPException
from starlette.requests import Request

router = import_module("equity_lake.api.routers.tracking")


def _request(client_host: str, host_header: str, headers: dict[str, str] | None = None) -> Request:
    raw_headers = [(b"host", host_header.encode("ascii"))]
    raw_headers.extend((key.lower().encode("ascii"), value.encode("ascii")) for key, value in (headers or {}).items())
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/tracking/private",
            "headers": raw_headers,
            "client": (client_host, 50000),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
    )


@pytest.mark.parametrize(
    ("client_host", "host_header"),
    [("127.0.0.1", "localhost:8000"), ("::1", "[::1]:8000"), ("127.0.0.1", "127.0.0.1")],
)
def test_direct_loopback_clients_and_hosts_are_allowed(client_host: str, host_header: str) -> None:
    assert router.require_loopback(_request(client_host, host_header)) is None


@pytest.mark.parametrize(
    ("client_host", "host_header", "headers"),
    [
        ("192.0.2.10", "127.0.0.1", {}),
        ("127.0.0.1", "192.0.2.10", {}),
        ("127.0.0.1", "127.0.0.1", {"x-forwarded-for": "192.0.2.10"}),
        ("127.0.0.1", "127.0.0.1", {"x-real-ip": "192.0.2.10"}),
        ("127.0.0.1", "127.0.0.1", {"forwarded": "for=192.0.2.10"}),
        ("127.0.0.1", "127.0.0.1", {"x-forwarded-proto": "https"}),
    ],
)
def test_remote_or_proxied_requests_fail_closed(client_host: str, host_header: str, headers: dict[str, str]) -> None:
    with pytest.raises(HTTPException) as raised:
        router.require_loopback(_request(client_host, host_header, headers))
    assert raised.value.status_code == 403
