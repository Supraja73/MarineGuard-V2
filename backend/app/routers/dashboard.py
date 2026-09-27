"""
dashboard.py (router)
-------------------------
Aggregate summary statistics for the overview page: ship counts, block
counts, active/critical alert counts, and a genuine chain-integrity check
(not a cached flag - it recomputes the chain on every call).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.blockchain import ledger
from app.core.dependencies import get_db, require_control_station
from app.db.models import Ship, Alert

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
async def dashboard_summary(session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    ship_count_result = await session.execute(select(func.count()).select_from(Ship))
    ship_count = ship_count_result.scalar_one()

    online_count_result = await session.execute(
        select(func.count()).select_from(Ship).where(Ship.status == "online")
    )
    online_count = online_count_result.scalar_one()

    docked_count_result = await session.execute(
        select(func.count()).select_from(Ship).where(Ship.status == "docked")
    )
    docked_count = docked_count_result.scalar_one()

    block_stats = await ledger.chain_stats(session)

    active_alerts_result = await session.execute(
        select(func.count()).select_from(Alert).where(Alert.acknowledged == 0, Alert.resolved == 0)
    )
    active_alerts = active_alerts_result.scalar_one()

    critical_alerts_result = await session.execute(
        select(func.count()).select_from(Alert).where(
            Alert.acknowledged == 0, Alert.resolved == 0, Alert.severity == "critical"
        )
    )
    critical_alerts = critical_alerts_result.scalar_one()

    is_valid, broken_at, _ = await ledger.verify_chain_integrity(session)

    # Threats blocked = security alerts that the system caught and rejected
    threats_result = await session.execute(
        select(func.count()).select_from(Alert).where(
            Alert.alert_type.in_(["gps_spoofing", "signature_failure", "replay_attack"])
        )
    )
    threats_blocked = threats_result.scalar_one()

    # Cyclone alerts that have been signed (all cyclone_risk alerts get ECDSA-logged)
    cyclone_signed_result = await session.execute(
        select(func.count()).select_from(Alert).where(Alert.alert_type == "cyclone_risk")
    )
    cyclone_alerts_signed = cyclone_signed_result.scalar_one()

    gps_spoof_result = await session.execute(
        select(func.count()).select_from(Alert).where(Alert.alert_type == "gps_spoofing")
    )
    gps_spoof_count = gps_spoof_result.scalar_one()

    route_deviation_result = await session.execute(
        select(func.count()).select_from(Alert).where(Alert.alert_type == "route_deviation")
    )
    route_deviation_count = route_deviation_result.scalar_one()

    return {
        "ship_count": ship_count,
        "online_count": online_count,
        "docked_count": docked_count,
        "arrived_count": docked_count,
        "total_blocks": block_stats["total_blocks"],
        "last_event": block_stats["last_event"],
        "last_timestamp": block_stats["last_timestamp"],
        "active_alerts": active_alerts,
        "critical_alerts": critical_alerts,
        "chain_valid": is_valid,
        "chain_broken_at": broken_at,
        "threats_blocked": threats_blocked,
        "cyclone_alerts_signed": cyclone_alerts_signed,
        "gps_spoof_count": gps_spoof_count,
        "route_deviation_count": route_deviation_count,
    }
