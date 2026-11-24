import json
from dataclasses import replace

import pytest


def test_cache_is_bound_to_prompt_configuration_and_sampling_identity(tmp_path):
    from pfc.propose.llm import Exchange, Request, Settings, complete

    sent = []

    def send(request):
        sent.append(request)
        return Exchange('{"invariants":["0<=i && i<=n"]}', request.settings.model, "response-test", 12, 8, "test")

    request = Request("curated prompt", "experiment/run-1/sum/1", Settings())
    first = complete(request, tmp_path, live=True, send=send)
    replay = complete(request, tmp_path, live=False, send=send)
    assert first.origin == "test" and replay.origin == "replay" and len(sent) == 1
    with pytest.raises(ValueError, match="cache miss"):
        complete(replace(request, sample_id="experiment/run-2/sum/1"), tmp_path, live=False, send=send)
    with pytest.raises(ValueError, match="cache miss"):
        complete(replace(request, prompt="changed"), tmp_path, live=False, send=send)
    complete(replace(request, sample_id="experiment/run-2/sum/1"), tmp_path, live=True, send=send)
    assert len(sent) == 2


def test_missing_key_fails_without_network_or_secret_output(monkeypatch):
    from pfc.propose.llm import Request, Settings, send_request

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        send_request(Request("prompt", "run-1", Settings()))


@pytest.mark.parametrize("legacy", [False, True])
def test_openai_responses_wire_contract_and_sanitized_failure(monkeypatch, legacy):
    import httpx2 as httpx
    from openai import OpenAI

    from pfc.propose.llm import Request, Settings, send_request

    request = Request("curated prompt", "run-1", Settings.legacy() if legacy else Settings())

    def handler(wire):
        payload = json.loads(wire.content)
        assert payload["model"] == request.settings.model
        if legacy:
            assert payload["temperature"] == 0.2 and payload["reasoning"] == {"effort": "none"}
        else:
            assert payload["model"] == "gpt-6-astra"
            assert "temperature" not in payload and payload["reasoning"] == {"effort": "low"}
        assert payload["store"] is False and payload["text"]["format"]["strict"] is True
        assert not payload.get("tools")
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": request.settings.model,
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "annotations": [], "text": '{"invariants":["0<=i && i<=n"]}'}
                        ],
                    }
                ],
            },
        )

    with OpenAI(
        api_key="test-only", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))
    ) as client:
        exchange = send_request(request, client=client)
        assert exchange.model == request.settings.model and exchange.text.startswith("{")

    def denied(wire):
        return httpx.Response(401, json={"error": {"message": "SECRET-MUST-NOT-APPEAR", "type": "auth"}})

    with OpenAI(
        api_key="test-only", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(denied))
    ) as client:
        with pytest.raises(ValueError) as error:
            send_request(request, client=client)
        assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("text,model", [("", None), ("x" * 17000, None), ('{"invariants":["1"]}', "different-model")])
def test_invalid_service_exchange_is_not_cached(tmp_path, text, model):
    from pfc.propose.llm import Exchange, Request, Settings, complete

    request = Request("prompt", "run-1", Settings())
    with pytest.raises(ValueError):
        complete(
            request, tmp_path, live=True, send=lambda r: Exchange(text, model or r.settings.model, "id", 0, 0, "test")
        )
    assert not list(tmp_path.glob("*.json"))


def test_corrupt_cache_cannot_trigger_network(tmp_path):
    from pfc.propose.llm import Exchange, Request, Settings, complete

    request = Request("prompt", "run-1", Settings())
    complete(
        request,
        tmp_path,
        live=True,
        send=lambda r: Exchange('{"invariants":["0"]}', r.settings.model, "id", 0, 0, "test"),
    )
    path = next(tmp_path.glob("*.json"))
    path.write_text('{"request":null}')
    with pytest.raises(ValueError, match="cached"):
        complete(
            request, tmp_path, live=True, send=lambda r: pytest.fail("cache corruption must not cause a paid request")
        )


def test_cache_protocol_records_fixed_reasoning_and_schema_version():
    from dataclasses import asdict

    from pfc.propose.llm import Settings

    data = asdict(Settings())
    assert data["reasoning_effort"] == "low" and data["protocol"] == "pfc-invariants-v3"
    assert data["temperature"] is None
