"""
reroute_orchestrator.py
-------------------------
The single place that turns "this voyage's route is unsafe" or "this
holding ship's route home is safe again" into a pending RerouteRequest
row. Both the manual /api/reroutes endpoints AND the automatic
background cyclone monitor call these same functions, so there is
exactly one implementation of the decision logic - the manual endpoint
isn't a separate code path that could drift from what the monitor does
automatically.
"""

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import ConnectionRegistry
from app.db.models import Cyclone, RerouteRequest, Ship, Voyage
from app.services.alert_service import raise_alert
from app.services.cyclone_reroute_service import (
    find_alternate_route, find_safest_port, route_is_blocked, route_clears_all_cyclones,
)
from app.services.voyage_service import voyage_total_distance_km
from app.detection import detection
from app.blockchain import ledger


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def apply_reroute(
    session: AsyncSession, registry: ConnectionRegistry | None,
    reroute: RerouteRequest, voyage: Voyage, ship: Ship | None,
    decided_by: str,
) -> None:
    """Actually applies a reroute/resume/safe-port proposal to the live
    voyage - splices in the new waypoints (or diverts to a safe port),
    re-anchors the voyage's origin to the ship's current position so the
    remaining route draws cleanly from where it actually is, and marks
    the request decided. Used by BOTH the manual Control Station
    /approve endpoint and the automatic cyclone monitor, so there is
    exactly one implementation of "what applying a reroute means."
    """
    if reroute.kind in ("alternate_route", "resume"):
        waypoints = json.loads(reroute.proposed_waypoints_json)
        dest = waypoints[-1]
        voyage.dest_lat, voyage.dest_lon = dest[0], dest[1]
        voyage.dest_port = reroute.original_dest_port if reroute.kind == "resume" else voyage.dest_port
        if ship:
            ship.dest_lat, ship.dest_lon = voyage.dest_lat, voyage.dest_lon
            ship.status = "online"
        if voyage.current_lat is not None and voyage.current_lon is not None:
            voyage.origin_lat, voyage.origin_lon = voyage.current_lat, voyage.current_lon
            voyage.origin_port = f"En Route ({reroute.kind.replace('_', ' ').title()})"
            voyage.distance_travelled_km = 0.0
        voyage.distance_total_km = voyage_total_distance_km(voyage)
        if voyage.current_lat is not None and voyage.current_lon is not None:
            voyage.distance_remaining_km = detection.haversine_km(
                voyage.current_lat, voyage.current_lon, voyage.dest_lat, voyage.dest_lon
            )
        title = f"\U0001F504 Route Auto-Changed: {ship.name if ship else voyage.ship_id}"
        detail = (
            f"Cyclone {reroute.cyclone_name or ''} detected on the route to {voyage.dest_port}. "
            f"System automatically switched to a clear alternate sea route."
        ) if reroute.kind == "alternate_route" else (
            f"{reroute.cyclone_name or 'The storm'} has cleared. System automatically resumed course to "
            f"{voyage.dest_port}."
        )
        blockchain_event = "JOURNEY_RESUMED" if reroute.kind == "resume" else None

    elif reroute.kind == "safe_port":
        voyage.dest_port = reroute.proposed_port
        voyage.dest_lat, voyage.dest_lon = reroute.proposed_port_lat, reroute.proposed_port_lon
        voyage.remarks = (
            f"Destination unsafe due to cyclone. Automatically redirected to safe port: {reroute.proposed_port}. "
            f"Holding at {reroute.proposed_port} until conditions clear. "
            f"Original destination: {reroute.original_dest_port}."
        )
        if ship:
            ship.dest_lat, ship.dest_lon = voyage.dest_lat, voyage.dest_lon
            ship.status = "holding"
        if voyage.current_lat is not None and voyage.current_lon is not None:
            voyage.origin_lat, voyage.origin_lon = voyage.current_lat, voyage.current_lon
            voyage.origin_port = "En Route (Cyclone Diversion)"
            voyage.distance_travelled_km = 0.0
        voyage.distance_total_km = voyage_total_distance_km(voyage)
        if voyage.current_lat is not None and voyage.current_lon is not None:
            voyage.distance_remaining_km = detection.haversine_km(
                voyage.current_lat, voyage.current_lon, voyage.dest_lat, voyage.dest_lon
            )
        title = f"\u26a0 Auto-Diverted to Safe Port: {ship.name if ship else voyage.ship_id}"
        detail = (
            f"No clear route around {reroute.cyclone_name or 'the storm'} was found. System automatically "
            f"diverted to {reroute.proposed_port} to hold until conditions clear."
        )
        blockchain_event = "REDIRECTED_TO_SAFE_PORT"
    else:
        return

    reroute.status = "approved"
    reroute.decided_by = decided_by
    reroute.decided_at = _now_utc()

    await session.commit()
    await session.refresh(voyage)

    if registry is not None:
        await raise_alert(
            session, registry, ship_id=voyage.ship_id, ship_name=ship.name if ship else voyage.ship_id,
            alert_type="route_replanned", severity="warning", title=title, detail=detail,
            lat=voyage.current_lat, lon=voyage.current_lon,
        )
    if blockchain_event:
        await ledger.append_block(
            session, ship_id=voyage.ship_id, ship_name=ship.name if ship else voyage.ship_id,
            event_type=blockchain_event, lat=voyage.current_lat, lon=voyage.current_lon,
            payload={"voyage_id": voyage.voyage_id, "reroute_id": reroute.id},
        )
    if registry is not None:
        await registry.broadcast({"kind": "reroute_decided", "reroute_id": reroute.id, "decision": "approved"})


