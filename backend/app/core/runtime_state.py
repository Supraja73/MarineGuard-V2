"""
runtime_state.py
-----------------
Holds the platform's Demo/Real tracking mode as live, in-process mutable
state - distinct from app.config.settings.Settings, which is read once at
startup from the environment and never changes for the life of the
process.

The Live Tracking spec requires switching between REAL MODE (real
ship GPS/AIS data, real weather, GPS spoofing detection enabled) and
DEMO MODE (simulation only, ships placed by clicking the map, no
browser GPS, no spoofing alerts) "without restarting the application".
A pydantic Settings field can't do that - it's fixed for the process
lifetime - so this module is the single source of truth for the
*current* mode, toggled at runtime via app.routers.mode and read by
anything that needs to behave differently per mode (currently
app.routers.detect's GPS-spoofing check).

Seeded from settings.simulation_mode at import time so existing
deployments keep their configured default on first boot, but from then
on this value - not the static setting - is authoritative.
"""

from app.config.settings import get_settings

VALID_MODES = ("demo", "real")

_state = {
    "mode": "demo" if get_settings().simulation_mode else "real",
}


def get_mode() -> str:
    return _state["mode"]


def is_demo_mode() -> bool:
    return _state["mode"] == "demo"


def set_mode(mode: str) -> str:
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}, got {mode!r}")
    _state["mode"] = mode
    return _state["mode"]
