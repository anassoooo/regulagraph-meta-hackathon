"""Opt-in live test against the real Meta Model API. Costs money; skipped by default.

Runs only when RUN_LIVE_META=1, META_INFERENCE_ENABLED=true and MODEL_API_KEY are
all set, after the credit balance has been checked in the Meta dashboard.
"""

import os

import pytest

from app.core.config import Settings
from app.services.meta_client import ChatMessage, MetaModelAdapter

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE_META") != "1", reason="live Meta API test is opt-in")


def test_live_minimal_completion() -> None:
    settings = Settings()
    if not settings.meta_inference_enabled or settings.meta_api_key is None:
        pytest.skip("META_INFERENCE_ENABLED=true and MODEL_API_KEY are required")
    result = MetaModelAdapter(settings).complete(
        [ChatMessage(role="user", content='Reply with the JSON {"status": "ok"} only.')],
        max_completion_tokens=64,
        temperature=0,
        reasoning_effort="minimal",
    )
    assert result.http_status == 200
    assert result.finish_reason in {"stop", "length"}
    assert result.usage is not None and result.usage.total_tokens > 0
