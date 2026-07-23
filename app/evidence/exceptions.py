from __future__ import annotations


class EvidenceError(ValueError):
    """Base exception for evidence-related failures."""


class EvidenceValidationError(EvidenceError):
    """Raised when evidence contains invalid or unsafe values."""


class EvidenceCollectionError(EvidenceError):
    """Raised when an evidence provider fails during collection."""
