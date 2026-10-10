"""Engine identities are independent of broker asset classes and GUI routes."""
from enum import StrEnum


class EngineId(StrEnum):
    WARRIOR = "WARRIOR_MOMENTUM_V1"
    SCALPER = "QUICK_SCALPER"
    CRYPTO = "CRYPTO"
    OPTIONS = "OPTIONS"
    FUTURES = "FUTURES"


def lifecycle_owner(lifecycle_id: str | None) -> EngineId | None:
    prefix, separator, remainder = (lifecycle_id or "").partition("|")
    if not separator or not remainder:
        return None
    try:
        return EngineId(prefix)
    except ValueError:
        return None


# Readiness describes current implementation, not an execution permission.
ENGINE_READINESS = {
    EngineId.WARRIOR: "Shared equity runtime; strategy orders identified by lifecycle",
    EngineId.SCALPER: "Shared equity runtime; strategy orders identified by lifecycle",
    EngineId.CRYPTO: "Separate spot paper supervisor; broker execution unavailable",
    EngineId.OPTIONS: "Planning only; contract quote and execution adapter required",
    EngineId.FUTURES: "Planning only; contract quote and execution adapter required",
}
