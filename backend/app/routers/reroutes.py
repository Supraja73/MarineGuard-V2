"""
reroutes.py (router)
------------------------
The cyclone-blocked-route workflow. Detection, route/safe-port
computation, AND applying the change are all automatic - see
app.services.cyclone_monitor, a background task that ticks every
CYCLONE_MONITOR_INTERVAL_SECONDS and calls the exact same
app.services.reroute_orchestrator functions this router calls manually:

1. A blocked voyage automatically gets an "alternate_route" reroute (if a
   clear searoute path exists) or a "safe_port" diversion (divert-and-hold,
   if no route exists) applied immediately - no one has to click
   anything. An alert is raised the moment this happens so it's visible
   on Live Tracking / Alerts right away. The /propose endpoint below just
   forces an immediate check instead of waiting for the next monitor tick.
2. Every applied reroute is logged (RerouteRequest.status="approved",
   decided_by="AUTO-SYSTEM") for audit purposes. The /approve and
   /reject endpoints remain available for Control Station to review the
   history or handle the rare case where no clear route/port exists at
   all, but nothing sits waiting for a manual click in normal operation.
3. A ship holding at a safe port is automatically re-checked by the
   monitor every tick; the moment a real clear path home exists again,
   the voyage automatically resumes. The /voyage/{id}/resume endpoint
   below just forces an immediate check.
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_control_station, get_current_user
from app.db.models import RerouteRequest, Ship, Voyage
from app.schemas.reroutes import ProposeRerouteRequest
from app.services.alert_service import raise_alert
from app.services.reroute_orchestrator import apply_reroute, propose_reroute_for_voyage, propose_resume_for_voyage
from app.services.voyage_service import voyage_total_distance_km
from app.detection import detection
from app.blockchain import ledger

router = APIRouter(prefix="/api/reroutes", tags=["reroutes"])


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _request_to_dict(r: RerouteRequest) -> dict:
    return {
        "id": r.id,
        "voyage_id": r.voyage_id,
        "ship_id": r.ship_id,
        "kind": r.kind,
        "cyclone_name": r.cyclone_name,
        "reason": r.reason,
        "proposed_waypoints": json.loads(r.proposed_waypoints_json) if r.proposed_waypoints_json else None,
        "proposed_distance_km": r.proposed_distance_km,
        "proposed_port": r.proposed_port,
        "proposed_port_lat": r.proposed_port_lat,
        "proposed_port_lon": r.proposed_port_lon,
        "original_dest_port": r.original_dest_port,
        "original_dest_lat": r.original_dest_lat,
        "original_dest_lon": r.original_dest_lon,
        "status": r.status,
        "decided_by": r.decided_by,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/pending")
async def list_pending(session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    """Everything awaiting a Control Station decision - what the approval
    inbox in the UI polls. Control Station only."""
    result = await session.execute(
        select(RerouteRequest).where(RerouteRequest.status == "pending").order_by(RerouteRequest.created_at.asc())
    )
    return [_request_to_dict(r) for r in result.scalars().all()]


@router.get("/voyage/{voyage_id}")
async def list_for_voyage(voyage_id: int, session: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    """Reroute/resume history and status for one voyage. A Ship Captain
    may only view this for their own assigned ship's voyage (this is how
    Captains see "reroute notifications" / "safe-port instructions"
    without getting the fleet-wide approvals inbox)."""
    voyage = await session.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "Voyage not found")
    if user.role == "ship_captain" and user.ship_id != voyage.ship_id:
        raise HTTPException(403, "Ship Captains may only view reroutes for their own assigned ship")
    result = await session.execute(
        select(RerouteRequest).where(RerouteRequest.voyage_id == voyage_id).order_by(RerouteRequest.created_at.desc())
    )
    return [_request_to_dict(r) for r in result.scalars().all()]


@router.post("/propose")
async def propose_reroute(payload: ProposeRerouteRequest, req: Request, session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    """Manual trigger for the same logic the background monitor runs
    automatically (app.services.cyclone_monitor). Useful for forcing an
    immediate check instead of waiting for the next monitor tick - the
    monitor is what makes this automatic per-voyage without anyone
    clicking anything, per the required behavior.
    """
    voyage = await session.get(Voyage, payload.voyage_id)
    if not voyage:
        raise HTTPException(404, "Voyage not found")
    if voyage.status != "in_progress":
        raise HTTPException(400, "Only an in-progress voyage can be rerouted")

    ship = await session.get(Ship, voyage.ship_id)
    if not ship:
        raise HTTPException(404, "Ship not found")

    registry = req.app.state.connection_registry
    try:
        result = await propose_reroute_for_voyage(session, registry, voyage, ship, source="manual")
    except ValueError:
        raise HTTPException(
            409,
            "No alternate route AND no safe port could be found clear of current cyclones. "
            "Ship should hold position and wait for Control Station instructions.",
        )

    if result is None:
        existing = await session.execute(
            select(RerouteRequest).where(RerouteRequest.voyage_id == voyage.voyage_id, RerouteRequest.status == "pending")
        )
        pending = existing.scalar_one_or_none()
        if pending:
            raise HTTPException(400, "This voyage already has a pending reroute request")
        raise HTTPException(400, "No active cyclones - nothing to reroute around")

    return _request_to_dict(result)


@router.post("/{request_id}/approve")
async def approve_reroute(
    request_id: int, req_body: Request, session: AsyncSession = Depends(get_db),
    current_user = Depends(require_control_station),
):
    """Manual override. In normal operation reroutes are already
    auto-applied the moment they're detected (see cyclone_monitor /
    reroute_orchestrator), so this will usually find nothing left in
    'pending' status - it exists for edge cases and for Control Station
    to force through a request the auto-monitor hasn't gotten to yet."""
    registry = req_body.app.state.connection_registry
    reroute = await session.get(RerouteRequest, request_id)
    if not reroute:
        raise HTTPException(404, "Reroute request not found")
    if reroute.status != "pending":
        raise HTTPException(400, f"Request already {reroute.status}")

    voyage = await session.get(Voyage, reroute.voyage_id)
    if not voyage:
        raise HTTPException(404, "Voyage not found")
    ship = await session.get(Ship, voyage.ship_id)

    await apply_reroute(session, registry, reroute, voyage, ship, decided_by=current_user.username)

    return {"reroute": _request_to_dict(reroute), "voyage_id": voyage.voyage_id}