async def get_active_cyclones(session: AsyncSession) -> list[dict]:
    result = await session.execute(select(Cyclone).where(Cyclone.active == 1))
    return [
        {"id": c.id, "name": c.name, "lat": c.center_lat, "lon": c.center_lon, "radius_km": c.radius_km}
        for c in result.scalars().all()
    ]


async def _has_pending(session: AsyncSession, voyage_id: int) -> bool:
    result = await session.execute(
        select(RerouteRequest).where(RerouteRequest.voyage_id == voyage_id, RerouteRequest.status == "pending")
    )
    return result.scalar_one_or_none() is not None


async def _already_safely_rerouted(session: AsyncSession, voyage: Voyage, cyclones: list[dict]) -> bool:
    """True if this voyage's most recently applied alternate_route/resume
    still clears every currently active cyclone. Without this check, the
    monitor kept re-triggering a fresh reroute on every tick just because
    the DIRECT origin->destination path is (still, permanently) blocked
    by the same cyclone the ship already rerouted around - that direct
    check will never stop being 'blocked' while the storm exists, so it
    isn't a valid signal for whether the ship's ACTUAL current path is
    still safe. That constant re-triggering was re-anchoring the voyage
    (resetting distance_travelled_km to 0, recomputing distance_total_km)
    every ~5 seconds, which is why progress/ETA looked stuck at 0% and
    the route kept flipping between different detour bearings instead of
    settling on one path.
    """
    result = await session.execute(
        select(RerouteRequest)
        .where(
            RerouteRequest.voyage_id == voyage.voyage_id,
            RerouteRequest.kind.in_(["alternate_route", "resume"]),
            RerouteRequest.status == "approved",
        )
        .order_by(RerouteRequest.created_at.desc())
        .limit(1)
    )
    last = result.scalar_one_or_none()
    if not last or not last.proposed_waypoints_json:
        return False
    try:
        waypoints = json.loads(last.proposed_waypoints_json)
    except (TypeError, ValueError):
        return False
    return route_clears_all_cyclones(waypoints, cyclones)


