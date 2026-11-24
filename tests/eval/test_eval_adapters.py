"""Evaluation-only service settings and transport: no secret in argv, base URL recorded, cache replay (roadmap E10)."""

import json

import httpx2 as httpx
import pytest
from openai import OpenAI

from pfc_eval import adapters


def test_settings_are_free_of_the_curated_pin_but_validated():
    default = adapters.Settings.default()
    assert default.model == "gpt-6-astra" and default.temperature is None and default.reasoning_effort == "low"
    assert default.base_url == adapters.OPENAI_BASE_URL and default.protocol == adapters.PROTOCOL
    local = adapters.Settings("qwen3-32b", 0.7, 2048, None, base_url="http://localhost:8000/v1")
    assert (
        local.snapshot_status() == "undated"
        and adapters.Settings("gpt-5.4-2026-03-05", 0.2).snapshot_status() == "dated"
    )
    for bad in (
        dict(model=""),
        dict(temperature=3.0),
        dict(max_output_tokens=0),
        dict(reasoning_effort="extreme"),
        dict(base_url="ftp://x"),
        dict(base_url="https://user:secret@host/v1"),
        dict(base_url="https://host/v1?key=abc"),
    ):
        with pytest.raises(ValueError):
            adapters.Settings(**{"model": "m", **bad})  # type: ignore[arg-type]
    assert adapters.describe(local)["base_url"] == "http://localhost:8000/v1" and "key" not in adapters.describe(local)


def test_request_validates_prompt_and_sampling_identity():
    settings = adapters.Settings.default()
    request = adapters.Request("prompt", "abc/L1/run-1/sum/1", settings)
    assert request.sample_id.endswith("/1")
    with pytest.raises(ValueError):
        adapters.Request("", "x", settings)
    with pytest.raises(ValueError):
        adapters.Request("prompt", "bad id!", settings)
    with pytest.raises(ValueError):
        adapters.Request("p" * 70000, "x", settings)


def test_missing_key_fails_without_network_or_secret_output(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    request = adapters.Request("prompt", "s/1", adapters.Settings.default())
    with pytest.raises(ValueError) as error:
        adapters.send(request)
    assert "OPENAI_API_KEY" in str(error.value) and "never" in str(error.value)


def test_wire_contract_through_a_mocked_endpoint_records_tokens_latency_and_base_url():
    settings = adapters.Settings("local-model", 0.7, 1024, None, base_url="http://localhost:8000/v1")
    request = adapters.Request("prompt text", "id/L3/run-1/sum/1", settings)
    seen = {}

    def handler(wire):
        seen["url"] = str(wire.url)
        payload = json.loads(wire.content)
        seen["payload"] = payload
        return httpx.Response(
            200,
            json={
                "id": "resp_local",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "local-model",
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 7,
                    "output_tokens_details": {"reasoning_tokens": 3},
                },
                "output": [
                    {
                        "id": "msg",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "annotations": [], "text": '{"invariants":["0 <= i"]}'}],
                    }
                ],
            },
        )

    with OpenAI(
        api_key="test-only",
        base_url=settings.base_url,
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as client:
        exchange = adapters.send(request, client=client)
    assert seen["url"].startswith("http://localhost:8000/v1/responses")
    assert seen["payload"]["model"] == "local-model" and seen["payload"]["temperature"] == 0.7
    assert "reasoning" not in seen["payload"] and seen["payload"]["store"] is False
    assert seen["payload"]["text"]["format"]["strict"] is True
    assert exchange.text.startswith("{") and exchange.input_tokens == 12 and exchange.output_tokens == 7
    assert exchange.reasoning_tokens == 3 and exchange.latency_ms >= 0 and exchange.origin == "live"

    def denied(wire):
        return httpx.Response(401, json={"error": {"message": "SECRET-MUST-NOT-APPEAR", "type": "auth"}})

    with OpenAI(
        api_key="test-only", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(denied))
    ) as client:
        with pytest.raises(ValueError) as error:
            adapters.send(request, client=client)
    assert "SECRET" not in str(error.value)


def test_cache_binds_the_request_and_replays_without_the_network(tmp_path):
    settings = adapters.Settings.default()
    request = adapters.Request("prompt", "id/L1/run-1/sum/1", settings)
    calls = []

    def fake(req):
        calls.append(req)
        return adapters.Exchange('{"invariants":["1"]}', settings.model, "resp_1", 5, 4, 0, 12.5, "live")

    first = adapters.complete(request, tmp_path / "cache", live=True, send=fake)
    assert first.origin == "live" and len(calls) == 1
    again = adapters.complete(request, tmp_path / "cache", live=False, send=fake)
    assert again.origin == "replay" and again.text == first.text and len(calls) == 1
    other = adapters.Request("prompt", "id/L1/run-1/sum/2", settings)
    with pytest.raises(ValueError, match="cache miss"):
        adapters.complete(other, tmp_path / "cache", live=False, send=fake)
    files = list((tmp_path / "cache").glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["request"]["settings"]["base_url"] == settings.base_url


@pytest.mark.parametrize("text,model", [("", "gpt-6-astra"), ("x" * 17000, "gpt-6-astra"), ('{"a":1}', "other")])
def test_invalid_exchanges_are_rejected_and_never_cached(tmp_path, text, model):
    settings = adapters.Settings.default()
    request = adapters.Request("prompt", "id/1", settings)

    def fake(req):
        return adapters.Exchange(text, model, "resp", 1, 1, 0, 1.0, "live")

    with pytest.raises(ValueError):
        adapters.complete(request, tmp_path / "cache", live=True, send=fake)
    assert not (tmp_path / "cache").exists() or not list((tmp_path / "cache").iterdir())
