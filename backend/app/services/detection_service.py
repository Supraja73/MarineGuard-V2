"""
detection_service.py
-----------------------
Runs the automatic anomaly-detection suite against a freshly reported
position: route deviation, restricted-zone entry, and unexpected-stop
detection. GPS spoofing is intentionally not run here because it needs an
operator-supplied elapsed-time value in this system's flow; it has its own
explicit endpoint (see app.routers.detect).

`ship` is a Ship ORM instance and `last_pos` is a Position ORM instance (or
None) - both use attribute access (ship.origin_lat), not dict subscripting,
since the migration to SQLAlchemy replaced sqlite3.Row objects with ORM
model instances everywhere in the request-handling path.
"""

from sqlalchemy import select

from app.detection import detection
from app.services.alert_service import raise_alert, resolve_alert
from app.db.models import RestrictedZone, Ship
from app.data.ports import WORLD_PORTS
from app.config.settings import get_settings
from datetime import datetime, timezone

# Ships closer than this while both underway are a collision risk. 5 km is
# tight enough that it won't fire for ships merely sharing a busy shipping
# lane a reasonable distance apart, but wide enough to give real warning
# time at typical cargo/tanker speeds.
COLLISION_RISK_KM = 5.0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _check_collision_risk(session, registry, ship, lat, lon, speed):
    """Pairwise proximity check against every other underway ship. Uses a
    canonical ordering of the (ship_id, other_id) pair for the alert_type
    so the SAME alert row is reused for both ships' cycles instead of two
    diverging ones - see raise_alert's stateful-per-(ship_id, alert_type)
    contract. Each ship still gets its own alert row (alert_type carries
    the *other* ship's id), so both operators watching either vessel see
    the warning.
    """
    if speed <= 0.5:
        return  # docked/stationary ships aren't a collision risk
    result = await session.execute(
        select(Ship).where(Ship.ship_id != ship.ship_id, Ship.status.notin_(["docked", "decommissioned", "registered"]))
    )
    others = result.scalars().all()
    at_risk_with = []
    for other in others:
        if other.current_lat is None or other.current_lon is None:
            continue
        dist = detection.haversine_km(lat, lon, other.current_lat, other.current_lon)
        if dist <= COLLISION_RISK_KM:
            at_risk_with.append((other, dist))
            alert_type = f"collision_risk::{other.ship_id}"
            await raise_alert(
                session, registry,
                ship_id=ship.ship_id, ship_name=ship.name,
                alert_type=alert_type, severity="critical",
                title=f"⚠ Collision Risk: {ship.name} / {other.name}",
                detail=f"{ship.name} is {dist:.1f} km from {other.name} — both underway.",
                lat=lat, lon=lon,
            )

    # Resolve any stale collision_risk alerts against ships no longer nearby.
    still_at_risk_ids = {o.ship_id for o, _ in at_risk_with}
    from app.db.models import Alert as _Alert  # local import: avoid a module-level cycle
    open_alerts = await session.execute(
        select(_Alert).where(_Alert.ship_id == ship.ship_id, _Alert.resolved == 0, _Alert.alert_type.like("collision_risk::%"))
    )
    for open_alert in open_alerts.scalars().all():
        other_id = open_alert.alert_type.split("::", 1)[1]
        if other_id not in still_at_risk_ids:
            await resolve_alert(session, registry, ship_id=ship.ship_id, alert_type=open_alert.alert_type)


