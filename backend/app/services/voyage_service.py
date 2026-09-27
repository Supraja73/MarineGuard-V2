"""
voyage_service.py
------------------
Pure calculation helpers for voyage progress: total/travelled/remaining
distance and ETA, all derived from real great-circle geometry
(app.detection.detection.haversine_km), never randomized or guessed.
"""

from datetime import datetime, timedelta, timezone

from app.detection import detection
from app.db.models import Voyage

# A voyage is considered "arrived" once the ship is within this distance
# of its declared destination - close enough that further great-circle
# distance is dominated by GPS/interpolation noise, not real travel.
ARRIVAL_THRESHOLD_KM = 5.0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def voyage_total_distance_km(voyage: Voyage) -> float:
    return detection.haversine_km(voyage.origin_lat, voyage.origin_lon, voyage.dest_lat, voyage.dest_lon)


def update_voyage_progress(voyage: Voyage, lat: float, lon: float, speed_kmh: float | None) -> bool:
    """Updates a voyage's live progress fields from a new ship position.
    Returns True if the voyage should now be marked as completed (the
    ship has arrived within ARRIVAL_THRESHOLD_KM of its destination).
    """
    voyage.current_lat = lat
    voyage.current_lon = lon
    if speed_kmh is not None and speed_kmh > 0:
        voyage.current_speed_kmh = speed_kmh

    total = voyage.distance_total_km or voyage_total_distance_km(voyage)
    voyage.distance_total_km = total

    travelled = detection.haversine_km(voyage.origin_lat, voyage.origin_lon, lat, lon)
    remaining = detection.haversine_km(lat, lon, voyage.dest_lat, voyage.dest_lon)

    voyage.distance_travelled_km = round(min(travelled, total), 2)
    voyage.distance_remaining_km = round(remaining, 2)

    if voyage.current_speed_kmh and voyage.current_speed_kmh > 0:
        hours_remaining = remaining / voyage.current_speed_kmh
        voyage.eta = _now_utc() + timedelta(hours=hours_remaining)
    else:
        voyage.eta = None

    return remaining <= ARRIVAL_THRESHOLD_KM


def voyage_to_dict(voyage: Voyage) -> dict:
    return {
        "voyage_id": voyage.voyage_id,
        "ship_id": voyage.ship_id,
        "origin_port": voyage.origin_port,
        "origin_lat": voyage.origin_lat,
        "origin_lon": voyage.origin_lon,
        "dest_port": voyage.dest_port,
        "dest_lat": voyage.dest_lat,
        "dest_lon": voyage.dest_lon,
        "current_lat": voyage.current_lat,
        "current_lon": voyage.current_lon,
        "current_speed_kmh": voyage.current_speed_kmh,
        "geofence_radius_km": voyage.geofence_radius_km,
        "distance_total_km": round(voyage.distance_total_km, 2) if voyage.distance_total_km else 0,
        "distance_travelled_km": round(voyage.distance_travelled_km, 2) if voyage.distance_travelled_km else 0,
        "distance_remaining_km": round(voyage.distance_remaining_km, 2) if voyage.distance_remaining_km else 0,
        "eta": voyage.eta.isoformat() if voyage.eta else None,
        "status": voyage.status,
        "remarks": voyage.remarks,
        "expected_departure": voyage.expected_departure.isoformat() if voyage.expected_departure else None,
        "created_at": voyage.created_at.isoformat() if voyage.created_at else None,
        "completed_at": voyage.completed_at.isoformat() if voyage.completed_at else None,
    }
