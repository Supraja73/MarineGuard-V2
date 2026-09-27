"""
ocean_route.py (router)
------------------------
Serves realistic, water-only sea routes computed by the `searoute`
package (see app/services/ocean_route_service.py), replacing the old
frontend `seaRouteWaypoints()` hardcoded-chokepoint logic.

GET /api/ocean-route?origin_lat=..&origin_lon=..&dest_lat=..&dest_lon=..
    -> { "route": [[lat, lon], ...], "distance_km": float }

The route is cached server-side (keyed by rounded coordinates) via
app.services.ocean_route_service, and the frontend additionally caches
it per-voyage so this endpoint is called once per voyage, not once per
animation frame.
"""

from fastapi import APIRouter, HTTPException, Query

from app.core.logging_config import get_logger
from app.services.ocean_route_service import get_ocean_route

router = APIRouter(prefix="/api/ocean-route", tags=["ocean-route"])
logger = get_logger("app.routers.ocean_route")


@router.get("")
async def ocean_route(
    origin_lat: float = Query(..., ge=-90, le=90),
    origin_lon: float = Query(..., ge=-180, le=180),
    dest_lat: float = Query(..., ge=-90, le=90),
    dest_lon: float = Query(..., ge=-180, le=180),
):
    try:
        result = get_ocean_route(origin_lat, origin_lon, dest_lat, dest_lon)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        logger.exception("Unexpected error computing ocean route")
        raise HTTPException(status_code=500, detail="Failed to compute ocean route")

    return result
