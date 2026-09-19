"""Bounded shared inference HTTP: no redirects, prompt logging or disk payloads."""

from __future__ import annotations

import hashlib
import json
import math
import random
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from printstash_core.inference import EmbeddingError
from printstash_core.inference.context import InferenceContext

from app.modules.inference import http_runtime
from app.modules.inference.endpoint import EndpointConfig

MAX_RESPONSE_BYTES = 2 * 1024**2
MAX_REQUEST_BYTES = 1024**2
MAX_ATTEMPTS = 3
_condition = threading.Condition()
_active = 0
_interactive_waiting = 0


class EndpointError(EmbeddingError):
    def __init__(
        self, code: str, *, status: int | None = None, unsupported: bool = False
    ):
        super().__init__(code)
        self.status = status
        self.unsupported = unsupported


@dataclass
class Circuit:
    failures: int = 0
    opened_until: float = 0
    probing: bool = False
    last_used: float = 0


_circuits: dict[str, Circuit] = {}


def close_client() -> None:
    http_runtime.close()
    with _condition:
        _circuits.clear()


@contextmanager
def _admit(context: InferenceContext):
    global _active, _interactive_waiting
    interactive = context.priority == "interactive"
    with _condition:
        if interactive:
            _interactive_waiting += 1
        try:
            while _active >= 4 or (not interactive and _interactive_waiting):
                _condition.wait(timeout=min(0.05, context.remaining()))
            context.remaining()
            _active += 1
        finally:
            if interactive:
                _interactive_waiting -= 1
    try:
        yield
    finally:
        with _condition:
            _active -= 1
            _condition.notify_all()


def _enter_circuit(endpoint: str) -> Circuit:
    key = hashlib.sha256(endpoint.encode()).hexdigest()
    with _condition:
        now = time.monotonic()
        if key not in _circuits:
            if len(_circuits) >= 64:
                oldest = min(_circuits, key=lambda item: _circuits[item].last_used)
                del _circuits[oldest]
            _circuits[key] = Circuit()
        circuit = _circuits[key]
        circuit.last_used = now
        if circuit.opened_until > now or circuit.probing:
            raise EndpointError("inference_circuit_open")
        if circuit.failures >= 3:
            circuit.probing = True
        return circuit


def _finish_circuit(circuit: Circuit, *, failed: bool) -> None:
    with _condition:
        circuit.probing = False
        if failed:
            circuit.failures += 1
            if circuit.failures >= 3:
                circuit.opened_until = time.monotonic() + 30
        else:
            circuit.failures = 0
            circuit.opened_until = 0


def _wait(delay: float, context: InferenceContext) -> None:
    until = time.monotonic() + delay
    while (remaining := until - time.monotonic()) > 0:
        time.sleep(min(0.05, remaining, context.remaining()))


def _retry_delay(value: str | None, attempt: int) -> float:
    if value:
        try:
            return min(2, max(0, float(value)))
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                return min(
                    2, max(0, (date - datetime.now(timezone.utc)).total_seconds())
                )
            except (ValueError, TypeError, OverflowError):
                pass
    return min(2, 0.1 * 2**attempt + random.uniform(0, 0.05))


def _json(raw: bytes) -> dict:
    def reject_constant(_value):
        raise ValueError("nonfinite JSON")

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON")
        return number

    try:
        body = json.loads(raw, parse_constant=reject_constant, parse_float=finite)
    except (ValueError, UnicodeError, RecursionError):
        raise EndpointError("inference_invalid_json") from None
    if not isinstance(body, dict):
        raise EndpointError("inference_invalid_json")
    return body


def _unsupported(body: dict) -> bool:
    error = body.get("error")
    if not isinstance(error, dict):
        return False
    return error.get("code") in {
        "unsupported_parameter",
        "unsupported_response_format",
        "unsupported_feature",
    } and error.get("param") in {
        "response_format",
        "tools",
        "tool_choice",
        "json_schema",
    }


def post_json(
    config: EndpointConfig,
    path: str,
    payload: dict,
    *,
    context: InferenceContext | None = None,
) -> dict:
    """One bounded operation, retrying explicit 429/5xx only.

    Ambiguous network/read timeouts are never retried; in particular they cannot
    turn a chat dialect probe into a second paid completion via another dialect.
    Callers receive stable error codes, never endpoint bodies or headers.
    """
    if path not in {"embeddings", "chat/completions", "responses"}:
        raise EndpointError("inference_operation_invalid")
    context = context or InferenceContext.bounded(config.timeout_seconds)
    context = InferenceContext(
        min(context.deadline, time.monotonic() + config.timeout_seconds),
        context.cancelled,
        context.priority,
    )
    request_bytes = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
    if len(request_bytes) > MAX_REQUEST_BYTES:
        raise EndpointError("inference_request_too_large")
    with _admit(context):
        circuit = _enter_circuit(config.base_url)
        retryable_failure = False
        try:
            for attempt in range(MAX_ATTEMPTS):
                context.remaining()
                try:
                    status, headers, raw = http_runtime.request(
                        config.base_url + "/" + path,
                        request_bytes,
                        config.request_headers(),
                        context,
                        MAX_RESPONSE_BYTES,
                    )
                    context.remaining()
                    if 300 <= status < 400:
                        raise EndpointError("inference_redirect_refused", status=status)
                    if status == 429 or status >= 500:
                        retryable_failure = True
                        if attempt < MAX_ATTEMPTS - 1:
                            _wait(
                                _retry_delay(headers.get("retry-after"), attempt),
                                context,
                            )
                            continue
                        raise EndpointError(
                            "inference_rate_limited"
                            if status == 429
                            else "inference_endpoint_failed",
                            status=status,
                        )
                    if status >= 400:
                        try:
                            body = _json(raw)
                        except EndpointError:
                            body = {}
                        raise EndpointError(
                            "inference_request_rejected",
                            status=status,
                            unsupported=_unsupported(body),
                        )
                    retryable_failure = False
                    return _json(raw)
                except (httpx.TimeoutException, TimeoutError):
                    retryable_failure = True
                    raise EndpointError("inference_timeout") from None
                except httpx.HTTPError:
                    retryable_failure = True
                    raise EndpointError("inference_network_unavailable") from None
                except ValueError as exc:
                    if isinstance(exc, EmbeddingError):
                        raise
                    code = str(exc)
                    if code not in {
                        "inference_response_encoding",
                        "inference_response_too_large",
                    }:
                        code = "inference_response_invalid"
                    raise EndpointError(code) from None
            raise EndpointError("inference_endpoint_failed")
        finally:
            _finish_circuit(circuit, failed=retryable_failure)
