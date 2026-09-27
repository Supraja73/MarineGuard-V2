"""
mode.py (schemas) companion — incidents.py (schemas)
------------------------------------------------------
Request models for the Incident/Attack Mode simulator (app.services.incident_service).

Two families of incident, both handled by the same start/act/end machinery:
  - ATTACK types: pirate, missile, drone, hijack, sabotage, cargo_theft, distress
  - SYSTEM/THREAT types with no real sensor feed to detect them from, so —
    consistent with the spec's own Attack Mode design — they're operator-
    triggered test incidents rather than invented background noise:
    engine_failure, fire, man_overboard, unauthorized_boarding,
    cargo_temp_failure, anchor_drift, comm_loss, ais_offline
"""

from typing import Optional
from pydantic import BaseModel, field_validator

ATTACK_TYPES = {
    "pirate": "Pirate Attack",
    "missile": "Missile Threat",
    "drone": "Drone Threat",
    "hijack": "Hijack Attempt",
    "sabotage": "Engine Sabotage",
    "cargo_theft": "Cargo Theft",
    "distress": "Emergency Distress",
}

SYSTEM_INCIDENT_TYPES = {
    "engine_failure": "Engine Failure",
    "fire": "Fire Onboard",
    "man_overboard": "Man Overboard",
    "unauthorized_boarding": "Unauthorized Boarding",
    "cargo_temp_failure": "Cargo Temperature Failure",
    "anchor_drift": "Anchor Drift",
    "comm_loss": "Communication Loss",
    "ais_offline": "AIS Offline",
}

ALL_INCIDENT_TYPES = {**ATTACK_TYPES, **SYSTEM_INCIDENT_TYPES}

OPERATOR_ACTIONS = (
    "acknowledge", "escalate", "dispatch_coast_guard", "request_naval_support",
    "broadcast_distress", "activate_emergency_protocol",
)


class StartIncidentRequest(BaseModel):
    ship_id: str
    incident_type: str

    @field_validator("incident_type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in ALL_INCIDENT_TYPES:
            raise ValueError(f"incident_type must be one of {list(ALL_INCIDENT_TYPES)}")
        return v


class IncidentActionRequest(BaseModel):
    action: str
    note: Optional[str] = None

    @field_validator("action")
    @classmethod
    def _validate_action(cls, v: str) -> str:
        if v not in OPERATOR_ACTIONS:
            raise ValueError(f"action must be one of {OPERATOR_ACTIONS}")
        return v
