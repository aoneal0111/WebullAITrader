"""Offline historical decision-intelligence artifact tooling.

DI-1 deliberately has no runtime composition imports.  Runtime lookup belongs
to a later phase; this package only builds and validates research artifacts.
"""

from .artifact import ArtifactValidationError, build_artifact, validate_artifact
from .models import ARTIFACT_SCHEMA_VERSION

__all__ = ["ARTIFACT_SCHEMA_VERSION", "ArtifactValidationError", "build_artifact", "validate_artifact"]
