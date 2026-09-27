"""
alert_service.py
-------------------
Centralizes how an alert comes into existence, and — as important for a
simulation that runs continuously — how it stays alive or dies.

Old behavior: every detection cycle that found a problem called
raise_alert() and got a brand new row plus a brand new blockchain block,
forever, for as long as the condition lasted. A ship sitting 12 km off
its route for ten minutes at a 30-second check interval produced twenty
"Route Deviation Detected" alerts and twenty blockchain blocks for one
continuous event.

New behavior: raise_alert() is stateful. For a given (ship_id,
alert_type) pair, only one *unresolved* alert may exist at a time.
  - First time the condition is seen -> a new alert row + a new
    blockchain block are created, exactly as before.
  - While the SAME condition persists -> the existing row is updated
    (severity/detail/lat/lon/last_seen_at/occurrence_count) and NO new
    row or block is created.
  - Call resolve_alert() the moment the condition clears -> the row is
    marked resolved (with a resolution timestamp) and a lightweight
    "Alert Resolved" blockchain block is appended. The next time the
    condition appears, it's genuinely new and opens a fresh alert.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.blockchain import ledger
from app.core.dependencies import ConnectionRegistry
from app.db.models import Alert


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def alert_to_dict(alert: Alert) -> dict:
    return {
        "alert_id": alert.alert_id,
        "ship_id": alert.ship_id,
        "ship_name": alert.ship_name,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "title": alert.title,
        "detail": alert.detail,
        "lat": alert.lat,
        "lon": alert.lon,
        "signature": alert.signature,
        "block_index": alert.block_index,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "last_seen_at": alert.last_seen_at.isoformat() if alert.last_seen_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "occurrence_count": alert.occurrence_count,
        "resolved": bool(alert.resolved),
        "acknowledged": alert.acknowledged,
    }


async def _get_active_alert(session: AsyncSession, ship_id, alert_type) -> Alert | None:
    if ship_id is None:
        return None
    result = await session.execute(
        select(Alert).where(
            Alert.ship_id == ship_id,
            Alert.alert_type == alert_type,
            Alert.resolved == 0,
        ).order_by(Alert.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def raise_alert(session: AsyncSession, registry: ConnectionRegistry, *, ship_id=None, ship_name=None,
                       alert_type: str, severity: str, title: str, detail: str,
                       lat=None, lon=None, signature=None) -> dict:
    """Create a new alert, or refresh the existing unresolved one of the
    same (ship_id, alert_type) if the condition is still ongoing. Every
    call still returns a usable alert dict either way, so existing
    callers that don't care about the distinction don't need to change.
    """
    now = _now_utc()

    existing = await _get_active_alert(session, ship_id, alert_type)
    if existing is not None:
        existing.severity = severity
        existing.title = title
        existing.detail = detail
        if lat is not None:
            existing.lat = lat
        if lon is not None:
            existing.lon = lon
        existing.last_seen_at = now
        existing.occurrence_count = (existing.occurrence_count or 1) + 1
        await session.commit()
        # Deliberately no new blockchain block and no new websocket
        # "new_alert" event here - an ongoing condition is a refresh, not
        # a new incident. A lighter "alert_updated" event still lets the
        # UI bump a "last confirmed" timestamp without treating it as new.
        alert_dict = alert_to_dict(existing)
        if registry is not None:
            await registry.broadcast({"kind": "alert_updated", "alert": alert_dict})
        return alert_dict

    block = await ledger.append_block(
        session, ship_id=ship_id, ship_name=ship_name, event_type="ALERT",
        lat=lat, lon=lon, speed=None,
        payload={"alert_type": alert_type, "title": title}, signature=signature,
    )

    alert = Alert(
        ship_id=ship_id,
        ship_name=ship_name,
        alert_type=alert_type,
        severity=severity,
        title=title,
        detail=detail,
        lat=lat,
        lon=lon,
        signature=signature,
        block_index=block["block_index"],
        created_at=now,
        last_seen_at=now,
        occurrence_count=1,
        resolved=0,
        acknowledged=0,
    )
    session.add(alert)
    await session.commit()

    alert_dict = alert_to_dict(alert)
    if registry is not None:
        await registry.broadcast({"kind": "new_alert", "alert": alert_dict})
    return alert_dict


async def resolve_alert(session: AsyncSession, registry: ConnectionRegistry, *,
                         ship_id: str, alert_type: str) -> dict | None:
    """Marks the active alert of this type for this ship as resolved (if
    one exists) and logs a single 'Alert Resolved' blockchain block for
    it. Call this the moment a detection cycle finds the condition has
    cleared (ship back in corridor, zone exited, speed recovered, etc).
    Returns None if there was nothing active to resolve, so callers can
    call this unconditionally every cycle without extra bookkeeping.
    """
    existing = await _get_active_alert(session, ship_id, alert_type)
    if existing is None:
        return None

    now = _now_utc()
    existing.resolved = 1
    existing.resolved_at = now
    await session.commit()

    block = await ledger.append_block(
        session, ship_id=ship_id, ship_name=existing.ship_name, event_type="ALERT_RESOLVED",
        lat=existing.lat, lon=existing.lon, speed=None,
        payload={"alert_type": alert_type, "original_alert_id": existing.alert_id},
    )
    existing.block_index = block["block_index"]
    await session.commit()

    alert_dict = alert_to_dict(existing)
    if registry is not None:
        await registry.broadcast({"kind": "alert_resolved", "alert": alert_dict})
    return alert_dict
