"""
cyclones.py (router)
------------------------
Simulated worldwide tropical cyclone tracking driven by a local database
table, replacing the live GDACS feed. Provides endpoints to fetch active
cyclones and trigger new test cyclones for demonstration purposes.

Route alert logic: a ship doesn't have to be inside the storm for an alert
to be justified - it's enough that the storm is near the ship, heading
toward it, or its forecast cone crosses the ship's planned route.
check_route_risk() scores each active cyclone against a ship's current
position AND its active voyage's origin->destination corridor.
"""

import math
import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_control_station
from app.core.logging_config import get_logger
from app.detection import detection
from app.db.models import Ship, Voyage, Cyclone
from app.services.alert_service import raise_alert, resolve_alert
from app.services.cyclone_spawn_service import spawn_point_for_voyage
from app.config.settings import get_settings

router = APIRouter(prefix="/api/cyclones", tags=["cyclones"])
logger = get_logger("app.routers.cyclones")

# Distance thresholds (km) for each risk tier
RISK_THRESHOLDS_KM = {
    "CRITICAL": 200,
    "HIGH": 500,
    "MEDIUM": 900,
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/active")
async def active_cyclones(session: AsyncSession = Depends(get_db)):
    """Fetch all active cyclones from the local database."""
    result = await session.execute(select(Cyclone).where(Cyclone.active == 1))
    cyclones = result.scalars().all()
    
    # Format exactly as the frontend expects
    cyclone_list = []
    for c in cyclones:
        cyclone_list.append({
            "id": str(c.id),
            "name": c.name,
            "lat": c.center_lat,
            "lon": c.center_lon,
            "radius_km": c.radius_km,
            "severity": c.severity,
            "wind_speed_kmh": 150 if c.severity == "high" else (100 if c.severity == "medium" else 60),
            "alert_level": c.severity,
            "category": f"Simulated {c.severity.title()} Cyclone",
        })
        
    return {"cyclones": cyclone_list, "source": "SIMULATED", "fetched_at": _now_utc().isoformat()}


class TriggerCycloneRequest(BaseModel):
    lat: float | None = None
    lon: float | None = None
    radius_km: float | None = None
    severity: str = "high"
    name: str = "Test Cyclone"
    ship_id: str | None = None  # target this ship's voyage; random active voyage if omitted


@router.post("/test_trigger")
async def trigger_test_cyclone(payload: TriggerCycloneRequest, session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    """Control-Station-only endpoint to create a simulated cyclone.

    If lat/lon are given explicitly, uses them as-is (kept for direct
    API testing). Otherwise - the normal path from the "Trigger Cyclone"
    UI button - places the cyclone realistically: along an active
    voyage's route (see app.services.cyclone_spawn_service), close
    enough to breach the route's safety buffer and force a decision, far
    enough that a detour is still geometrically possible, and clear of
    the ship's current position, the destination, and the departure
    port.
    """
    lat, lon, radius_km = payload.lat, payload.lon, payload.radius_km

    if lat is None or lon is None:
        query = select(Voyage).where(Voyage.status == "in_progress")
        if payload.ship_id:
            query = query.where(Voyage.ship_id == payload.ship_id)
        result = await session.execute(query)
        voyages = result.scalars().all()
        if not voyages:
            raise HTTPException(400, "No active voyage to threaten - start a voyage first" if not payload.ship_id else f"No active voyage for ship {payload.ship_id}")
        voyage = random.choice(voyages)

        settings = get_settings()
        spawn = spawn_point_for_voyage(voyage, settings.cyclone_safety_buffer_km)
        lat, lon = spawn["lat"], spawn["lon"]
        radius_km = radius_km or spawn["radius_km"]

    new_cyclone = Cyclone(
        name=payload.name,
        center_lat=lat,
        center_lon=lon,
        radius_km=radius_km or 300.0,
        severity=payload.severity.lower(),
        active=1,
        created_at=_now_utc()
    )
    session.add(new_cyclone)
    await session.commit()
    return {"status": "success", "cyclone_id": new_cyclone.id, "lat": lat, "lon": lon, "radius_km": new_cyclone.radius_km}

@router.post("/test_clear")
async def clear_test_cyclones(session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    """Admin-only endpoint to clear simulated cyclones."""
    await session.execute(update(Cyclone).values(active=0))
    await session.commit()
    
    # We should ideally clear the active alerts related to cyclones here,
    # but the frontend will also call checkAllCycloneRouteRisks which resolves them automatically
    # since no cyclones will intersect the routes anymore.
    return {"status": "success"}


def _risk_level(distance_to_ship_km: float | None, distance_to_route_km: float | None) -> str:
    best = min(d for d in (distance_to_ship_km, distance_to_route_km) if d is not None) \
        if (distance_to_ship_km is not None or distance_to_route_km is not None) else None
    if best is None:
        return "SAFE"
    if best <= RISK_THRESHOLDS_KM["CRITICAL"]:
        return "CRITICAL"
    if best <= RISK_THRESHOLDS_KM["HIGH"]:
        return "HIGH"
    if best <= RISK_THRESHOLDS_KM["MEDIUM"]:
        return "MEDIUM"
    return "SAFE"


@router.post("/check_route")
async def check_route_risk(payload: dict, request: Request, session: AsyncSession = Depends(get_db)):
    """Assesses every active cyclone against one ship's current position
    AND its active voyage's origin->destination corridor.
    """
    ship_id = payload.get("ship_id")
    cyclones = payload.get("cyclones") or []
    if not ship_id:
        raise HTTPException(400, "ship_id is required")

    ship = await session.get(Ship, ship_id)
    if not ship:
        raise HTTPException(404, "Ship not found")

    voyage_result = await session.execute(
        select(Voyage).where(Voyage.ship_id == ship_id, Voyage.status == "in_progress").limit(1)
    )
    voyage = voyage_result.scalar_one_or_none()

    registry = request.app.state.connection_registry
    assessments = []
    
    # We will compute a master flag to return to the frontend for UI locking
    has_cyclone_warning = False

    for c in cyclones:
        try:
            c_lat, c_lon = float(c["lat"]), float(c["lon"])
        except (KeyError, TypeError, ValueError):
            continue

        distance_to_ship_km = None
        if ship.current_lat is not None and ship.current_lon is not None:
            distance_to_ship_km = round(detection.haversine_km(ship.current_lat, ship.current_lon, c_lat, c_lon), 1)

        distance_to_route_km = None
        if voyage:
            distance_to_route_km = round(
                detection.point_to_segment_distance_km(
                    c_lat, c_lon, voyage.origin_lat, voyage.origin_lon, voyage.dest_lat, voyage.dest_lon
                ), 1
            )

        radius_km = c.get("radius_km") or 300.0
        
        # If the ship or its route is inside the cyclone's radius, this is a warning
        is_inside_radius = False
        if distance_to_ship_km is not None and distance_to_ship_km <= radius_km:
            is_inside_radius = True
        if distance_to_route_km is not None and distance_to_route_km <= radius_km:
            is_inside_radius = True
            
        if is_inside_radius:
            has_cyclone_warning = True

        level = _risk_level(distance_to_ship_km, distance_to_route_km)
        if is_inside_radius and level == "SAFE":
            level = "MEDIUM"

        eta_hours = None
        wind_kmh = c.get("wind_speed_kmh")
        assumed_translation_kmh = 20.0
        if distance_to_ship_km is not None:
            eta_hours = round(distance_to_ship_km / assumed_translation_kmh, 1)

        assessment = {
            "cyclone_id": c.get("id"),
            "cyclone_name": c.get("name"),
            "distance_to_ship_km": distance_to_ship_km,
            "distance_to_route_km": distance_to_route_km,
            "risk_level": level,
            "estimated_hours_to_danger": eta_hours,
            "wind_speed_kmh": wind_kmh,
        }
        assessments.append(assessment)

        # For the demo, trigger an alert if the ship or its route falls inside the cyclone radius
        if is_inside_radius:
            await raise_alert(
                session, registry,
                ship_id=ship.ship_id, ship_name=ship.name, alert_type="cyclone_route_alert",
                severity="critical",
                title=f"\u26a0 Cyclone Warning: {c.get('name', 'Cyclone')} intercepts route!",
                detail=(
                    f"Cyclone {c.get('name', 'system')} is {distance_to_ship_km} km from {ship.name}"
                    f"{', ' + str(distance_to_route_km) + ' km from planned route' if distance_to_route_km is not None else ''}."
                    f" Route locked. Awaiting Control Station reroute."
                ),
                lat=c_lat, lon=c_lon,
            )
        else:
            await resolve_alert(session, registry, ship_id=ship.ship_id, alert_type="cyclone_route_alert")

    return {"ship_id": ship_id, "assessments": assessments, "has_cyclone_warning": has_cyclone_warning}
