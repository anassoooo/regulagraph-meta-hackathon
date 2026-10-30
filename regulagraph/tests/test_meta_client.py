"""Adapter tests use httpx.MockTransport: no network, no paid calls."""

import json
import logging

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import InferenceDisabledError, ModelProviderError
from app.services.meta_client import ChatMessage, MetaModelAdapter

FAKE_KEY = "test-key-not-real-123"
MESSAGES = [ChatMessage(role="user", content="Bonjour")]

OK_BODY = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1761523200,
    "model": "muse-spark-1.3",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Salut", "refusal": None},
            "finish_reason": "stop",
            "logprobs": None,
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}


def _adapter(settings_factory, handler, **overrides) -> MetaModelAdapter:
    settings = settings_factory(meta_api_key=FAKE_KEY, meta_inference_enabled=True, **overrides)
    return MetaModelAdapter(settings, transport=httpx.MockTransport(handler))


def _never_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError("network must not be reached")


def test_disabled_by_default_and_never_hits_network(settings_factory) -> None:
    adapter = MetaModelAdapter(settings_factory(meta_api_key=FAKE_KEY), transport=httpx.MockTransport(_never_called))
    with pytest.raises(InferenceDisabledError):
        adapter.complete(MESSAGES)


def test_missing_key_blocks_call(settings_factory) -> None:
    adapter = MetaModelAdapter(
        settings_factory(meta_inference_enabled=True), transport=httpx.MockTransport(_never_called)
    )
    with pytest.raises(InferenceDisabledError, match="MODEL_API_KEY"):
        adapter.complete(MESSAGES)


def test_request_matches_documented_contract(settings_factory) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=OK_BODY)

    result = _adapter(settings_factory, handler, meta_max_completion_tokens=256).complete(
        MESSAGES, temperature=0.0, max_completion_tokens=10_000, seed=7
    )

    assert seen["url"] == "https://api.meta.ai/v1/chat/completions"
    assert seen["auth"] == f"Bearer {FAKE_KEY}"
    assert seen["body"] == {
        "model": "muse-spark-1.3",
        "messages": [{"role": "user", "content": "Bonjour"}],
        "max_completion_tokens": 256,  # capped by settings
        "temperature": 0.0,
        "seed": 7,
    }
    assert result.text == "Salut"
    assert result.finish_reason == "stop"
    assert result.usage is not None and result.usage.total_tokens == 7


@pytest.mark.parametrize(("status", "retryable"), [(400, False), (401, False), (429, True), (503, True)])
def test_provider_errors_are_structured(settings_factory, status: int, retryable: bool) -> None:
    body = {"error": {"message": "boom", "type": "rate_limit_error", "param": None, "code": None}}
    adapter = _adapter(settings_factory, lambda r: httpx.Response(status, json=body))
    with pytest.raises(ModelProviderError) as exc:
        adapter.complete(MESSAGES)
    assert exc.value.status_code == status
    assert exc.value.retryable is retryable
    assert exc.value.error_type == "rate_limit_error"


def test_empty_error_body_is_handled(settings_factory) -> None:
    adapter = _adapter(settings_factory, lambda r: httpx.Response(404))
    with pytest.raises(ModelProviderError) as exc:
        adapter.complete(MESSAGES)
    assert exc.value.status_code == 404


def test_unexpected_schema_is_rejected(settings_factory) -> None:
    adapter = _adapter(settings_factory, lambda r: httpx.Response(200, json={"id": "x", "choices": []}))
    with pytest.raises(ModelProviderError, match="schema"):
        adapter.complete(MESSAGES)


def test_timeout_is_retryable(settings_factory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ModelProviderError) as exc:
        _adapter(settings_factory, handler).complete(MESSAGES)
    assert exc.value.retryable


def test_api_key_never_logged_or_repr(settings_factory, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    settings = settings_factory(meta_api_key=FAKE_KEY, meta_inference_enabled=True)
    MetaModelAdapter(settings, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=OK_BODY))).complete(
        MESSAGES
    )
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in settings.model_dump_json()


_ENV_VARS = ["MODEL_API_KEY", "META_INFERENCE_ENABLED", "META_API_BASE_URL", "META_MODEL_ID"]


@pytest.fixture
def no_http_client(monkeypatch):
    """Fail if anything tries to build an HTTP client at all."""

    def _boom(*args, **kwargs):
        raise AssertionError("httpx.Client must not be constructed while inference is disabled")

    monkeypatch.setattr(httpx, "Client", _boom)


def test_real_defaults_disable_inference(monkeypatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.meta_inference_enabled is False
    assert settings.meta_api_key is None


@pytest.mark.parametrize("key", [None, FAKE_KEY])
def test_disabled_adapter_never_creates_http_client(monkeypatch, no_http_client, key) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    if key:
        monkeypatch.setenv("MODEL_API_KEY", key)  # a key alone must not enable calls
    adapter = MetaModelAdapter(Settings(_env_file=None))
    with pytest.raises(InferenceDisabledError):
        adapter.complete(MESSAGES)


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_falsey_env_values_keep_inference_disabled(monkeypatch, no_http_client, value) -> None:
    monkeypatch.setenv("MODEL_API_KEY", FAKE_KEY)
    monkeypatch.setenv("META_INFERENCE_ENABLED", value)
    with pytest.raises(InferenceDisabledError):
        MetaModelAdapter(Settings(_env_file=None)).complete(MESSAGES)


# Sprint 2: structured output, reasoning effort, latency, rate-limit headers ----

def test_optional_fields_are_sent_only_when_given(settings_factory) -> None:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=OK_BODY, headers={"x-ratelimit-remaining-tokens": "3999000"})

    adapter = _adapter(settings_factory, handler)
    plain = adapter.complete(MESSAGES)
    fmt = {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}, "strict": True}}
    adapter.complete(MESSAGES, response_format=fmt, reasoning_effort="low")
    assert "response_format" not in sent[0] and "reasoning_effort" not in sent[0]
    assert sent[1]["response_format"] == fmt and sent[1]["reasoning_effort"] == "low"
    assert plain.http_status == 200 and plain.latency_ms is not None and plain.latency_ms >= 0
    assert plain.rate_limit_remaining == {"x-ratelimit-remaining-tokens": "3999000"}


@pytest.mark.parametrize("effort", ["", "ultra", "LOW"])
def test_unknown_reasoning_effort_rejected_before_request(settings_factory, effort) -> None:
    from app.core.errors import InvalidModelRequestError

    with pytest.raises(InvalidModelRequestError, match="reasoning_effort"):
        _adapter(settings_factory, _never_called).complete(MESSAGES, reasoning_effort=effort)


def test_reasoning_tokens_are_read_from_usage_details(settings_factory) -> None:
    body = {**OK_BODY, "usage": {**OK_BODY["usage"], "completion_tokens_details": {"reasoning_tokens": 3}}}
    result = _adapter(settings_factory, lambda r: httpx.Response(200, json=body)).complete(MESSAGES)
    assert result.usage.reasoning_tokens == 3
    assert _adapter(settings_factory, lambda r: httpx.Response(200, json=OK_BODY)).complete(MESSAGES).usage.reasoning_tokens == 0
