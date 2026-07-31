"""Validated Webull market-data streaming endpoint configuration."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


_SCHEME_DEFAULTS = {
    "mqtt": ("tcp", False, 1883),
    "mqtts": ("tcp", True, 1883),
    "ws": ("websockets", False, 80),
    "wss": ("websockets", True, 8883),
}


@dataclass(frozen=True, slots=True)
class WebullStreamEndpoint:
    configured_stream_url: str
    scheme: str
    mqtt_host: str
    mqtt_port: int
    transport: str
    tls_enable: bool
    websocket_path: str | None

    def __post_init__(self) -> None:
        expected = _SCHEME_DEFAULTS.get(self.scheme)
        if expected is None:
            raise ValueError(
                f"unsupported Webull stream URL scheme: {self.scheme or '<missing>'}"
            )
        expected_transport, expected_tls, _ = expected
        if self.transport != expected_transport:
            raise ValueError(
                f"{self.scheme} Webull stream URL requires "
                f"transport={expected_transport!r}, not {self.transport!r}"
            )
        if self.tls_enable is not expected_tls:
            raise ValueError(
                f"{self.scheme} Webull stream URL requires "
                f"tls_enable={expected_tls!r}"
            )
        if not self.mqtt_host:
            raise ValueError("Webull stream URL must include a hostname")
        if not isinstance(self.mqtt_port, int) or isinstance(self.mqtt_port, bool):
            raise ValueError("Webull stream URL port must be an integer")
        if not 1 <= self.mqtt_port <= 65535:
            raise ValueError("Webull stream URL port must be between 1 and 65535")
        if self.transport == "websockets":
            if not self.websocket_path or not self.websocket_path.startswith("/"):
                raise ValueError(
                    "Webull WebSocket path must start with '/'"
                )
        elif self.websocket_path is not None:
            raise ValueError(
                "TCP Webull stream URL must not include a WebSocket path"
            )

    @classmethod
    def parse(cls, value: str) -> "WebullStreamEndpoint":
        configured_url = str(value).strip()
        if not configured_url:
            raise ValueError("Webull stream URL must not be blank")

        parsed = urlparse(configured_url)
        scheme = parsed.scheme.lower()
        defaults = _SCHEME_DEFAULTS.get(scheme)
        if defaults is None:
            raise ValueError(
                f"unsupported Webull stream URL scheme: {scheme or '<missing>'}"
            )
        if parsed.hostname is None:
            raise ValueError("Webull stream URL must include a hostname")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Webull stream URL must not include credentials")
        if parsed.params or parsed.query or parsed.fragment:
            raise ValueError(
                "Webull stream URL must not include parameters, query, or fragment"
            )

        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"invalid Webull stream URL port: {exc}") from exc

        transport, tls_enable, default_port = defaults
        if transport == "websockets":
            websocket_path = parsed.path or "/mqtt"
        else:
            if parsed.path:
                raise ValueError(
                    "TCP Webull stream URL must not include a WebSocket path"
                )
            websocket_path = None

        return cls(
            configured_stream_url=configured_url,
            scheme=scheme,
            mqtt_host=parsed.hostname,
            mqtt_port=default_port if port is None else port,
            transport=transport,
            tls_enable=tls_enable,
            websocket_path=websocket_path,
        )


def parse_webull_stream_url(value: str) -> WebullStreamEndpoint:
    return WebullStreamEndpoint.parse(value)


__all__ = ["WebullStreamEndpoint", "parse_webull_stream_url"]
