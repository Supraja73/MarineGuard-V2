"""
weather.py (router)
-----------------------
Records real weather readings (fetched by the browser from Open-Meteo
and posted here) and computes cyclone risk using published meteorological
thresholds in app.detection.detection.cyclone_risk_score.

Alert logic — only ships that are actually near the storm are alerted:
  - MODERATE risk  : ships within 300 km of the weather cell centre
  - HIGH risk      : ships within 500 km
  - EXTREME risk   : ships within 800 km
  - No lat/lon in request : fall back to alerting all ships (legacy)
"""

import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.detection import detection
from app.schemas.weather import WeatherReadingRequest
from app.core.dependencies import get_db
from app.db.models import WeatherReading, Ship
from app.services.alert_service import raise_alert, resolve_alert

router = APIRouter(prefix="/api/weather", tags=["weather"])

# Radius (km) within which a ship is considered "at risk" per risk level
ALERT_RADIUS_KM = {
    "MODERATE": 300,
    "HIGH": 500,
    "EXTREME": 800,
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _reading_to_dict(reading: WeatherReading) -> dict:
    return {
        "reading_id": reading.reading_id,
        "region": reading.region,
        "wind_speed_kmh": reading.wind_speed_kmh,
        "pressure_hpa": reading.pressure_hpa,
        "wave_height_m": reading.wave_height_m,
        "rainfall_mm": reading.rainfall_mm,
        "lat": reading.lat,
        "lon": reading.lon,
        "recorded_at": reading.recorded_at.isoformat() if reading.recorded_at else None,
    }


def _ship_in_storm_radius(ship: Ship, storm_lat: float, storm_lon: float, radius_km: float) -> bool:
    """True if the ship's current position is within radius_km of the storm centre."""
    if ship.current_lat is None or ship.current_lon is None:
        # No known position — include conservatively
        return True
    dist = detection.haversine_km(ship.current_lat, ship.current_lon, storm_lat, storm_lon)
    return dist <= radius_km


def _ship_heading_toward_storm(ship: Ship, storm_lat: float, storm_lon: float) -> bool:
    """True if the ship's heading (origin→dest vector) points toward the storm."""
    if None in (ship.current_lat, ship.current_lon, ship.dest_lat, ship.dest_lon):
        return False
    bearing_to_dest = detection.bearing_deg(
        ship.current_lat, ship.current_lon, ship.dest_lat, ship.dest_lon
    )
    bearing_to_storm = detection.bearing_deg(
        ship.current_lat, ship.current_lon, storm_lat, storm_lon
    )
    # Within 60° either side of the ship's course is "heading toward"
    diff = abs((bearing_to_storm - bearing_to_dest + 360) % 360)
    return diff <= 60 or diff >= 300


# Independent single-field weather thresholds (spec: Extreme Wind, High
# Waves, Heavy Rain as their own threat types) - distinct from cyclone_risk,
# which only fires on the combined wind+pressure score and can miss e.g. a
# high-wave swell with unremarkable pressure. Scoped to ships within
# THREAT_RADIUS_KM of the reading so a reading from one region doesn't
# alert a ship on the other side of the ocean.
THREAT_RADIUS_KM = 200
EXTREME_WIND_KMH = 75
HIGH_WAVES_M = 6
HEAVY_RAIN_MM = 40


async def _raise_or_resolve_weather_threats(session, registry, req: WeatherReadingRequest):
    if req.lat is None or req.lon is None:
        return
    ships_result = await session.execute(select(Ship).where(Ship.status != "decommissioned"))
    all_ships = ships_result.scalars().all()

    checks = (
        ("extreme_wind", "⚠ Extreme Wind", req.wind_speed_kmh, EXTREME_WIND_KMH, "km/h"),
        ("high_waves", "🌊 High Waves", req.wave_height_m, HIGH_WAVES_M, "m"),
        ("heavy_rain", "🌧 Heavy Rain", req.rainfall_mm, HEAVY_RAIN_MM, "mm/hr"),
    )
    for ship in all_ships:
        if ship.current_lat is None or ship.current_lon is None:
            continue
        dist_km = detection.haversine_km(ship.current_lat, ship.current_lon, req.lat, req.lon)
        for alert_type, label, value, threshold, unit in checks:
            if value is None:
                continue
            if dist_km <= THREAT_RADIUS_KM and value >= threshold:
                await raise_alert(
                    session, registry,
                    ship_id=ship.ship_id, ship_name=ship.name,
                    alert_type=alert_type, severity="warning" if value < threshold * 1.3 else "critical",
                    title=f"{label}: {ship.name}",
                    detail=f"{label} reported in {req.region}: {value:.0f} {unit} "
                           f"(threshold {threshold} {unit}), {int(dist_km)} km from ship.",
                    lat=req.lat, lon=req.lon,
                )
            else:
                await resolve_alert(session, registry, ship_id=ship.ship_id, alert_type=alert_type)


@router.post("/report")
async def report_weather(req: WeatherReadingRequest, request: Request, session: AsyncSession = Depends(get_db)):
    registry = request.app.state.connection_registry

    reading = WeatherReading(
        region=req.region,
        wind_speed_kmh=req.wind_speed_kmh,
        pressure_hpa=req.pressure_hpa,
        wave_height_m=req.wave_height_m,
        rainfall_mm=req.rainfall_mm,
        lat=req.lat,
        lon=req.lon,
        recorded_at=_now_utc(),
    )
    session.add(reading)
    await session.commit()

    risk = detection.cyclone_risk_score(req.wind_speed_kmh, req.pressure_hpa)

    alert_levels = ("MODERATE", "HIGH", "EXTREME")
    if risk["level"] in alert_levels:
        ships_result = await session.execute(
            select(Ship).where(Ship.status != "decommissioned")
        )
        all_ships = ships_result.scalars().all()
        radius_km = ALERT_RADIUS_KM.get(risk["level"], 500)
        storm_lat = req.lat
        storm_lon = req.lon

        for ship in all_ships:
            # If we have a storm position, only alert ships that are
            # within the risk radius OR heading toward the storm.
            # If no storm position was provided, alert everyone (legacy fallback).
            if storm_lat is not None and storm_lon is not None:
                in_radius = _ship_in_storm_radius(ship, storm_lat, storm_lon, radius_km)
                heading_toward = _ship_heading_toward_storm(ship, storm_lat, storm_lon)
                if not in_radius and not heading_toward:
                    continue  # ship is safe — skip alert

                dist_km = (
                    round(detection.haversine_km(ship.current_lat, ship.current_lon, storm_lat, storm_lon), 0)
                    if ship.current_lat is not None else None
                )
                proximity_note = f" Ship is {int(dist_km)} km from storm centre." if dist_km is not None else ""
                heading_note = " Ship is heading toward the storm." if heading_toward else ""
            else:
                proximity_note = ""
                heading_note = ""

            severity = "warning" if risk["level"] in ("MODERATE", "HIGH") else "critical"
            await raise_alert(
                session, registry,
                ship_id=ship.ship_id, ship_name=ship.name,
                alert_type="cyclone_risk",
                severity=severity,
                title=f"Cyclone Risk: {risk['level']}",
                detail=(
                    f"Wind {req.wind_speed_kmh:.0f} km/h, pressure {req.pressure_hpa:.0f} hPa "
                    f"in {req.region}. Risk score {risk['score']}/100."
                    f"{proximity_note}{heading_note}"
                ),
                lat=storm_lat, lon=storm_lon,
            )

    await _raise_or_resolve_weather_threats(session, registry, req)

    return {"risk": risk, "region": req.region, "lat": req.lat, "lon": req.lon}


@router.get("/latest")
async def latest_weather(region: Optional[str] = None, session: AsyncSession = Depends(get_db)):
    query = select(WeatherReading)
    if region:
        query = query.where(WeatherReading.region == region)
    query = query.order_by(WeatherReading.recorded_at.desc()).limit(1)
    result = await session.execute(query)
    reading = result.scalar_one_or_none()
    if not reading:
        return None
    data = _reading_to_dict(reading)
    data["risk"] = detection.cyclone_risk_score(reading.wind_speed_kmh, reading.pressure_hpa)
    return data


@router.get("/history")
async def weather_history(region: Optional[str] = None, limit: int = 50, session: AsyncSession = Depends(get_db)):
    query = select(WeatherReading)
    if region:
        query = query.where(WeatherReading.region == region)
    query = query.order_by(WeatherReading.recorded_at.desc()).limit(limit)
    result = await session.execute(query)
    readings = result.scalars().all()
    return [_reading_to_dict(r) for r in readings]
