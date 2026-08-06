"""Safe, deterministic Webull streaming diagnostic configurations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from app.webull.sdk_streaming_adapter import MQTT_V311


MQTT_V5 = 5


@dataclass(frozen=True, slots=True)
class StreamingDiagnosticCase:
    name: str
    mqtt_protocol: int
    transport: str
    tls_enable: bool
    mqtt_port: int
    websocket_path: str | None
    sdk_supported: bool


DIAGNOSTIC_MATRIX = (
    StreamingDiagnosticCase(
        "sdk-default", MQTT_V311, "tcp", True, 1883, None, True
    ),
    StreamingDiagnosticCase(
        "mqtt311-websocket", MQTT_V311, "websockets", True, 8883,
        "/mqtt", False,
    ),
    StreamingDiagnosticCase(
        "mqtt5-websocket", MQTT_V5, "websockets", True, 8883,
        "/mqtt", False,
    ),
    StreamingDiagnosticCase(
        "mqtt311-raw-tls", MQTT_V311, "tcp", True, 1883, None, True
    ),
    StreamingDiagnosticCase(
        "documented-sdk-production", MQTT_V311, "tcp", True, 1883,
        None, True,
    ),
)


def run_until_conclusive(
    probe: Callable[[StreamingDiagnosticCase, str], str],
    *,
    symbol: str = "AAPL",
    cases: Iterable[StreamingDiagnosticCase] = DIAGNOSTIC_MATRIX,
) -> tuple[tuple[str, str], ...]:
    """Probe one symbol sequentially and stop after the first accepted case."""

    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("symbol must not be blank")
    results: list[tuple[str, str]] = []
    for case in cases:
        result = str(probe(case, normalized)).strip().upper()
        results.append((case.name, result))
        if result in {"CONNACK_ACCEPTED", "PAYLOAD_RECEIVED"}:
            break
    return tuple(results)


__all__ = [
    "DIAGNOSTIC_MATRIX",
    "MQTT_V5",
    "StreamingDiagnosticCase",
    "run_until_conclusive",
]
