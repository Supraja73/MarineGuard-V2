"""
detect.py (router)
----------------------
On-demand anomaly checks, used by the Detection page's "Run check" actions.
Each check uses real geometry/physics from app.detection.detection and
raises a genuine alert (logged to the blockchain) when it trips.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.detection import detection
from app.schemas.detect import SpoofCheckRequest, RouteCheckRequest, SpeedCheckRequest, SARClassifyRequest
from app.schemas.zones import ZoneCheckRequest
from app.core.dependencies import get_db
from app.core import runtime_state
from app.db.models import Ship, Position, RestrictedZone
from app.services.alert_service import raise_alert
from app.services import ml_classifier_service

router = APIRouter(prefix="/api/detect", tags=["detection"])


async def _get_ship_or_404(session: AsyncSession, ship_id: str) -> Ship:
    ship = await session.get(Ship, ship_id)
    if not ship:
        raise HTTPException(404, "Ship not found")
    return ship


@router.post("/spoofing")
async def detect_spoofing(req: SpoofCheckRequest, request: Request, session: AsyncSession = Depends(get_db)):
    registry = request.app.state.connection_registry

    ship = await _get_ship_or_404(session, req.ship_id)

    if runtime_state.is_demo_mode():
        # GPS-spoofing detection compares the distance between two fixes
        # against the maximum distance physically coverable in the
        # elapsed time - a check that only makes sense against real GPS
        # hardware. Simulated ships can legitimately have their position
        # set directly (fast-forward, replan, manual test data), which
        # would otherwise look identical to a spoofed reading and produce
        # a false positive on every check. Skip entirely in Demo Mode
        # rather than raise alerts against data that was never real
        # AIS/GPS in the first place. Demo/Real is a live toggle (see
        # app.routers.mode) - switch to Real Mode from the Live Tracking
        # page, no restart required, once this deployment is wired to a
        # live AIS/GPS feed.
        return {
            "skipped": True,
            "reason": "GPS spoofing detection is disabled while Demo Mode is active. "
                      "Switch to Real Mode on the Live Tracking page once this deployment "
                      "is wired to a live AIS/GPS feed.",
            "is_spoofed": False,
        }

    last_pos_result = await session.execute(
        select(Position).where(Position.ship_id == req.ship_id).order_by(Position.recorded_at.desc()).limit(1)
    )
    last_pos = last_pos_result.scalar_one_or_none()
    prev_lat = last_pos.lat if last_pos else ship.origin_lat
    prev_lon = last_pos.lon if last_pos else ship.origin_lon

    result = detection.check_gps_spoofing(prev_lat, prev_lon, req.reported_lat, req.reported_lon, req.minutes_elapsed)
    result["previous_lat"] = prev_lat
    result["previous_lon"] = prev_lon

    if result["is_spoofed"]:
        alert = await raise_alert(
            session, registry,
            ship_id=req.ship_id, ship_name=ship.name, alert_type="gps_spoofing",
            severity="critical", title="GPS Spoofing Detected",
            detail=f"{ship.name} reported a position requiring an implied speed of "
                   f"{result['implied_speed_kmh']} km/h, which exceeds the maximum realistic "
                   f"vessel speed of {result['max_realistic_speed_kmh']} km/h. Reading quarantined.",
            lat=req.reported_lat, lon=req.reported_lon,
        )
        result["alert"] = alert
    return result


@router.post("/route")
async def detect_route(req: RouteCheckRequest, request: Request, session: AsyncSession = Depends(get_db)):
    registry = request.app.state.connection_registry

    ship = await _get_ship_or_404(session, req.ship_id)

    result = detection.check_route_deviation(
        req.lat, req.lon, ship.origin_lat, ship.origin_lon, ship.dest_lat, ship.dest_lon
    )
    if result["is_deviation"]:
        alert = await raise_alert(
            session, registry,
            ship_id=req.ship_id, ship_name=ship.name, alert_type="route_deviation",
            severity="warning", title="Route Deviation Detected",
            detail=f"{ship.name} is {result['distance_from_corridor_km']} km off its expected corridor.",
            lat=req.lat, lon=req.lon,
        )
        result["alert"] = alert
    return result


@router.post("/zone")
async def detect_zone(req: ZoneCheckRequest, session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(RestrictedZone))
    zones = result.scalars().all()
    zone_list = [{"name": z.name, "lat": z.lat, "lon": z.lon,
                  "radius_km": z.radius_km, "zone_type": z.zone_type} for z in zones]
    zone_hit = detection.check_restricted_zone(req.lat, req.lon, zone_list)
    return {"zone_hit": zone_hit}


@router.post("/speed")
async def detect_speed(req: SpeedCheckRequest, request: Request, session: AsyncSession = Depends(get_db)):
    registry = request.app.state.connection_registry

    ship = await _get_ship_or_404(session, req.ship_id)

    result = detection.check_speed_anomaly(req.reported_speed, req.expected_speed)
    if result["is_anomaly"]:
        alert = await raise_alert(
            session, registry,
            ship_id=req.ship_id, ship_name=ship.name,
            alert_type=result["reason"], severity="warning",
            title="Unexpected Stop Detected" if result["reason"] == "unexpected_stop" else "Speed Anomaly Detected",
            detail=f"{ship.name} reported {req.reported_speed} km/h against an expected "
                   f"{req.expected_speed} km/h" + (f" at {req.location_label}." if req.location_label else "."),
        )
        result["alert"] = alert
    return result


@router.post("/sar-classify")
async def classify_sar_image(req: SARClassifyRequest, request: Request, session: AsyncSession = Depends(get_db)):
    """
    Classifies a dual-band Synthetic Aperture Radar (SAR) image target using PyTorch CustomCNN.
    Predicts: Iceberg (1) vs Vessel (0) with confidence percentage.
    If an Iceberg is detected near shipping lanes, raises a verified threat Alert logged to the Blockchain.
    """
    registry = request.app.state.connection_registry

    if not req.band_1 or not req.band_2:
        raise HTTPException(400, "Both 'band_1' and 'band_2' arrays (5625 float values each) are required.")

    try:
        classification = ml_classifier_service.classify_sar_target(
            req.band_1, req.band_2, sample_id=req.sample_id
        )
    except Exception as exc:
        raise HTTPException(500, f"SAR Classification failed: {str(exc)}")

    # If classified as an Iceberg (Threat) with high confidence, raise a PoET Blockchain alert
    if classification["is_threat"]:
        ship_id = req.ship_id
        ship_name = "Navigation Hazard Sector"
        target_lat = req.lat or 47.5
        target_lon = req.lon or -52.5

        if ship_id:
            ship = await session.get(Ship, ship_id)
            if ship:
                ship_name = ship.name
                if not req.lat or not req.lon:
                    target_lat = ship.origin_lat
                    target_lon = ship.origin_lon

        alert = await raise_alert(
            session, registry,
            ship_id=ship_id or "SAR-ICEBERG-01",
            ship_name=ship_name,
            alert_type="iceberg_detected",
            severity="critical",
            title="SAR Radar: Iceberg Hazard Detected",
            detail=f"PyTorch Deep Learning Classifier identified an Iceberg target (ID: {classification['sample_id']}) "
                   f"with {classification['confidence']}% confidence in active maritime transit sector.",
            lat=target_lat,
            lon=target_lon,
        )
        classification["alert"] = alert

    return classification