async def run_detection_suite(session, registry, ship, lat, lon, speed, last_pos):
    triggered = []

    await _check_collision_risk(session, registry, ship, lat, lon, speed)

    route_result = detection.check_route_deviation(
        lat, lon, ship.origin_lat, ship.origin_lon,
        ship.dest_lat, ship.dest_lon,
    )
    if route_result["is_deviation"]:
        alert = await raise_alert(
            session, registry,
            ship_id=ship.ship_id, ship_name=ship.name,
            alert_type="route_deviation", severity="warning",
            title="Route Deviation Detected",
            detail=f"{ship.name} is {route_result['distance_from_corridor_km']} km "
                   f"from its expected corridor (limit: {route_result['corridor_km']} km).",
            lat=lat, lon=lon,
        )
        triggered.append(alert)
    else:
        # Ship is back inside its corridor - close out any deviation alert
        # that was open for it rather than leaving it active forever.
        await resolve_alert(session, registry, ship_id=ship.ship_id, alert_type="route_deviation")

    zones_result = await session.execute(select(RestrictedZone))
    zones = zones_result.scalars().all()
    zone_hit = detection.check_restricted_zone(lat, lon, [
        {"name": z.name, "lat": z.lat, "lon": z.lon,
         "radius_km": z.radius_km, "zone_type": z.zone_type} for z in zones
    ])
    if zone_hit:
        alert = await raise_alert(
            session, registry,
            ship_id=ship.ship_id, ship_name=ship.name,
            alert_type="restricted_zone", severity="critical",
            title="Restricted Zone Entry",
            detail=f"{ship.name} entered {zone_hit['zone_name']} "
                   f"({zone_hit['zone_type']}), {zone_hit['distance_from_center_km']} km from zone center.",
            lat=lat, lon=lon,
        )
        triggered.append(alert)
    else:
        await resolve_alert(session, registry, ship_id=ship.ship_id, alert_type="restricted_zone")

    if last_pos:
        expected_speed = last_pos.speed if last_pos.speed and last_pos.speed > 0 else speed
        speed_result = detection.check_speed_anomaly(speed, expected_speed if expected_speed > 0 else 1)
        if speed_result["is_anomaly"] and speed <= 0.01:
            alert = await raise_alert(
                session, registry,
                ship_id=ship.ship_id, ship_name=ship.name,
                alert_type="unexpected_stop", severity="warning",
                title="Unexpected Stop Detected",
                detail=f"{ship.name} speed dropped to 0 km/h.",
                lat=lat, lon=lon,
            )
            triggered.append(alert)
        elif speed > 0.01:
            # Moving again - clear any stale "unexpected stop" alert.
            await resolve_alert(session, registry, ship_id=ship.ship_id, alert_type="unexpected_stop")

    # Hijack Alert Logic
    settings = get_settings()
    is_moving = speed > 0.5 or (last_pos and detection.haversine_km(lat, lon, last_pos.lat, last_pos.lon) > 0.1)
    
    if is_moving:
        ship.last_moved_at = _now_utc()
        if ship.status == "⚠ POSSIBLE HIJACK":
            ship.status = "underway"
            await resolve_alert(session, registry, ship_id=ship.ship_id, alert_type="possible_hijack")
            from app.blockchain import ledger
            await ledger.append_block(
                session, ship_id=ship.ship_id, ship_name=ship.name, event_type="HIJACK_CLEARED",
                lat=lat, lon=lon, speed=speed, payload={"reason": "Ship resumed movement"},
                signature="system", signer_pub_key="system"
            )
    else:
        if not ship.last_moved_at:
            ship.last_moved_at = _now_utc()
        else:
            stall_duration_ms = (_now_utc() - ship.last_moved_at).total_seconds() * 1000
            if stall_duration_ms > settings.hijack_stall_threshold_ms:
                if ship.status not in ["docked", "idle"]:
                    # Check if near a port
                    near_port = False
                    for port in WORLD_PORTS:
                        dist = detection.haversine_km(lat, lon, port["lat"], port["lon"])
                        if dist < 20: # 20km geofence
                            near_port = True
                            break
                    
                    if not near_port and ship.status != "⚠ POSSIBLE HIJACK":
                        ship.status = "⚠ POSSIBLE HIJACK"
                        alert = await raise_alert(
                            session, registry,
                            ship_id=ship.ship_id, ship_name=ship.name,
                            alert_type="possible_hijack", severity="critical",
                            title="⚠ POSSIBLE HIJACK",
                            detail=f"{ship.name} has been stalled in open water for over {settings.hijack_stall_threshold_ms // 60000} minutes.",
                            lat=lat, lon=lon,
                        )
                        triggered.append(alert)

    return triggered
