"""
mode.py (schemas)
------------------
Request model for switching the platform between Demo Mode and Real
Mode at runtime (see app.core.runtime_state).
"""

from pydantic import BaseModel, field_validator

from app.core.runtime_state import VALID_MODES


class ModeSetRequest(BaseModel):
    mode: str

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_MODES:
            raise ValueError(f"mode must be one of {VALID_MODES}")
        return v
