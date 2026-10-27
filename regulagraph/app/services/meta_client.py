"""Provider-independent model adapter and the Meta Model API implementation.

Verified against the official docs on 2026-10-27:
  * https://dev.meta.ai/docs/api-reference/chat-completions/create-chat-completion
  * https://dev.meta.ai/docs/api-reference/chat-completions/schemas
  * https://dev.meta.ai/docs/authentication
  * https://dev.meta.ai/docs/error-handling

  POST {base_url}/chat/completions   (base_url = https://api.meta.ai/v1)
  Authorization: Bearer <MODEL_API_KEY>
  Request:  {"model", "messages": [{"role", "content"}], "temperature"?, "max_completion_tokens"?, "seed"?}
  Response: {"id", "model", "choices": [{"message": {"content", "refusal"}, "finish_reason"}], "usage"?}
  Error:    {"error": {"message", "type", "param", "code"}}

Billing safeguard: no request is sent unless ``META_INFERENCE_ENABLED=true``.
"""

import logging
from collections.abc import Sequence
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings
from app.core.errors import InferenceDisabledError, ModelProviderError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 503}


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class CompletionResult(BaseModel):
    response_id: str
    model_id: str
    text: str | None
    refusal: str | None = None
    finish_reason: str
    usage: TokenUsage | None = None


class ModelAdapter(Protocol):
    """What the rest of the backend depends on; swap implementations in tests."""

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_completion_tokens: int | None = None,
        seed: int | None = None,
    ) -> CompletionResult: ...


# Wire format of the provider response (only the fields we consume).
class _WireMessage(BaseModel):
    content: str | None = None
    refusal: str | None = None


class _WireChoice(BaseModel):
    message: _WireMessage
    finish_reason: str


class _WireResponse(BaseModel):
    id: str
    model: str
    choices: list[_WireChoice] = Field(min_length=1)
    usage: TokenUsage | None = None


class MetaModelAdapter:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_completion_tokens: int | None = None,
        seed: int | None = None,
    ) -> CompletionResult:
        s = self._settings
        if not s.meta_inference_enabled:
            raise InferenceDisabledError("Meta inference is disabled; set META_INFERENCE_ENABLED=true to allow paid calls")
        if s.meta_api_key is None or not s.meta_api_key.get_secret_value():
            raise InferenceDisabledError("MODEL_API_KEY is not configured")
        if not messages:
            raise ValueError("messages must not be empty")

        cap = s.meta_max_completion_tokens
        payload: dict[str, object] = {
            "model": s.meta_model_id,
            "messages": [m.model_dump() for m in messages],
            "max_completion_tokens": min(max_completion_tokens or cap, cap),
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if seed is not None:
            payload["seed"] = seed

        headers = {"Authorization": f"Bearer {s.meta_api_key.get_secret_value()}"}
        logger.info("meta chat completion: model=%s messages=%d", s.meta_model_id, len(messages))
        try:
            with httpx.Client(
                base_url=s.meta_api_base_url, timeout=s.meta_request_timeout_seconds, transport=self._transport
            ) as client:
                response = client.post("/chat/completions", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ModelProviderError("Meta API request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ModelProviderError(f"Meta API transport error: {type(exc).__name__}", retryable=True) from exc

        if response.status_code != 200:
            raise _provider_error(response)

        try:
            wire = _WireResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ModelProviderError("unexpected Meta API response schema", status_code=200) from exc

        choice = wire.choices[0]
        return CompletionResult(
            response_id=wire.id,
            model_id=wire.model,
            text=choice.message.content,
            refusal=choice.message.refusal,
            finish_reason=choice.finish_reason,
            usage=wire.usage,
        )


def _provider_error(response: httpx.Response) -> ModelProviderError:
    message, error_type = "Meta API request failed", None
    try:
        err = response.json().get("error") or {}
        message = str(err.get("message") or message)
        error_type = err.get("type")
    except (ValueError, AttributeError):
        pass  # e.g. empty body on unknown path (404)
    return ModelProviderError(
        message,
        status_code=response.status_code,
        error_type=error_type,
        retryable=response.status_code in _RETRYABLE_STATUS,
    )
