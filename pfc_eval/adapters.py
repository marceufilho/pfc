"""Evaluation-only service boundary for the proposer conditions (roadmap E10, architecture §3.1/§3.2).

The frozen `pfc.propose.llm` adapter accepts only the two pinned curated protocols. The evaluation needs
other models, sampling settings and an OpenAI-compatible base URL (condition L3), so this module carries
its own independently recorded `Settings`, the same wire contract (Responses API, strict JSON schema, one
attempt, no SDK retries) and the same cache discipline (request-bound files, replay never counts as
sampling). The key comes only from the environment; nothing here grants proof authority.
"""

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

from openai import APIError, Omit, OpenAI, omit
from openai.types.shared import ReasoningEffort
from openai.types.shared_params import Reasoning

from pfc.config import MAX_PROMPT_BYTES, MAX_PROPOSAL_BYTES, OPENAI_KEY_ENV, PROPOSER_TIMEOUT_SECONDS
from pfc.digest import sha256_text

PROTOCOL = "pfc-eval-proposer-v3"  # v1 frozen wording; v2 names proof goals; v3 program-specific overflow caveat
OPENAI_BASE_URL = "https://api.openai.com/v1"
EFFORTS = (None, "none", "low", "medium", "high")
Effort = str | None
Origin = Literal["live", "replay", "test"]


@dataclass(frozen=True)
class Settings:
    """Model identity and sampling settings, recorded verbatim in evidence; no curated pin applies."""

    model: str
    temperature: float | None = None
    max_output_tokens: int = 2048
    reasoning_effort: Effort = "low"
    base_url: str = OPENAI_BASE_URL
    protocol: str = PROTOCOL

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", self.model):
            raise ValueError("model identifier must be a short printable name")
        if self.temperature is not None and not (
            isinstance(self.temperature, (int, float)) and 0.0 <= float(self.temperature) <= 2.0
        ):
            raise ValueError("temperature must be omitted or lie in [0, 2]")
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 32768:
            raise ValueError("max_output_tokens must be an integer in 1..32768")
        if self.reasoning_effort not in EFFORTS:
            raise ValueError("reasoning_effort must be omitted or one of none, low, medium, high")
        parts = urlsplit(self.base_url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.netloc
            or parts.query
            or parts.fragment
            or "@" in parts.netloc
        ):
            raise ValueError("base_url must be a plain http(s) URL without credentials or query")
        if not self.protocol:
            raise ValueError("protocol label required")

    @classmethod
    def default(cls) -> "Settings":
        """Mirrors the curated pfc-invariants-v3 sampling (gpt-6-astra, low effort, temperature omitted)."""
        return cls("gpt-6-astra", None, 2048, "low")

    def snapshot_status(self) -> str:
        return "dated" if re.search(r"\d{4}-\d{2}-\d{2}$", self.model) else "undated"


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
    reasoning_tokens: int
    latency_ms: float
    origin: Origin


def describe(settings: Settings) -> dict[str, object]:
    return {**asdict(settings), "snapshot_status": settings.snapshot_status()}


def validate(exchange: Exchange, settings: Settings) -> None:
    if (
        not isinstance(exchange.text, str)
        or not exchange.text.strip()
        or len(exchange.text.encode("utf-8")) > MAX_PROPOSAL_BYTES
        or exchange.model != settings.model
        or not isinstance(exchange.response_id, str)
        or not exchange.response_id
        or exchange.origin not in ("live", "replay", "test")
        or any(
            type(v) is not int or v < 0
            for v in (exchange.input_tokens, exchange.output_tokens, exchange.reasoning_tokens)
        )
        or not isinstance(exchange.latency_ms, (int, float))
        or exchange.latency_ms < 0
    ):
        raise ValueError("invalid, incomplete or mismatched model response")


def _key() -> str:
    key = os.environ.get(OPENAI_KEY_ENV)
    if not key or not key.strip() or any(c.isspace() for c in key):
        raise ValueError(f"configure {OPENAI_KEY_ENV} in the environment; never pass the key as an argument")
    return key


def send(request: Request, *, client: OpenAI | None = None) -> Exchange:
    """Exactly one HTTP attempt against the settings' base URL; failures are sanitized."""
    settings = request.settings
    if client is None:
        with OpenAI(
            api_key=_key(), base_url=settings.base_url, max_retries=0, timeout=PROPOSER_TIMEOUT_SECONDS
        ) as owned:
            return send(request, client=owned)
    reasoning: Reasoning | Omit = omit
    if settings.reasoning_effort is not None:
        reasoning = Reasoning(effort=cast(ReasoningEffort, settings.reasoning_effort))
    started = time.monotonic()
    try:
        response = client.responses.create(
            model=settings.model,
            input=request.prompt,
            store=False,
            temperature=settings.temperature if settings.temperature is not None else omit,
            reasoning=reasoning,
            max_output_tokens=settings.max_output_tokens,
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
        latency = (time.monotonic() - started) * 1000.0
        if response.status != "completed":
            raise ValueError("response was incomplete or refused")
        usage = response.usage
        details = getattr(usage, "output_tokens_details", None) if usage else None
        exchange = Exchange(
            response.output_text,
            response.model,
            response.id,
            usage.input_tokens if usage else 0,
            usage.output_tokens if usage else 0,
            int(getattr(details, "reasoning_tokens", 0) or 0) if details else 0,
            round(latency, 3),
            "live",
        )
        validate(exchange, settings)
        return exchange
    except (AttributeError, TypeError):
        raise ValueError("malformed service response") from None
    except APIError as error:
        # SDK messages can carry response bodies, prompts or credentials; keep only the class name.
        raise ValueError(
            f"service request failed ({type(error).__name__}); inspect endpoint access and limits"
        ) from None


def complete(request: Request, cache: Path, *, live: bool, send: Callable[[Request], Exchange] = send) -> Exchange:
    """Request-bound cache: a hit replays with origin `replay`; a miss needs `live`."""
    canonical = json.dumps(asdict(request), sort_keys=True, separators=(",", ":"))
    path = cache / f"{sha256_text(canonical)}.json"
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
