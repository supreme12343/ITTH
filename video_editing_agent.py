"""Compatibility entry point for the video editing agent prototype.

The implementation lives in ``notworking(prototype)/video_editing_agent.py``.
This module keeps existing application imports working during the prototype
move and lets callers continue using ``from video_editing_agent import ...``.
"""

import importlib.util
import sys
from pathlib import Path

_implementation_path = (
    Path(__file__).resolve().parent
    / "notworking(prototype)"
    / "video_editing_agent.py"
)
_module_name = "_video_editing_agent_prototype"
_spec = importlib.util.spec_from_file_location(_module_name, _implementation_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load video editing agent from {_implementation_path}")
_implementation = importlib.util.module_from_spec(_spec)
sys.modules[_module_name] = _implementation
_spec.loader.exec_module(_implementation)

for _name in dir(_implementation):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_implementation, _name)

__all__ = [name for name in dir(_implementation) if not name.startswith("_")]
