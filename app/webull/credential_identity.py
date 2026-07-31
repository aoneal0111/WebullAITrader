"""Safe, non-reversible identity for credential-scoped runtime state."""

from __future__ import annotations

from hashlib import sha256


def credential_fingerprint(app_key: str, app_secret: str) -> str:
    if not app_key.strip() or not app_secret.strip():
        return "fp_missing"
    digest = sha256(
        b"atlas-webull-credential-v1\0"
        + app_key.encode("utf-8")
        + b"\0"
        + app_secret.encode("utf-8")
    ).hexdigest()
    return f"fp_{digest[:12]}"


__all__ = ["credential_fingerprint"]
