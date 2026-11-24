"""Single OpenAI boundary. Response caches are untrusted data, never proof caches."""

import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from openai import APIError, OpenAI, omit

from pfc.config import (
    MAX_PROMPT_BYTES,
    MAX_PROPOSAL_BYTES,
    OPENAI_KEY_ENV,
    PROPOSER_MODEL,
    PROPOSER_OUTPUT_TOKENS,
    PROPOSER_TEMPERATURE,
    PROPOSER_TIMEOUT_SECONDS,
)
from pfc.digest import sha256_text


@dataclass(frozen=True)
class Settings:
    model: str = PROPOSER_MODEL
    temperature: float | None = PROPOSER_TEMPERATURE
    max_output_tokens: int = PROPOSER_OUTPUT_TOKENS
    reasoning_effort: Literal["none", "low"] = "low"
    protocol: str = "pfc-invariants-v3"

    def __post_init__(self) -> None:
        current = (PROPOSER_MODEL, None, "low", "pfc-invariants-v3")
        historical = ("gpt-5.4-2026-03-05", 0.2, "none", "pfc-invariants-v2")
        identity = (self.model, self.temperature, self.reasoning_effort, self.protocol)
        if (
            identity not in (current, historical)
            or type(self.max_output_tokens) is not int
            or self.max_output_tokens != PROPOSER_OUTPUT_TOKENS
        ):
            raise ValueError("experiment settings must match the pinned protocol")

    @classmethod
    def legacy(cls) -> "Settings":
        """Keep the already sampled v2 protocol readable without silently migrating it."""
        return cls("gpt-5.4-2026-03-05", 0.2, 2048, "none", "pfc-invariants-v2")

    @classmethod
    def from_data(cls, data: object) -> "Settings":
        """Accept only complete, known configurations; archived data cannot select arbitrary models."""
        for settings in (cls(), cls.legacy()):
            payload = asdict(settings)
            if data == payload or data == {"node": "Settings", **payload}:
                # Dict equality alone would also accept 2048.0 as the integer token budget.
                if isinstance(data, dict) and type(data.get("max_output_tokens")) is int:
                    return settings
        raise ValueError("experiment settings must match the pinned protocol")


@dataclass(frozen=True)
class Request:
    prompt: str
    sample_id: str
    settings: Settings

    def __post_init__(self) -> None:
        if not self.prompt or len(self.prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ValueError("empty or oversized proposal prompt")
        if not re.fullmatch(r"[a-zA-Z0-9_/-]{1,128}", self.sample_id):
            raise ValueError("invalid sampling identity")


@dataclass(frozen=True)
class Exchange:
    text: str
    model: str
    response_id: str
    input_tokens: int
    output_tokens: int
    origin: Literal["live", "replay", "test"]


def validate(exchange: Exchange, settings: Settings) -> None:
    if (
        not isinstance(exchange.text, str)
        or not exchange.text.strip()
        or len(exchange.text.encode("utf-8")) > MAX_PROPOSAL_BYTES
        or exchange.model != settings.model
        or not isinstance(exchange.response_id, str)
        or not exchange.response_id
        or not isinstance(exchange.origin, str)
        or exchange.origin not in {"live", "replay", "test"}
        or any(type(v) is not int or v < 0 for v in (exchange.input_tokens, exchange.output_tokens))
    ):
        raise ValueError("invalid, incomplete or mismatched model response")


def send_request(request: Request, *, client: OpenAI | None = None) -> Exchange:
    """Exactly one HTTP attempt; failures consume the outer five-attempt budget."""
    if client is None:
        key = os.environ.get(OPENAI_KEY_ENV)
        if not key or not key.strip() or any(c.isspace() for c in key):
            raise ValueError("configure OPENAI_API_KEY in the environment; never pass it as an argument")
        with OpenAI(
            api_key=key, base_url="https://api.openai.com/v1", max_retries=0, timeout=PROPOSER_TIMEOUT_SECONDS
        ) as owned:
            return send_request(request, client=owned)
    try:
        response = client.responses.create(
            model=request.settings.model,
            input=request.prompt,
            store=False,
            temperature=request.settings.temperature if request.settings.temperature is not None else omit,
            reasoning={"effort": request.settings.reasoning_effort},
            max_output_tokens=request.settings.max_output_tokens,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "loop_invariants",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"invariants": {"type": "array", "items": {"type": "string"}}},
                        "required": ["invariants"],
                        "additionalProperties": False,
                    },
                }
            },
        )
        if response.status != "completed":
            raise ValueError("OpenAI response was incomplete or refused")
        exchange = Exchange(
            response.output_text,
            response.model,
            response.id,
            response.usage.input_tokens if response.usage else 0,
            response.usage.output_tokens if response.usage else 0,
            "live",
        )
        validate(exchange, request.settings)
        return exchange
    except (AttributeError, TypeError):
        raise ValueError("malformed OpenAI response") from None
    except APIError as error:
        # SDK exception messages can contain response bodies, prompts or credentials.
        raise ValueError(f"OpenAI request failed ({type(error).__name__}); inspect account access and limits") from None


def complete(
    request: Request, cache: Path, *, live: bool, send: Callable[[Request], Exchange] = send_request
) -> Exchange:
    canonical = json.dumps(asdict(request), sort_keys=True, separators=(",", ":"))
    identity = sha256_text(canonical)
    path = cache / f"{identity}.json"
    if path.exists():
        try:
            if path.is_symlink() or path.stat().st_size > MAX_PROMPT_BYTES + 2 * MAX_PROPOSAL_BYTES:
                raise ValueError("invalid cached exchange")
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("request") != asdict(request):
                raise ValueError("cached request does not match")
            exchange = Exchange(**data["exchange"])
            validate(exchange, request.settings)
            return replace(exchange, origin="replay")
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise ValueError("cannot validate cached exchange") from error
    if not live:
        raise ValueError("cache miss; network is disabled without --live")
    exchange = send(request)
    validate(exchange, request.settings)
    cache.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump({"request": asdict(request), "exchange": asdict(exchange)}, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return exchange
