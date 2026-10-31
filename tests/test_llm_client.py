"""Model-client contract tests (no external network calls)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from open_code_ai_connect import ProviderError

from doctorcode.engine.llm_client import CompatibleChatClient, LLMError, load_models
from doctorcode.settings import ConnectionProfile


class _TestServer(ThreadingHTTPServer):
    daemon_threads = True


@contextmanager
def catalog_server(
    *,
    status: int = 200,
    payload: Any = None,
    raw: bytes | None = None,
    delay: float = 0.0,
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    body = (
        raw
        if raw is not None
        else json.dumps(
            payload if payload is not None else {"data": [{"id": "b"}, {"id": "a"}]}
        ).encode("utf-8")
    )
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            requests.append({"path": self.path, "headers": dict(self.headers)})
            if delay:
                time.sleep(delay)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *args: object) -> None:
            pass

        def handle(self) -> None:
            with suppress(ConnectionError, OSError):
                super().handle()

    server = _TestServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_rejects_missing_base_url() -> None:
    with pytest.raises(LLMError, match="base URL"):
        CompatibleChatClient(ConnectionProfile(model="some-model"))


def test_client_can_be_constructed_before_a_model_is_chosen() -> None:
    client = CompatibleChatClient(
        ConnectionProfile(base_url="http://127.0.0.1:9/v1"),
        timeout=1.0,
    )

    async def scenario() -> None:
        with pytest.raises(LLMError, match="model is required"):
            await client.complete([], [])
        await client.close()

    asyncio.run(scenario())


def test_load_models_uses_base_url_and_bearer_token() -> None:
    payload = {
        "data": [
            {"id": "  zeta  "},
            {"id": "alpha"},
            {"id": "alpha"},
            {"id": ""},
            {"no_id": True},
        ]
    }
    profile = ConnectionProfile(model="unused", api_key="sk-test")

    with catalog_server(payload=payload) as (base_url, requests):
        profile.base_url = f"{base_url}/v1/"
        models = asyncio.run(load_models(profile, timeout=5.0))

    assert models == ["alpha", "zeta"]
    assert len(requests) == 1
    assert requests[0]["path"] == "/v1/models"
    assert requests[0]["headers"]["Authorization"] == "Bearer sk-test"


def test_load_models_sends_no_authorization_header_without_a_key() -> None:
    profile = ConnectionProfile(model="unused")

    with catalog_server(payload={"data": [{"id": "m"}]}) as (base_url, requests):
        profile.base_url = base_url
        assert asyncio.run(load_models(profile, timeout=5.0)) == ["m"]

    assert "Authorization" not in requests[0]["headers"]


@pytest.mark.parametrize(
    ("provider_code", "llm_code", "retryable"),
    [
        ("INVALID_BASE_URL", "MODELS_BASE_URL", False),
        ("AUTH", "MODELS_AUTH", False),
        ("FORBIDDEN", "MODELS_FORBIDDEN", False),
        ("RATE_LIMIT", "MODELS_RATE_LIMIT", True),
        ("SERVER", "MODELS_SERVER", True),
        ("HTTP_STATUS", "MODELS_HTTP", False),
        ("TIMEOUT", "MODELS_TIMEOUT", True),
        ("NETWORK", "MODELS_CONNECTION", True),
        ("INVALID_RESPONSE", "MODELS_INVALID_RESPONSE", False),
        ("EMPTY_CATALOG", "MODELS_EMPTY", False),
    ],
)
def test_provider_errors_map_to_stable_llm_codes(
    monkeypatch: pytest.MonkeyPatch,
    provider_code: str,
    llm_code: str,
    retryable: bool,
) -> None:
    class FakeManager:
        def __init__(self, base_url: str, api_key: str = "", *, timeout: float = 40.0):
            assert base_url
            assert api_key == "sk-secret"

        def list_models(self) -> list[str]:
            raise ProviderError(
                provider_code,
                f"failure {provider_code}",
                retryable=retryable,
            )

    monkeypatch.setattr("doctorcode.engine.llm_client.ProviderManager", FakeManager)
    profile = ConnectionProfile(
        base_url="http://example.invalid/v1", api_key="sk-secret", model="m"
    )

    with pytest.raises(LLMError) as info:
        asyncio.run(load_models(profile, timeout=1.0))

    assert info.value.code == llm_code
    assert info.value.retryable is retryable
    assert "sk-secret" not in str(info.value)
    assert "sk-secret" not in repr(info.value)


def test_missing_connect_module_maps_to_a_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provider catalogue lives in an optional module; its absence is typed."""
    from doctorcode.engine import llm_client

    monkeypatch.setattr(llm_client, "ProviderManager", None)
    profile = ConnectionProfile(
        base_url="http://example.invalid/v1", api_key="", model="m"
    )

    with pytest.raises(LLMError) as info:
        asyncio.run(llm_client.load_models(profile, timeout=1.0))

    assert info.value.code == "MODELS_MODULE_MISSING"
    assert "open_code_ai_connect" in str(info.value)


@pytest.mark.parametrize(
    "base_url",
    ["", "example.com/v1", "http://example.com/v1/models", "ftp://example.com/v1"],
)
def test_invalid_base_url_maps_to_models_base_url(base_url: str) -> None:
    profile = ConnectionProfile(base_url=base_url, model="m")

    with pytest.raises(LLMError) as info:
        asyncio.run(load_models(profile, timeout=1.0))

    assert info.value.code == "MODELS_BASE_URL"
    assert info.value.retryable is False


def test_malformed_catalog_maps_to_invalid_response() -> None:
    profile = ConnectionProfile(model="m")

    with catalog_server(raw=b"not-json") as (base_url, _):
        profile.base_url = base_url
        with pytest.raises(LLMError) as info:
            asyncio.run(load_models(profile, timeout=5.0))

    assert info.value.code == "MODELS_INVALID_RESPONSE"


def test_empty_catalog_maps_to_empty_code() -> None:
    profile = ConnectionProfile(model="m")

    with catalog_server(payload={"data": []}) as (base_url, _):
        profile.base_url = base_url
        with pytest.raises(LLMError) as info:
            asyncio.run(load_models(profile, timeout=5.0))

    assert info.value.code == "MODELS_EMPTY"


def test_http_error_maps_status_and_never_leaks_the_key() -> None:
    profile = ConnectionProfile(model="m", api_key="sk-never-leak")

    with catalog_server(status=401, payload={"error": "no"}) as (base_url, _):
        profile.base_url = base_url
        with pytest.raises(LLMError) as info:
            asyncio.run(load_models(profile, timeout=5.0))

    assert info.value.code == "MODELS_AUTH"
    assert "sk-never-leak" not in str(info.value)


def test_network_failure_is_retryable_and_never_leaks_the_key() -> None:
    profile = ConnectionProfile(
        base_url="http://256.256.256.256/v1",
        api_key="sk-never-leak",
        model="m",
    )

    with pytest.raises(LLMError) as info:
        asyncio.run(load_models(profile, timeout=1.0))

    assert info.value.code == "MODELS_CONNECTION"
    assert info.value.retryable is True
    assert "sk-never-leak" not in str(info.value)
    assert "sk-never-leak" not in repr(info.value)
