"""
incidents.py (router)
------------------------
Attack Mode + system-incident endpoints. All state-changing actions are
control-station-only (require_control_station), same gate used for
reroute approval elsewhere in this app.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_control_station
from app.schemas.incidents import StartIncidentRequest, IncidentActionRequest, ALL_INCIDENT_TYPES, ATTACK_TYPES, SYSTEM_INCIDENT_TYPES
from app.services import incident_service
from app.routers.ships import _get_ship_or_404

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


@router.get("/types")
async def list_incident_types():
    return {"attacks": ATTACK_TYPES, "system_incidents": SYSTEM_INCIDENT_TYPES}


@router.get("/ship/{ship_id}")
async def list_active_for_ship(ship_id: str, session: AsyncSession = Depends(get_db)):
    return await incident_service.active_incidents_for_ship(session, ship_id)


@router.post("/start")
async def start_incident(req: StartIncidentRequest, request: Request,
                          session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    ship = await _get_ship_or_404(session, req.ship_id)
    registry = request.app.state.connection_registry
    result = await incident_service.start_incident(session, registry, ship, req.incident_type)
    return result


@router.post("/{ship_id}/{incident_type}/act")
async def act_on_incident(ship_id: str, incident_type: str, req: IncidentActionRequest, request: Request,
                           session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    if incident_type not in ALL_INCIDENT_TYPES:
        raise HTTPException(400, "Unknown incident_type")
    ship = await _get_ship_or_404(session, ship_id)
    registry = request.app.state.connection_registry
    return await incident_service.act_on_incident(session, registry, ship, incident_type, req.action, req.note)


@router.post("/{ship_id}/{incident_type}/end")
async def end_incident(ship_id: str, incident_type: str, request: Request,
                        session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    if incident_type not in ALL_INCIDENT_TYPES:
        raise HTTPException(400, "Unknown incident_type")
    ship = await _get_ship_or_404(session, ship_id)
    registry = request.app.state.connection_registry
    return await incident_service.end_incident(session, registry, ship, incident_type)
