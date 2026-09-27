"""
incident_service.py
----------------------
Attack Mode + system-incident simulator.

Spec calls for two closely related things under "ATTACK MODE":
  1. Simulated attacks (Pirate/Missile/Drone/Hijack/Sabotage/Cargo
     Theft/Distress) with a pulsing-red ship marker, a CRITICAL alert,
     a blockchain event, an incident timeline, and operator actions
     (Acknowledge / Escalate / Dispatch Coast Guard / Request Naval
     Support / Broadcast Distress / Activate Emergency Protocol / End
     Incident).
  2. A separate list of "threats" with no real sensor data behind them
     in this system (Engine Failure, Fire, Man Overboard, Unauthorized
     Boarding, Cargo Temperature Failure, Anchor Drift, Communication
     Loss, AIS Offline) - there's no engine telemetry or bilge sensor
     to detect these FROM, so - consistent with the spec's own design
     for attacks - they're modeled as the same kind of operator-
     triggered test incident rather than invented as background noise
     that would just be fabricated data pretending to be real.

Both families share one mechanism here: start_incident() raises a
CRITICAL alert, flips the ship into an "under attack/incident" status
(so the frontend can render the pulsing marker), and logs an
"ATTACK_STARTED" blockchain event. act_on_incident() logs each operator
action as its own lightweight blockchain event without creating alert
spam. end_incident() resolves the alert, restores ship status, and
logs "ATTACK_RESOLVED".
"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.blockchain import ledger
from app.db.models import Ship, Alert
from app.services.alert_service import raise_alert, resolve_alert
from app.schemas.incidents import ALL_INCIDENT_TYPES, ATTACK_TYPES

INCIDENT_ALERT_PREFIX = "incident::"
UNDER_INCIDENT_STATUS = "🚨 UNDER ATTACK"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _alert_type_for(incident_type: str) -> str:
    return f"{INCIDENT_ALERT_PREFIX}{incident_type}"


async def start_incident(session, registry, ship: Ship, incident_type: str) -> dict:
    label = ALL_INCIDENT_TYPES[incident_type]
    is_attack = incident_type in ATTACK_TYPES
    ship.status = UNDER_INCIDENT_STATUS
    await session.commit()

    alert = await raise_alert(
        session, registry,
        ship_id=ship.ship_id, ship_name=ship.name,
        alert_type=_alert_type_for(incident_type), severity="critical",
        title=f"🚨 {label}: {ship.name}",
        detail=f"{'Attack' if is_attack else 'Incident'} simulation started for {ship.name}: {label}.",
        lat=ship.current_lat, lon=ship.current_lon,
    )

    block = await ledger.append_block(
        session, ship_id=ship.ship_id, ship_name=ship.name,
        event_type="ATTACK_STARTED" if is_attack else "INCIDENT_STARTED",
        lat=ship.current_lat, lon=ship.current_lon, speed=ship.current_speed,
        payload={"incident_type": incident_type, "label": label},
    )

    if registry is not None:
        await registry.broadcast({
            "kind": "incident_started", "ship_id": ship.ship_id, "ship_name": ship.name,
            "incident_type": incident_type, "label": label, "is_attack": is_attack,
            "alert": alert, "block_index": block["block_index"],
        })

    return {"alert": alert, "block_index": block["block_index"], "incident_type": incident_type, "label": label}


async def act_on_incident(session, registry, ship: Ship, incident_type: str, action: str, note: str | None) -> dict:
    """Logs an operator response action against an in-progress incident as
    its own blockchain event. Doesn't touch the alert (still open/critical)
    or ship status (still under incident) - only end_incident() does that -
    so multiple actions can be logged in sequence during a real response.
    """
    action_labels = {
        "acknowledge": "Acknowledged",
        "escalate": "Escalated",
        "dispatch_coast_guard": "Coast Guard Dispatched",
        "request_naval_support": "Naval Support Requested",
        "broadcast_distress": "Distress Broadcast",
        "activate_emergency_protocol": "Emergency Protocol Activated",
    }
    label = action_labels.get(action, action)
    block = await ledger.append_block(
        session, ship_id=ship.ship_id, ship_name=ship.name, event_type="ATTACK_ACTION",
        lat=ship.current_lat, lon=ship.current_lon, speed=ship.current_speed,
        payload={"incident_type": incident_type, "action": action, "note": note},
    )
    if registry is not None:
        await registry.broadcast({
            "kind": "incident_action", "ship_id": ship.ship_id, "ship_name": ship.name,
            "incident_type": incident_type, "action": action, "label": label, "note": note,
            "block_index": block["block_index"],
        })
    return {"action": action, "label": label, "block_index": block["block_index"]}


async def end_incident(session, registry, ship: Ship, incident_type: str) -> dict:
    label = ALL_INCIDENT_TYPES.get(incident_type, incident_type)
    is_attack = incident_type in ATTACK_TYPES

    await resolve_alert(session, registry, ship_id=ship.ship_id, alert_type=_alert_type_for(incident_type))

    # Restore to a sane operating status. We don't track the exact prior
    # status (docked/idle/underway) separately from the position-tick
    # simulator's own state machine, so "underway" is the correct default
    # to hand back to it - the next position tick will re-derive docked/
    # idle status on its own if the ship is actually stationary.
    ship.status = "underway"
    await session.commit()

    block = await ledger.append_block(
        session, ship_id=ship.ship_id, ship_name=ship.name,
        event_type="ATTACK_RESOLVED" if is_attack else "INCIDENT_RESOLVED",
        lat=ship.current_lat, lon=ship.current_lon, speed=ship.current_speed,
        payload={"incident_type": incident_type, "label": label},
    )
    if registry is not None:
        await registry.broadcast({
            "kind": "incident_ended", "ship_id": ship.ship_id, "ship_name": ship.name,
            "incident_type": incident_type, "label": label, "block_index": block["block_index"],
        })
    return {"block_index": block["block_index"]}


async def active_incidents_for_ship(session, ship_id: str) -> list[dict]:
    result = await session.execute(
        select(Alert).where(
            Alert.ship_id == ship_id, Alert.resolved == 0,
            Alert.alert_type.like(f"{INCIDENT_ALERT_PREFIX}%"),
        )
    )
    out = []
    for a in result.scalars().all():
        incident_type = a.alert_type[len(INCIDENT_ALERT_PREFIX):]
        out.append({"incident_type": incident_type, "label": ALL_INCIDENT_TYPES.get(incident_type, incident_type),
                     "alert_id": a.alert_id, "is_attack": incident_type in ATTACK_TYPES,
                     "created_at": a.created_at.isoformat() if a.created_at else None})
    return out
