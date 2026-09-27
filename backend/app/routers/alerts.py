"""
alerts.py (router)
----------------------
Read and manage alerts. Every alert that exists was raised by a real
detection event (signature failure, replay, route deviation, restricted
zone, unexpected stop, GPS spoofing, cyclone risk) or a manual broadcast
triggered by an operator - there is no seed/demo alert data.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.alerts import AckAlertRequest, BroadcastAlertRequest
from app.core.dependencies import get_db, get_current_user_optional, require_control_station
from app.db.models import Alert, Ship
from app.services.alert_service import raise_alert, alert_to_dict

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(severity: Optional[str] = None, limit: int = 100, session: AsyncSession = Depends(get_db), user=Depends(get_current_user_optional)):
    """Control Station and Passenger both see the full alert feed
    (unchanged, always-public behavior). A Ship Captain only sees alerts
    for their own assigned ship - cyclone/weather/reroute notifications
    concerning other vessels aren't theirs to see.
    """
    query = select(Alert)
    if severity:
        query = query.where(Alert.severity == severity)
    if user is not None and user.role == "ship_captain":
        query = query.where(Alert.ship_id == (user.ship_id or "__none__"))
    query = query.order_by(Alert.created_at.desc()).limit(limit)
    result = await session.execute(query)
    alerts = result.scalars().all()
    return [alert_to_dict(a) for a in alerts]


@router.post("/acknowledge")
async def acknowledge_alert(req: AckAlertRequest, session: AsyncSession = Depends(get_db)):
    alert = await session.get(Alert, req.alert_id)
    if alert:
        alert.acknowledged = 1
        await session.commit()
    return {"acknowledged": True}


@router.post("/broadcast")
async def manual_broadcast_alert(req: BroadcastAlertRequest, request: Request, session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    registry = request.app.state.connection_registry

    ship_name = None
    if req.ship_id:
        ship = await session.get(Ship, req.ship_id)
        ship_name = ship.name if ship else None

    alert = await raise_alert(
        session, registry,
        ship_id=req.ship_id, ship_name=ship_name, alert_type="manual_broadcast",
        severity=req.severity, title=req.title, detail=req.detail,
        lat=req.lat, lon=req.lon,
    )
    return alert
