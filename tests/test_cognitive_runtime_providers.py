"""Coverage for the Cognitive Runtime providers.

The NIM provider is tested against a mock transport, so no network or key is
needed; the fallback and circuit breaker are tested with deterministic fakes and
an injected clock. A live NIM call stays a manual smoke, like SITL evidence.
"""

import json
import unittest

import httpx

from brain.cognitive_runtime import (
    AllProvidersFailedError,
    CircuitBreaker,
    FallbackProvider,
    NIMProvider,
    ProviderError,
    ProviderResponse,
)


class _FakeProvider:
    def __init__(self, name, *, fail=False, response=None):
        self.name = name
        self._fail = fail
        self._response = response or ProviderResponse(
            content="ok", tool_calls=(), model=name, input_tokens=1, output_tokens=1
        )
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self._fail:
            raise ProviderError(f"{self.name} down")
        return self._response


def _nim_with(handler) -> NIMProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return NIMProvider(model="test-model", api_key="k", client=client)


class NIMProviderTests(unittest.TestCase):
    def test_parses_text_and_usage(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "model": "served-model",
                    "choices": [{"message": {"content": "hello", "tool_calls": []}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                },
            )

        response = _nim_with(handler).complete([{"role": "user", "content": "hi"}], [])
        self.assertEqual(response.content, "hello")
        self.assertEqual(response.model, "served-model")
        self.assertEqual((response.input_tokens, response.output_tokens), (12, 3))

    def test_parses_tool_calls(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "function": {
                                            "name": "telemetry.read",
                                            "arguments": json.dumps({"detail": True}),
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                },
            )

        response = _nim_with(handler).complete([], [])
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].capability_id, "telemetry.read")
        self.assertEqual(response.tool_calls[0].arguments, {"detail": True})

    def test_http_error_raises_provider_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        with self.assertRaises(ProviderError):
            _nim_with(handler).complete([], [])

    def test_from_env_refuses_without_key(self) -> None:
        with self.assertRaises(ProviderError):
            NIMProvider.from_env({"NIM_MISSION_MODEL": "m"})


class FallbackTests(unittest.TestCase):
    def test_falls_through_to_the_first_healthy_provider(self) -> None:
        down = _FakeProvider("primary", fail=True)
        up = _FakeProvider("secondary")
        fallback = FallbackProvider([down, up])
        response = fallback.complete([], [])
        self.assertEqual(response.model, "secondary")
        self.assertEqual(fallback.served_by, "secondary")

    def test_all_failing_raises(self) -> None:
        fallback = FallbackProvider([_FakeProvider("a", fail=True), _FakeProvider("b", fail=True)])
        with self.assertRaises(AllProvidersFailedError):
            fallback.complete([], [])

    def test_open_breaker_skips_a_provider_without_calling_it(self) -> None:
        clock = [1000.0]
        primary = _FakeProvider("primary", fail=True)
        secondary = _FakeProvider("secondary")
        fallback = FallbackProvider(
            [primary, secondary],
            breaker_factory=lambda: CircuitBreaker(failure_threshold=1, cooldown_s=60, clock=lambda: clock[0]),
        )
        fallback.complete([], [])  # primary fails once -> breaker opens (threshold 1)
        calls_before = primary.calls
        fallback.complete([], [])  # primary should be skipped now
        self.assertEqual(primary.calls, calls_before)
        self.assertEqual(fallback.served_by, "secondary")


class RetryTests(unittest.TestCase):
    def test_retries_a_transient_failure_then_succeeds(self) -> None:
        from brain.cognitive_runtime import RetryingProvider

        class _Flaky:
            name = "flaky"

            def __init__(self):
                self.calls = 0

            def complete(self, messages, tools):
                self.calls += 1
                if self.calls < 2:
                    raise ProviderError("transient")
                return ProviderResponse(content="ok", tool_calls=(), model="flaky",
                                        input_tokens=1, output_tokens=1)

        flaky = _Flaky()
        provider = RetryingProvider(flaky, attempts=3, sleep=lambda _s: None)
        response = provider.complete([], [])
        self.assertEqual(response.content, "ok")
        self.assertEqual(flaky.calls, 2)

    def test_gives_up_after_attempts_and_raises(self) -> None:
        from brain.cognitive_runtime import RetryingProvider

        class _Dead:
            name = "dead"

            def complete(self, messages, tools):
                raise ProviderError("down")

        provider = RetryingProvider(_Dead(), attempts=2, sleep=lambda _s: None)
        with self.assertRaises(ProviderError):
            provider.complete([], [])


class CircuitBreakerTests(unittest.TestCase):
    def test_opens_after_threshold_and_recovers_after_cooldown(self) -> None:
        now = [0.0]
        breaker = CircuitBreaker(failure_threshold=2, cooldown_s=30, clock=lambda: now[0])
        self.assertTrue(breaker.is_available())
        breaker.record_failure()
        self.assertTrue(breaker.is_available())  # one failure is not enough
        breaker.record_failure()
        self.assertFalse(breaker.is_available())  # open
        now[0] = 31.0
        self.assertTrue(breaker.is_available())  # half-open after cooldown
        breaker.record_success()
        self.assertTrue(breaker.is_available())


if __name__ == "__main__":
    unittest.main()
