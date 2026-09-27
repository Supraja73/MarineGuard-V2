"""
mode.py (router)
------------------
Lets the frontend read and switch the platform's Live Tracking mode
(demo <-> real) at runtime, with no server restart and no env change.

DEMO MODE: simulation only. Ships are placed by clicking the map and
moved by the existing voyage-tick simulator. No GPS spoofing alerts
(see app.routers.detect - spoofing checks against real hardware
physics don't make sense against positions an operator can legitimately
teleport via replan/fast-forward).

REAL MODE: intended for a deployment wired to a live AIS/GPS feed.
GPS spoofing detection is enabled. This endpoint only flips the mode
flag - it does not itself ingest any live feed, since none is
connected in this deployment.

Every connected dashboard client is notified immediately over the
existing /ws/live websocket so all open sessions switch in lockstep,
matching the spec requirement to switch modes "without restarting the
application".
"""

from fastapi import APIRouter, Request

from app.core import runtime_state
from app.schemas.mode import ModeSetRequest
from app.core.logging_config import get_logger

router = APIRouter(prefix="/api/mode", tags=["mode"])
logger = get_logger("app.routers.mode")


@router.get("")
async def get_mode():
    return {"mode": runtime_state.get_mode()}


@router.post("")
async def set_mode(req: ModeSetRequest, request: Request):
    registry = request.app.state.connection_registry
    new_mode = runtime_state.set_mode(req.mode)
    logger.info("Tracking mode switched to %s", new_mode)
    await registry.broadcast({"kind": "mode_changed", "mode": new_mode})
    return {"mode": new_mode}