@router.post("/{request_id}/reject")
async def reject_reroute(
    request_id: int, req_body: Request, session: AsyncSession = Depends(get_db),
    current_user = Depends(require_control_station),
):
    """Control Station only. Leaves the voyage untouched (still under
    cyclone warning) - operator disagreed with the proposal."""
    registry = req_body.app.state.connection_registry
    reroute = await session.get(RerouteRequest, request_id)
    if not reroute:
        raise HTTPException(404, "Reroute request not found")
    if reroute.status != "pending":
        raise HTTPException(400, f"Request already {reroute.status}")

    reroute.status = "rejected"
    reroute.decided_by = current_user.username
    reroute.decided_at = _now_utc()
    await session.commit()

    await registry.broadcast({"kind": "reroute_decided", "reroute_id": reroute.id, "decision": "rejected"})
    return {"reroute": _request_to_dict(reroute)}


@router.post("/voyage/{voyage_id}/resume")
async def propose_resume(voyage_id: int, req: Request, session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    """Manual trigger for the same automatic resume check the background
    monitor runs on every holding ship each tick (see
    app.services.cyclone_monitor). Useful for forcing an immediate check
    instead of waiting for the next tick.
    """
    voyage = await session.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "Voyage not found")
    ship = await session.get(Ship, voyage.ship_id)

    registry = req.app.state.connection_registry
    result = await propose_resume_for_voyage(session, registry, voyage, ship, source="manual")
    if result is None:
        return {"can_resume": False, "reason": "Cyclone(s) still block the route, this voyage isn't holding, or a request is already pending."}
    return {"can_resume": True, "reroute": _request_to_dict(result)}
