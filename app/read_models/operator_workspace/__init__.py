"""Immutable cross-panel operator selection state."""

from .models import OperatorWorkspaceSnapshot, WorkspaceSelectionSource
from .projector import OperatorWorkspaceProjector

__all__ = [
    "OperatorWorkspaceProjector",
    "OperatorWorkspaceSnapshot",
    "WorkspaceSelectionSource",
]