async def propose_reroute_for_voyage(
    session: AsyncSession, registry: ConnectionRegistry | None, voyage: Voyage, ship: Ship | None,
    cyclones: list[dict] | None = None, source: str = "manual",
) -> RerouteRequest | None:
    """Creates a pending alternate_route or safe_port RerouteRequest for a
    voyage whose route is unsafe. Returns None (no-op) if there's already
    a pending request for this voyage, or if no cyclones are active.
    Raises ValueError("no_route_or_port") if neither an alternate route
    nor a safe port could be found at all - caller decides what to do
    with that (the API turns it into a 409; the monitor just logs it and
    tries again next tick).
    """
    if await _has_pending(session, voyage.voyage_id):
        return None

    if cyclones is None:
        cyclones = await get_active_cyclones(session)
    if not cyclones:
        return None

    if await _already_safely_rerouted(session, voyage, cyclones):
        return None

    origin_lat = voyage.current_lat if voyage.current_lat is not None else voyage.origin_lat
    origin_lon = voyage.current_lon if voyage.current_lon is not None else voyage.origin_lon

    alt = find_alternate_route(origin_lat, origin_lon, voyage.dest_lat, voyage.dest_lon, cyclones)

    await ledger.append_block(
        session, ship_id=voyage.ship_id, ship_name=ship.name if ship else voyage.ship_id,
        event_type="CYCLONE_DETECTED", lat=origin_lat, lon=origin_lon,
        payload={"voyage_id": voyage.voyage_id, "cyclone_name": cyclones[0]["name"]},
    )

    if alt is not None:
        req = RerouteRequest(
            voyage_id=voyage.voyage_id, ship_id=voyage.ship_id, kind="alternate_route",
            cyclone_name=cyclones[0]["name"],
            reason=(
                f"[{source}] Direct route to {voyage.dest_port} is blocked by "
                f"{cyclones[0]['name']}. Alternate sea route found that clears all active cyclones."
            ),
            proposed_waypoints_json=json.dumps(alt["waypoints"]),
            proposed_distance_km=alt["distance_km"],
            status="pending", created_at=_now_utc(),
        )
    else:
        safe_port = find_safest_port(origin_lat, origin_lon, cyclones, exclude_port_name=voyage.dest_port)
        if safe_port is None:
            raise ValueError("no_route_or_port")
        await ledger.append_block(
            session, ship_id=voyage.ship_id, ship_name=ship.name if ship else voyage.ship_id,
            event_type="DESTINATION_UNSAFE", lat=origin_lat, lon=origin_lon,
            payload={"voyage_id": voyage.voyage_id, "dest_port": voyage.dest_port, "cyclone_name": cyclones[0]["name"]},
        )
        req = RerouteRequest(
            voyage_id=voyage.voyage_id, ship_id=voyage.ship_id, kind="safe_port",
            cyclone_name=cyclones[0]["name"],
            reason=(
                f"[{source}] No clear route to {voyage.dest_port} could be found around "
                f"active cyclone(s). Proposing diversion to {safe_port['name']} ({safe_port['country']}) "
                f"to hold until conditions clear."
            ),
            proposed_port=safe_port["name"], proposed_port_lat=safe_port["lat"], proposed_port_lon=safe_port["lon"],
            original_dest_port=voyage.dest_port, original_dest_lat=voyage.dest_lat, original_dest_lon=voyage.dest_lon,
            status="pending", created_at=_now_utc(),
        )

    session.add(req)
    await session.commit()
    await session.refresh(req)

    if alt is not None:
        await ledger.append_block(
            session, ship_id=voyage.ship_id, ship_name=ship.name if ship else voyage.ship_id,
            event_type="ALTERNATE_ROUTE_GENERATED", lat=origin_lat, lon=origin_lon,
            payload={"voyage_id": voyage.voyage_id, "distance_km": alt["distance_km"]},
        )

    # Apply immediately - no Control Station click required. The system
    # already validated this route clears every active cyclone
    # (find_alternate_route only ever returns a validated candidate), so
    # there's nothing left for a human to approve; waiting for a click
    # just left ships sailing toward a blocked destination in the
    # meantime, which was the actual bug.
    await apply_reroute(session, registry, req, voyage, ship, decided_by="AUTO-SYSTEM")

    return req


async def propose_resume_for_voyage(
    session: AsyncSession, registry: ConnectionRegistry | None, voyage: Voyage, ship: Ship | None,
    cyclones: list[dict] | None = None, source: str = "manual",
) -> RerouteRequest | None:
    """For a voyage currently holding at a safe port (found via its most
    recent approved safe_port RerouteRequest), checks whether the route
    home to the ORIGINAL destination is clear again, and if so files a
    pending "resume" request. Returns None if not holding, already has a
    pending request, or the route is still blocked.
    """
    if await _has_pending(session, voyage.voyage_id):
        return None

    last_diversion = await session.execute(
        select(RerouteRequest)
        .where(RerouteRequest.voyage_id == voyage.voyage_id, RerouteRequest.kind == "safe_port", RerouteRequest.status == "approved")
        .order_by(RerouteRequest.decided_at.desc()).limit(1)
    )
    diversion = last_diversion.scalar_one_or_none()
    if not diversion:
        return None

    if cyclones is None:
        cyclones = await get_active_cyclones(session)

    # Cheap straight-line check first - only run the expensive full route
    # search if the corridor home actually looks clear now.
    if cyclones and route_is_blocked(
        voyage.current_lat, voyage.current_lon, diversion.original_dest_lat, diversion.original_dest_lon, cyclones
    ) is not None:
        return None

    alt = find_alternate_route(
        voyage.current_lat, voyage.current_lon,
        diversion.original_dest_lat, diversion.original_dest_lon, cyclones,
    )
    if alt is None:
        return None

    req = RerouteRequest(
        voyage_id=voyage.voyage_id, ship_id=voyage.ship_id, kind="resume",
        reason=f"[{source}] Cyclone has cleared enough to resume the route to {diversion.original_dest_port}.",
        proposed_waypoints_json=json.dumps(alt["waypoints"]),
        proposed_distance_km=alt["distance_km"],
        original_dest_port=diversion.original_dest_port,
        original_dest_lat=diversion.original_dest_lat, original_dest_lon=diversion.original_dest_lon,
        status="pending", created_at=_now_utc(),
    )
    session.add(req)
    await session.commit()
    await session.refresh(req)

    await ledger.append_block(
        session, ship_id=voyage.ship_id, ship_name=ship.name if ship else voyage.ship_id,
        event_type="CYCLONE_CLEARED", lat=voyage.current_lat, lon=voyage.current_lon,
        payload={"voyage_id": voyage.voyage_id, "original_dest_port": diversion.original_dest_port},
    )

    await apply_reroute(session, registry, req, voyage, ship, decided_by="AUTO-SYSTEM")

    return req
