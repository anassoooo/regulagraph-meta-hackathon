"""Sprint 5: the hosting configuration serves only the static snapshot, with the app's own security headers."""

import re
from pathlib import Path

from app.main import SECURITY_HEADERS

RENDER_YAML = Path(__file__).resolve().parents[2] / "render.yaml"


def headers() -> dict[str, str]:
    text = RENDER_YAML.read_text(encoding="utf-8")
    rules = re.findall(r"- path: (\S+)\n\s+name: (\S+)\n\s+value: (.+)", text)
    assert rules, "no header rules found"
    assert all(path == "/*" for path, _, _ in rules)
    return {name: value.strip().strip('"') for _, name, value in rules}


def test_static_site_headers_match_the_app() -> None:
    assert headers() == SECURITY_HEADERS


def test_render_config_is_static_manual_and_publishes_only_the_snapshot() -> None:
    text = RENDER_YAML.read_text(encoding="utf-8")
    assert "runtime: static" in text
    assert 'autoDeployTrigger: "off"' in text
    assert "staticPublishPath: ./regulagraph/site\n" in text
    # No server process, no environment, no secret: a static site has nothing to configure.
    for forbidden in ("startCommand", "envVars", "MODEL_API_KEY", "META_", "pip install", "fetch_corpus"):
        assert forbidden not in text, forbidden
