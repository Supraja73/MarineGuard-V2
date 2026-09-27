"""
voyages.py (router)
-----------------------
Voyages are a layer on top of ship registration: a registered ship can
start a voyage (origin/destination chosen from the port dropdown, a
validated speed, a geofence radius), have it tracked automatically as
position reports come in (see app.routers.positions, which updates the
active voyage's progress on every accepted position report), replan it
mid-trip without re-registering, or have it auto-complete on arrival.
Completed voyages are kept (status="completed") as voyage history.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_control_station, get_current_user, get_current_user_optional
from app.data.ports import get_port, WORLD_PORTS
from app.db.models import Ship, Voyage
from app.schemas.voyages import VoyageCreateRequest, VoyageReplanRequest
from app.services.voyage_service import voyage_to_dict, voyage_total_distance_km
from app.services.alert_service import raise_alert
from app.blockchain import ledger

router = APIRouter(prefix="/api/voyages", tags=["voyages"])


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/ports")
async def list_ports():
    """World ports available for the origin/destination dropdowns."""
    return WORLD_PORTS


async def _get_ship_or_404(session: AsyncSession, ship_id: str) -> Ship:
    ship = await session.get(Ship, ship_id)
    if not ship:
        raise HTTPException(404, "Ship not found")
    return ship


async def _active_voyage_for_ship(session: AsyncSession, ship_id: str) -> Voyage | None:
    result = await session.execute(
        select(Voyage)
        .where(Voyage.ship_id == ship_id, Voyage.status == "in_progress")
        .order_by(Voyage.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.post("")
async def create_voyage(req: VoyageCreateRequest, request: Request, session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    registry = request.app.state.connection_registry
    ship = await _get_ship_or_404(session, req.ship_id)

    origin = get_port(req.origin_port)
    dest = get_port(req.dest_port)
    if not origin:
        raise HTTPException(400, f"Unknown origin port: {req.origin_port}")
    if not dest:
        raise HTTPException(400, f"Unknown destination port: {req.dest_port}")
    if req.origin_port == req.dest_port:
        raise HTTPException(400, "Origin and destination must be different ports")

    # Only one active voyage per ship - close out any existing one as
    # "replanned" rather than leaving two in_progress rows around.
    existing = await _active_voyage_for_ship(session, req.ship_id)
    if existing:
        existing.status = "replanned"
        existing.completed_at = _now_utc()

    voyage = Voyage(
        ship_id=req.ship_id,
        origin_port=origin["name"], origin_lat=origin["lat"], origin_lon=origin["lon"],
        dest_port=dest["name"], dest_lat=dest["lat"], dest_lon=dest["lon"],
        current_lat=origin["lat"], current_lon=origin["lon"],
        current_speed_kmh=req.current_speed_kmh,
        geofence_radius_km=req.geofence_radius_km,
        status="in_progress",
        remarks=req.remarks,
        expected_departure=req.expected_departure,
        created_at=_now_utc(),
    )
    voyage.distance_total_km = voyage_total_distance_km(voyage)
    voyage.distance_travelled_km = 0
    voyage.distance_remaining_km = voyage.distance_total_km
    if voyage.current_speed_kmh > 0:
        from datetime import timedelta
        voyage.eta = _now_utc() + timedelta(hours=voyage.distance_remaining_km / voyage.current_speed_kmh)
    session.add(voyage)

    # Keep the ship's own origin/dest/position fields in sync so the rest
    # of the app (route deviation, cyclone heading check, map markers)
    # keeps working off a single source of truth for "where is this ship
    # going right now".
    ship.origin_port, ship.origin_lat, ship.origin_lon = origin["name"], origin["lat"], origin["lon"]
    ship.dest_port, ship.dest_lat, ship.dest_lon = dest["name"], dest["lat"], dest["lon"]
    ship.current_lat, ship.current_lon = origin["lat"], origin["lon"]
    ship.current_speed = req.current_speed_kmh
    ship.status = "online"

    await session.commit()
    await session.refresh(voyage)

    await raise_alert(
        session, registry,
        ship_id=ship.ship_id, ship_name=ship.name, alert_type="voyage_started",
        severity="info", title=f"Voyage Started: {ship.name}",
        detail=f"{origin['name']} \u2192 {dest['name']} at {req.current_speed_kmh:.0f} km/h.",
        lat=origin["lat"], lon=origin["lon"],
    )
    await ledger.append_block(
        session, ship_id=ship.ship_id, ship_name=ship.name, event_type="VOYAGE_STARTED",
        lat=origin["lat"], lon=origin["lon"],
        payload={"voyage_id": voyage.voyage_id, "origin_port": origin["name"], "dest_port": dest["name"]},
    )

    await registry.broadcast({"kind": "voyage_started", "ship_id": ship.ship_id, "voyage_id": voyage.voyage_id})

    return voyage_to_dict(voyage)


@router.get("/active")
async def list_active_voyages(session: AsyncSession = Depends(get_db), user=Depends(get_current_user_optional)):
    """Control Station sees every active voyage fleet-wide, and so does
    an unauthenticated Passenger view (read-only, always public). A Ship
    Captain only sees the active voyage for their own assigned ship.
    """
    query = select(Voyage).where(Voyage.status == "in_progress")
    if user is not None and user.role == "ship_captain":
        if not user.ship_id:
            return []
        query = query.where(Voyage.ship_id == user.ship_id)
    result = await session.execute(query)
    voyages = result.scalars().all()
    return [voyage_to_dict(v) for v in voyages]


@router.get("/ship/{ship_id}")
async def get_ship_voyages(ship_id: str, session: AsyncSession = Depends(get_db), user=Depends(get_current_user_optional)):
    """Full voyage history for a ship, most recent first."""
    if user is not None and user.role == "ship_captain" and user.ship_id != ship_id:
        raise HTTPException(403, "Ship Captains may only view their own assigned ship's voyages")
    await _get_ship_or_404(session, ship_id)
    result = await session.execute(
        select(Voyage).where(Voyage.ship_id == ship_id).order_by(Voyage.created_at.desc())
    )
    voyages = result.scalars().all()
    return [voyage_to_dict(v) for v in voyages]


@router.get("/ship/{ship_id}/active")
async def get_active_voyage(ship_id: str, session: AsyncSession = Depends(get_db), user=Depends(get_current_user_optional)):
    if user is not None and user.role == "ship_captain" and user.ship_id != ship_id:
        raise HTTPException(403, "Ship Captains may only view their own assigned ship's voyage")
    await _get_ship_or_404(session, ship_id)
    voyage = await _active_voyage_for_ship(session, ship_id)
    return voyage_to_dict(voyage) if voyage else None


@router.patch("/{voyage_id}")
async def replan_voyage(voyage_id: int, req: VoyageReplanRequest, request: Request, session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    """Edits an in-progress voyage's destination/origin/speed/geofence
    without ending it or requiring re-registration - the cyclone-blocked-
    route case from the spec.
    """
    registry = request.app.state.connection_registry
    voyage = await session.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "Voyage not found")
    if voyage.status != "in_progress":
        raise HTTPException(400, "Only an in-progress voyage can be replanned")

    ship = await _get_ship_or_404(session, voyage.ship_id)
    changed_route = False

    if req.origin_port:
        origin = get_port(req.origin_port)
        if not origin:
            raise HTTPException(400, f"Unknown origin port: {req.origin_port}")
        voyage.origin_port, voyage.origin_lat, voyage.origin_lon = origin["name"], origin["lat"], origin["lon"]
        changed_route = True
    if req.dest_port:
        dest = get_port(req.dest_port)
        if not dest:
            raise HTTPException(400, f"Unknown destination port: {req.dest_port}")
        voyage.dest_port, voyage.dest_lat, voyage.dest_lon = dest["name"], dest["lat"], dest["lon"]
        ship.dest_port, ship.dest_lat, ship.dest_lon = dest["name"], dest["lat"], dest["lon"]
        changed_route = True
    elif req.dest_lat is not None and req.dest_lon is not None:
        voyage.dest_port, voyage.dest_lat, voyage.dest_lon = "Custom Waypoint", req.dest_lat, req.dest_lon
        ship.dest_port, ship.dest_lat, ship.dest_lon = "Custom Waypoint", req.dest_lat, req.dest_lon
        changed_route = True
    if req.current_speed_kmh is not None:
        voyage.current_speed_kmh = req.current_speed_kmh
        ship.current_speed = req.current_speed_kmh
    if req.geofence_radius_km is not None:
        voyage.geofence_radius_km = req.geofence_radius_km
    if req.remarks is not None:
        voyage.remarks = req.remarks

    if changed_route:
        # Distance/remaining are recomputed from the ship's current
        # position against the (possibly new) destination, not reset to
        # zero - the ship doesn't teleport back to a new origin.
        voyage.distance_total_km = voyage_total_distance_km(voyage)
        if voyage.current_lat is not None and voyage.current_lon is not None:
            from app.detection import detection
            voyage.distance_remaining_km = detection.haversine_km(
                voyage.current_lat, voyage.current_lon, voyage.dest_lat, voyage.dest_lon
            )
            if voyage.current_speed_kmh > 0:
                from datetime import timedelta
                voyage.eta = _now_utc() + timedelta(hours=voyage.distance_remaining_km / voyage.current_speed_kmh)

    await session.commit()
    await session.refresh(voyage)

    await raise_alert(
        session, registry,
        ship_id=ship.ship_id, ship_name=ship.name, alert_type="route_replanned",
        severity="warning", title=f"Route Replanned: {ship.name}",
        detail=f"Voyage updated \u2014 now heading to {voyage.dest_port}.",
        lat=voyage.current_lat, lon=voyage.current_lon,
    )
    await registry.broadcast({"kind": "voyage_replanned", "ship_id": ship.ship_id, "voyage_id": voyage.voyage_id})

    return voyage_to_dict(voyage)


@router.post("/{voyage_id}/complete")
async def complete_voyage(voyage_id: int, request: Request, session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    registry = request.app.state.connection_registry
    voyage = await session.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "Voyage not found")
    if voyage.status == "completed":
        return voyage_to_dict(voyage)

    voyage.status = "completed"
    voyage.completed_at = _now_utc()
    voyage.distance_remaining_km = 0

    ship = await session.get(Ship, voyage.ship_id)
    if ship:
        ship.status = "docked"

    await session.commit()
    await session.refresh(voyage)

    if ship:
        await raise_alert(
            session, registry,
            ship_id=ship.ship_id, ship_name=ship.name, alert_type="voyage_completed",
            severity="info", title=f"\u2705 {ship.name} has reached destination",
            detail=f"Arrived at {voyage.dest_port}.",
            lat=voyage.dest_lat, lon=voyage.dest_lon,
        )
        await ledger.append_block(
            session, ship_id=ship.ship_id, ship_name=ship.name, event_type="DESTINATION_REACHED",
            lat=voyage.dest_lat, lon=voyage.dest_lon, payload={"voyage_id": voyage.voyage_id, "dest_port": voyage.dest_port},
        )
        await ledger.append_block(
            session, ship_id=ship.ship_id, ship_name=ship.name, event_type="VOYAGE_COMPLETED",
            lat=voyage.dest_lat, lon=voyage.dest_lon, payload={"voyage_id": voyage.voyage_id},
        )
    await registry.broadcast({"kind": "voyage_completed", "ship_id": voyage.ship_id, "voyage_id": voyage.voyage_id})

    return voyage_to_dict(voyage)
