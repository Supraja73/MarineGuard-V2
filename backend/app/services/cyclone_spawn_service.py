"""
cyclone_spawn_service.py
---------------------------
Where the "Trigger Cyclone" test button actually puts the storm. Instead
of a random map point (which usually landed nowhere near any ship and
did nothing), this picks a point along an active voyage's route and
offsets it to the side by a realistic distance - close enough that the
route's safety buffer is genuinely breached (a decision has to be made),
far enough that a detour around it is still geometrically possible
(this isn't meant to be an unsolvable trap).
"""

import math
import random

from app.detection import detection

# Cyclone center lands this far along the origin->destination corridor,
# as a fraction of the route (0=origin, 1=destination). Kept away from
# both ends so the storm doesn't spawn on top of a port.
ROUTE_FRACTION_RANGE = (0.35, 0.65)

# How far off to the side of the route the cyclone center is placed (km).
OFFSET_RANGE_KM = (75.0, 100.0)

# Minimum straight-line distance the spawn point must keep from the
# ship's current position, the destination, and the origin port, so the
# storm never lands "on" any of them even at extreme fraction/offset
# combinations.
MIN_CLEARANCE_FROM_ENDPOINTS_KM = 120.0


def _interpolate(lat1, lon1, lat2, lon2, t):
    """Linear lat/lon interpolation - adequate for the few-hundred-km
    spans this spawns cyclones over; not meant to be geodesically exact."""
    return lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t


def _offset_point(lat, lon, bearing_deg_value, distance_km):
    d_lat = (distance_km * math.cos(math.radians(bearing_deg_value))) / 111.0
    d_lon = (distance_km * math.sin(math.radians(bearing_deg_value))) / (
        111.0 * math.cos(math.radians(lat)) or 1.0
    )
    return lat + d_lat, lon + d_lon


def spawn_point_for_voyage(voyage, safety_buffer_km: float) -> dict:
    """Given a Voyage (origin_lat/lon, dest_lat/lon, current_lat/lon),
    returns {"lat", "lon", "radius_km"} for a cyclone that intersects the
    route's safety buffer without sitting on the ship, the destination,
    or the departure port. Falls back to a slightly wider offset if the
    first attempt lands too close to an endpoint.
    """
    origin_lat = voyage.current_lat if voyage.current_lat is not None else voyage.origin_lat
    origin_lon = voyage.current_lon if voyage.current_lon is not None else voyage.origin_lon
    dest_lat, dest_lon = voyage.dest_lat, voyage.dest_lon

    route_bearing = detection.bearing_deg(origin_lat, origin_lon, dest_lat, dest_lon)
    # Perpendicular to the route, so the storm sits off to one side of
    # the corridor rather than ahead of or behind the ship.
    side = random.choice([90, -90])
    perpendicular_bearing = (route_bearing + side) % 360

    for _attempt in range(6):
        t = random.uniform(*ROUTE_FRACTION_RANGE)
        route_lat, route_lon = _interpolate(origin_lat, origin_lon, dest_lat, dest_lon, t)

        offset_km = random.uniform(*OFFSET_RANGE_KM)
        cyclone_lat, cyclone_lon = _offset_point(route_lat, route_lon, perpendicular_bearing, offset_km)

        clears_endpoints = (
            detection.haversine_km(cyclone_lat, cyclone_lon, origin_lat, origin_lon) >= MIN_CLEARANCE_FROM_ENDPOINTS_KM
            and detection.haversine_km(cyclone_lat, cyclone_lon, dest_lat, dest_lon) >= MIN_CLEARANCE_FROM_ENDPOINTS_KM
            and detection.haversine_km(cyclone_lat, cyclone_lon, voyage.origin_lat, voyage.origin_lon) >= MIN_CLEARANCE_FROM_ENDPOINTS_KM
        )
        if not clears_endpoints:
            continue

        # Radius chosen so (radius + safety buffer) reliably reaches back
        # to the route (offset_km) - i.e. it genuinely threatens the
        # voyage - while staying small enough that the opposite side of
        # the route is still clearly open for a detour.
        radius_km = max(150.0, offset_km + safety_buffer_km - 10.0)
        radius_km = min(radius_km, offset_km + safety_buffer_km + 40.0)

        return {"lat": cyclone_lat, "lon": cyclone_lon, "radius_km": round(radius_km, 1)}

    # Extremely short voyage (origin/dest are already close together) -
    # fall back to the midpoint offset regardless of clearance; still
    # better than a fully random map point.
    route_lat, route_lon = _interpolate(origin_lat, origin_lon, dest_lat, dest_lon, 0.5)
    offset_km = sum(OFFSET_RANGE_KM) / 2
    cyclone_lat, cyclone_lon = _offset_point(route_lat, route_lon, perpendicular_bearing, offset_km)
    radius_km = offset_km + safety_buffer_km - 10.0
    return {"lat": cyclone_lat, "lon": cyclone_lon, "radius_km": round(radius_km, 1)}
