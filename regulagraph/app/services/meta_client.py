"""Provider-independent model adapter and the Meta Model API implementation.

Verified against the official docs on 2026-10-27:
  * https://dev.meta.ai/docs/api-reference/chat-completions/create-chat-completion
  * https://dev.meta.ai/docs/api-reference/chat-completions/schemas
  * https://dev.meta.ai/docs/authentication
  * https://dev.meta.ai/docs/error-handling

  POST {base_url}/chat/completions   (base_url = https://api.meta.ai/v1)
  Authorization: Bearer <MODEL_API_KEY>
  Request:  {"model", "messages": [{"role", "content"}], "temperature"?, "max_completion_tokens"?, "seed"?,
             "response_format"?, "reasoning_effort"?}
  Response: {"id", "model", "choices": [{"message": {"content", "refusal"}, "finish_reason"}], "usage"?}
  Error:    {"error": {"message", "type", "param", "code"}}

Re-checked 2026-10-30 (schemas page): ``max_completion_tokens`` caps visible
output *plus reasoning* tokens; ``finish_reason`` is one of stop, length,
tool_calls, content_filter, function_call; ``response_format`` accepts
``{"type": "json_schema", "json_schema": {"name", "schema", "strict"}}``;
``reasoning_effort`` is none|minimal|low|medium|high|xhigh|max. Pricing page:
pay-as-you-go, no free tier, no spending limit and no balance endpoint listed.

Billing safeguard: no request is sent unless ``META_INFERENCE_ENABLED=true``.
"""

import logging
import time
from collections.abc import Sequence
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings
from app.core.errors import InferenceDisabledError, InvalidModelRequestError, ModelProviderError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 503}
REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})
_RATE_LIMIT_HEADERS = ("x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens")


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class _CompletionTokenDetails(BaseModel):
    reasoning_tokens: int = 0


class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    completion_tokens_details: _CompletionTokenDetails | None = None

    @property
    def reasoning_tokens(self) -> int:
        return self.completion_tokens_details.reasoning_tokens if self.completion_tokens_details else 0


class CompletionResult(BaseModel):
    response_id: str
    model_id: str
    text: str | None
    refusal: str | None = None
    finish_reason: str
    usage: TokenUsage | None = None
    latency_ms: float | None = None
    http_status: int = 200
    rate_limit_remaining: dict[str, str] = Field(default_factory=dict)


class ModelAdapter(Protocol):
    """What the rest of the backend depends on; swap implementations in tests."""

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_completion_tokens: int | None = None,
        seed: int | None = None,
        response_format: dict[str, object] | None = None,
        reasoning_effort: str | None = None,
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
        response_format: dict[str, object] | None = None,
        reasoning_effort: str | None = None,
    ) -> CompletionResult:
        s = self._settings
        if not s.meta_inference_enabled:
            raise InferenceDisabledError("Meta inference is disabled; set META_INFERENCE_ENABLED=true to allow paid calls")
        if s.meta_api_key is None or not s.meta_api_key.get_secret_value():
            raise InferenceDisabledError("MODEL_API_KEY is not configured")
        if not messages:
            raise InvalidModelRequestError("messages must not be empty")
        # Explicit check: `0 or cap` would silently turn 0 into the cap, and bool is an int subclass.
        if max_completion_tokens is not None and (
            isinstance(max_completion_tokens, bool)
            or not isinstance(max_completion_tokens, int)
            or max_completion_tokens <= 0
        ):
            raise InvalidModelRequestError(
                "max_completion_tokens must be a positive integer",
                details={"max_completion_tokens": repr(max_completion_tokens)},
            )
        if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORTS:
            raise InvalidModelRequestError("unsupported reasoning_effort", details={"reasoning_effort": reasoning_effort})

        cap = s.meta_max_completion_tokens
        tokens = cap if max_completion_tokens is None else min(max_completion_tokens, cap)
        payload: dict[str, object] = {
            "model": s.meta_model_id,
            "messages": [m.model_dump() for m in messages],
            "max_completion_tokens": tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if seed is not None:
            payload["seed"] = seed
        if response_format is not None:
            payload["response_format"] = response_format
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort

        headers = {"Authorization": f"Bearer {s.meta_api_key.get_secret_value()}"}
        logger.info("meta chat completion: model=%s messages=%d", s.meta_model_id, len(messages))
        started = time.perf_counter()
        try:
            with httpx.Client(
                base_url=s.meta_api_base_url, timeout=s.meta_request_timeout_seconds, transport=self._transport
            ) as client:
                response = client.post("/chat/completions", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ModelProviderError("Meta API request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ModelProviderError(f"Meta API transport error: {type(exc).__name__}", retryable=True) from exc
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

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
            latency_ms=latency_ms,
            http_status=response.status_code,
            rate_limit_remaining={h: response.headers[h] for h in _RATE_LIMIT_HEADERS if h in response.headers},
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


class ReplayAdapter:
    """Returns pre-recorded completions instead of calling the API (tests, offline demos).

    Results are labelled with ``model_id`` as given; a replayed answer must never
    be reported as a live model result.
    """

    def __init__(self, results: Sequence[CompletionResult]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, object]] = []

    @classmethod
    def from_text(cls, text: str | None, *, model_id: str = "replay", finish_reason: str = "stop",
                  refusal: str | None = None) -> "ReplayAdapter":
        return cls([CompletionResult(response_id="replay", model_id=model_id, text=text,
                                     refusal=refusal, finish_reason=finish_reason)])

    def complete(self, messages: Sequence[ChatMessage], **kwargs: object) -> CompletionResult:
        self.calls.append({"messages": list(messages), **kwargs})
        if not self._results:
            raise ModelProviderError("replay adapter has no more recorded results")
        return self._results.pop(0)
