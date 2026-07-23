"""Model providers for the Cognitive Runtime: a provider protocol, an
OpenAI-compatible NVIDIA NIM provider, and a fallback wrapper with a per-provider
circuit breaker.

The runtime talks to models only through ``Provider.complete``. A ``NIMProvider``
calls NIM's OpenAI-compatible chat-completions endpoint over httpx. A
``FallbackProvider`` tries an ordered list of providers, skipping any whose
circuit breaker is open, so a single provider's outage degrades to a fallback or
a clean failure rather than a hang or a crash.

Nothing here reaches the flight stack: a provider returns text and tool-call
requests; whether a tool may run, and whether a flight may be drafted, is decided
downstream by the ToolPolicy and the safety gate.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import time
from typing import Any, Protocol

import httpx


class ProviderError(RuntimeError):
    """Raised when a provider cannot produce a completion."""


class AllProvidersFailedError(ProviderError):
    """Raised when every provider in a fallback chain failed or was open."""


@dataclass(frozen=True)
class ToolCall:
    """A model's request to call one tool, as the runtime will dispatch it."""

    call_id: str
    capability_id: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ProviderResponse:
    """One model completion: text, any tool calls, the model id and token usage."""

    content: str | None
    tool_calls: tuple[ToolCall, ...]
    model: str
    input_tokens: int
    output_tokens: int


class Provider(Protocol):
    """A model backend the runtime can call. ``name`` labels it for audit."""

    name: str

    def complete(
        self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]
    ) -> ProviderResponse: ...


# -- NVIDIA NIM (OpenAI-compatible) ---------------------------------------


@dataclass
class NIMProvider:
    """Calls NIM's OpenAI-compatible /chat/completions endpoint over httpx."""

    model: str
    api_key: str
    base_url: str = "https://integrate.api.nvidia.com/v1"
    name: str = "nim-primary"
    timeout_s: float = 30.0
    client: httpx.Client | None = None

    def complete(
        self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]
    ) -> ProviderResponse:
        payload: dict[str, Any] = {"model": self.model, "messages": list(messages)}
        if tools:
            payload["tools"] = list(tools)
        client = self.client or httpx.Client(timeout=self.timeout_s)
        try:
            response = client.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.HTTPError as error:
            raise ProviderError(f"NIM request failed: {error}") from error
        finally:
            if self.client is None:
                client.close()
        if response.status_code >= 400:
            raise ProviderError(f"NIM returned HTTP {response.status_code}.")
        return _parse_openai_response(response.json(), fallback_model=self.model)

    @classmethod
    def from_env(cls, env: dict[str, str], client: httpx.Client | None = None) -> NIMProvider:
        """Build a NIM provider from the environment, refusing without a key."""
        api_key = env.get("NVIDIA_API_KEY", "").strip()
        model = env.get("NIM_MISSION_MODEL", "").strip()
        if not api_key or not model:
            raise ProviderError("NVIDIA_API_KEY and NIM_MISSION_MODEL must both be set.")
        base_url = env.get("NIM_BASE_URL", "").strip() or cls.base_url
        return cls(model=model, api_key=api_key, base_url=base_url, client=client)


def _parse_openai_response(body: dict[str, Any], fallback_model: str) -> ProviderResponse:
    try:
        choice = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as error:
        raise ProviderError("NIM response had no message choice.") from error
    tool_calls = tuple(_parse_tool_call(call) for call in choice.get("tool_calls") or [])
    usage = body.get("usage") or {}
    return ProviderResponse(
        content=choice.get("content"),
        tool_calls=tool_calls,
        model=body.get("model", fallback_model),
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
    )


def _parse_tool_call(call: dict[str, Any]) -> ToolCall:
    import json

    function = call.get("function") or {}
    raw_args = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
    except (ValueError, TypeError):
        arguments = {}
    return ToolCall(
        call_id=str(call.get("id", "")),
        capability_id=str(function.get("name", "")),
        arguments=arguments if isinstance(arguments, dict) else {},
    )


# -- circuit breaker + fallback -------------------------------------------


@dataclass
class CircuitBreaker:
    """Opens after consecutive failures, then half-opens after a cooldown.

    While open, a provider is skipped without being called, so a dead backend
    stops costing latency on every turn. One success closes it again.
    """

    failure_threshold: int = 3
    cooldown_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    _failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    def is_available(self) -> bool:
        if self._opened_at is None:
            return True
        if self.clock() - self._opened_at >= self.cooldown_s:
            return True  # half-open: allow a probe
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self.clock()

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None and not self.is_available()


@dataclass
class RetryingProvider:
    """Retries a single provider on transient failure before giving up.

    Retry is for a flaky call to one backend; failover to a different backend is
    the FallbackProvider's job. Compose them: fall back over retrying providers.
    The sleep function is injected so tests do not actually wait.
    """

    provider: Provider
    attempts: int = 2
    sleep: Callable[[float], None] = time.sleep
    backoff_s: float = 0.5

    @property
    def name(self) -> str:
        return self.provider.name

    @property
    def served_by(self) -> str | None:
        return getattr(self.provider, "served_by", None)

    def complete(
        self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]
    ) -> ProviderResponse:
        last: ProviderError | None = None
        for attempt in range(max(1, self.attempts)):
            try:
                return self.provider.complete(messages, tools)
            except ProviderError as error:
                last = error
                if attempt + 1 < self.attempts:
                    self.sleep(self.backoff_s * (attempt + 1))
        raise last if last is not None else ProviderError("no attempts made")


@dataclass
class _Backend:
    provider: Provider
    breaker: CircuitBreaker


@dataclass
class FallbackProvider:
    """Tries providers in order, skipping open breakers, until one answers."""

    name: str = "fallback"

    def __init__(
        self,
        providers: Sequence[Provider],
        breaker_factory: Callable[[], CircuitBreaker] = CircuitBreaker,
    ) -> None:
        if not providers:
            raise ProviderError("A fallback chain needs at least one provider.")
        self._backends = [_Backend(provider, breaker_factory()) for provider in providers]
        self.name = "fallback"
        self.served_by: str | None = None

    def complete(
        self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]
    ) -> ProviderResponse:
        errors: list[str] = []
        for backend in self._backends:
            if not backend.breaker.is_available():
                errors.append(f"{backend.provider.name}: circuit open")
                continue
            try:
                response = backend.provider.complete(messages, tools)
            except ProviderError as error:
                backend.breaker.record_failure()
                errors.append(f"{backend.provider.name}: {error}")
                continue
            backend.breaker.record_success()
            self.served_by = backend.provider.name
            return response
        raise AllProvidersFailedError("; ".join(errors) or "no providers available")
