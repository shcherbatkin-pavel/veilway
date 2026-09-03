from __future__ import annotations

import httpx
import pytest

from veilway_control.cloud import CloudOperationError, YandexCloudProvider
from veilway_control.config import Settings


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://compute.example.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                "request failed", request=request, response=response
            )

    def json(self) -> dict:
        return self.payload


def make_provider() -> YandexCloudProvider:
    provider = YandexCloudProvider(
        Settings(
            public_host="testserver",
            yandex_compute_api="https://compute.example.test",
        ),
        "allowed-instance",
    )
    provider._iam_token = lambda: "test-token"  # type: ignore[method-assign]
    return provider


def test_yandex_operation_uses_instance_scoped_endpoint(monkeypatch) -> None:
    def fake_get(url, **kwargs):
        assert url == (
            "https://compute.example.test/compute/v1/instances/"
            "allowed-instance/operations"
        )
        assert kwargs["params"] == {"pageSize": 100}
        return FakeResponse(
            {"operations": [{"id": "expected-operation", "done": True}]}
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    assert make_provider().operation_complete("expected-operation") is True


def test_yandex_operation_not_yet_listed_keeps_waiting(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: FakeResponse({"operations": []}),
    )

    assert make_provider().operation_complete("expected-operation") is False


def test_yandex_operation_error_is_definitive(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(
            {
                "operations": [
                    {"id": "expected-operation", "done": True, "error": {"code": 1}}
                ]
            }
        ),
    )

    with pytest.raises(CloudOperationError) as raised:
        make_provider().operation_complete("expected-operation")

    assert raised.value.definitive is True
