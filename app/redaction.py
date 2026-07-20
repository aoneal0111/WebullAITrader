from __future__ import annotations


def redact_account_number(value: str) -> str:
    """Hide an account identifier while retaining at most its final four characters."""
    cleaned = value.strip()
    return f"****{cleaned[-4:]}" if cleaned else "****"
