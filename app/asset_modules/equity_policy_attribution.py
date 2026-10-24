"""Passive, startup-frozen equity policy attribution; no trading authority."""
from hashlib import sha256
import json
from pathlib import Path
import re

from app.asset_modules.engine_catalog import EngineId

FIELDS = ("strategy_family", "strategy_policy_version", "strategy_code_fingerprint",
          "strategy_configuration_fingerprint", "strategy_attribution_scope")

# Explicit source scope: deliberately not presented as a full repository hash.
SHARED = (
    "asset_modules/equity_policy_attribution.py", "asset_modules/controlled_paper_service.py",
    "asset_modules/paper_controller_router.py", "asset_modules/admission_controller.py",
    "strategies/warrior_momentum/autonomous_paper.py", "strategies/warrior_momentum/configuration.py",
    "strategies/warrior_momentum/opportunity_engine.py", "paper_gateway/gateway.py",
    "composition/desktop.py", "strategies/warrior_momentum/desktop_sidecar.py",
    "services/trading_service.py",
    "market/scalp_session.py",
)
SOURCE_FILES = {
    EngineId.WARRIOR.value: SHARED + (
        "strategies/warrior_momentum/forward_runtime.py", "strategies/warrior_momentum/models.py",
        "strategies/warrior_momentum/setups.py", "strategies/warrior_momentum/features.py",
        "strategies/warrior_momentum/runtime.py", "strategies/warrior_momentum/risk.py",
        "strategies/warrior_momentum/trade_management.py",
    ),
    EngineId.SCALPER.value: SHARED + (
        "strategies/warrior_momentum/quick_scalper.py",
        "strategies/warrior_momentum/quick_scalper_runtime.py",
    ),
}


def source_fingerprint(root, paths):
    manifest = [(p, sha256((Path(root)/p).read_bytes().replace(b"\r\n", b"\n")).hexdigest())
                for p in sorted(paths)]
    return sha256(json.dumps(manifest, separators=(",", ":")).encode()).hexdigest()


def family(signal, engine):
    if engine == EngineId.SCALPER.value:
        setup = getattr(signal, "setup_type", None)
        return setup if setup in {"QUICK_SCALP_STREAM", "QUICK_SCALP_MOMENTUM"} else "QUICK_SCALP_MOMENTUM"
    primary = getattr(signal, "taxonomy_strategy_id", None)
    if primary:
        value = getattr(primary, "value", primary)
    else:
        value = getattr(signal, "setup_type", None)
        value = getattr(value, "value", value)
    # Never choose an arbitrary overlapping taxonomy membership as primary.
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", value) else "UNKNOWN"


class EquityPolicyAttribution:
    def __init__(self, configuration_fingerprints, *, source_root=None):
        root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[1]
        self._configuration = dict(configuration_fingerprints)
        self._code = {}
        for engine, files in SOURCE_FILES.items():
            try:
                self._code[engine] = source_fingerprint(root, files)
            except OSError:
                self._code[engine] = "UNKNOWN"

    def __call__(self, signal):
        owner = str(getattr(signal, "strategy_owner", None) or getattr(signal, "strategy_id", ""))
        engine = EngineId.WARRIOR.value if owner.startswith("WARRIOR_MOMENTUM") else owner
        if engine not in SOURCE_FILES:
            return {}
        setup = family(signal, engine)
        code, config = self._code[engine], self._configuration.get(engine, "UNKNOWN")
        version = "UNKNOWN"
        if (setup != "UNKNOWN" and re.fullmatch(r"[0-9a-f]{64}", code)
                and isinstance(config, str) and re.fullmatch(r"[0-9a-f]{64}", config)):
            material = json.dumps(["EQUITY_POLICY_ATTRIBUTION_V1", engine, setup, code, config])
            version = "EQUITY_POLICY_V1:" + sha256(material.encode()).hexdigest()
        return dict(zip(FIELDS, (setup, version, code, config,
                                "DECLARED_SOURCE_AND_ENGINE_CONFIG_V1")))


def inherited_metadata(metadata):
    return {k: metadata[k] for k in FIELDS if k in metadata}
