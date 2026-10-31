"""The four Sprint 3 replay responses are byte-for-byte unchanged, in replay-only and in live mode.

The hashes were captured from main at 5beecb4 (Sprint 3 merged), before any Sprint 3.5 change,
on Python 3.13.12 and 3.11.15 (identical). A change to any replay response, however small, fails here.
"""

import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.services.corpus import DEFAULT_RAW_DIR, load_manifest

GOLDEN = {
    "E01": ("0cff4f8555ee761ea6d8b1d72cb37e57d276249e8098070ef496ffeb07428562", 5032),
    "E12": ("be4bc6b2bdb9c880e47258d093e7bdc0f1f208426770f0e8b4f5597b74f220af", 3343),
    "E05": ("7784a3237fd422b4ce5fa98f8c86c4eb6384b2e6b6e5c8adcbba81c64cec0f16", 3353),
    "E13": ("cd54a220e9af5f4ecb4099fa2318e8df1becbb349fc7e3305c617d1def0c8600", 3559),
}
SCENARIOS_SHA256 = "d9f97a62b9c19a5611a81fe51e9bfa6c2f565b5477c020d237ddea4b8ee39752"
EVALUATION_SHA256 = "4fb7666c925c6dbaac72b9ec0589ab531ac3c5629d40a9563fdfd1075d5323a3"

pytestmark = pytest.mark.skipif(
    not all((DEFAULT_RAW_DIR / e.filename).is_file() for e in load_manifest().documents if e.role == "investigation"),
    reason="corpus PDFs not downloaded",
)


@pytest.fixture(params=["replay-only", "live mode"])
def client(request, monkeypatch, tmp_path):
    def no_network(*args, **kwargs):
        raise AssertionError("replay must not make network calls")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    from app.services.meta_client import MetaModelAdapter

    monkeypatch.setattr(MetaModelAdapter, "complete", no_network)
    if request.param == "live mode":
        monkeypatch.setenv("LIVE_MODE_ENABLED", "true")
        monkeypatch.setenv("META_INFERENCE_ENABLED", "true")
        monkeypatch.setenv("MODEL_API_KEY", "golden-test-key-0123")
        monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path / "live"))
    else:
        monkeypatch.setenv("LIVE_MODE_ENABLED", "false")
        monkeypatch.setenv("META_INFERENCE_ENABLED", "false")
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


@pytest.mark.parametrize("scenario_id", list(GOLDEN))
def test_replay_response_is_byte_identical(client, scenario_id) -> None:
    r = client.post(f"/api/scenarios/{scenario_id}/replay")
    assert r.status_code == 200
    assert (hashlib.sha256(r.content).hexdigest(), len(r.content)) == GOLDEN[scenario_id]


def test_scenarios_and_evaluation_are_byte_identical(client) -> None:
    assert hashlib.sha256(client.get("/api/scenarios").content).hexdigest() == SCENARIOS_SHA256
    assert hashlib.sha256(client.get("/api/evaluation").content).hexdigest() == EVALUATION_SHA256
