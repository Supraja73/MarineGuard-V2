"""
zones.py (router)
---------------------
Create, list, and delete restricted/geo-fenced zones used by the
restricted-zone detection check and the automatic detection suite that
runs on every position report.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.zones import ZoneCreateRequest
from app.core.dependencies import get_db
from app.db.models import RestrictedZone

router = APIRouter(prefix="/api/zones", tags=["zones"])


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _zone_to_dict(zone: RestrictedZone) -> dict:
    return {
        "zone_id": zone.zone_id,
        "name": zone.name,
        "zone_type": zone.zone_type,
        "lat": zone.lat,
        "lon": zone.lon,
        "radius_km": zone.radius_km,
        "created_at": zone.created_at.isoformat() if zone.created_at else None,
    }


@router.get("")
async def list_zones(session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(RestrictedZone).order_by(RestrictedZone.created_at.asc()))
    zones = result.scalars().all()
    return [_zone_to_dict(z) for z in zones]


@router.post("")
async def create_zone(req: ZoneCreateRequest, session: AsyncSession = Depends(get_db)):
    zone = RestrictedZone(
        name=req.name,
        zone_type=req.zone_type,
        lat=req.lat,
        lon=req.lon,
        radius_km=req.radius_km,
        created_at=_now_utc(),
    )
    session.add(zone)
    await session.commit()
    return _zone_to_dict(zone)


@router.delete("/{zone_id}")
async def delete_zone(zone_id: int, session: AsyncSession = Depends(get_db)):
    zone = await session.get(RestrictedZone, zone_id)
    if not zone:
        raise HTTPException(404, "Zone not found")
    await session.delete(zone)
    await session.commit()
    return {"deleted": True}
