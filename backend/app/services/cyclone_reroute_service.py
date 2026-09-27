"""
cyclone_reroute_service.py
---------------------------
Turns "a cyclone is blocking this ship's route" into a concrete proposal:
either a real sea route that clears every active cyclone, or - if no such
route can be found - the nearest port that is itself clear of every
active cyclone, to hold at until the storm passes.

Nothing in this module writes to the database or touches a live voyage.
It only computes candidates; app.routers.reroutes turns the chosen
candidate into a pending RerouteRequest row, and a Control Station
approval is what actually applies it to the voyage.
"""

import math

from app.detection import detection
from app.data.ports import WORLD_PORTS
from app.services.ocean_route_service import get_ocean_route
from app.config.settings import get_settings

# How far outside a cyclone's stated radius a route/port must stay to be
# considered "clear" of it. Storm cones move and grow, so hugging the
# exact radius boundary isn't good enough. Configurable via
# CYCLONE_SAFETY_BUFFER_KM (see app.config.settings); this constant is
# only the fallback default.
SAFETY_BUFFER_KM = 75.0


def _buffer_km() -> float:
    try:
        return get_settings().cyclone_safety_buffer_km
    except Exception:
        return SAFETY_BUFFER_KM

# Candidate bearings (degrees from the cyclone center, 0=N, 90=E, ...)
# to try pushing a detour waypoint out to. Tried in this order; the first
# one that produces a route clearing every active cyclone wins.
_CANDIDATE_BEARINGS = [90, 270, 45, 135, 225, 315, 0, 180]


def _min_distance_to_route_km(route: list[list[float]], lat: float, lon: float) -> float:
    """Closest distance (km) from (lat, lon) to any leg of a route
    given as a list of [lat, lon] waypoints."""
    best = math.inf
    for a, b in zip(route, route[1:]):
        d = detection.point_to_segment_distance_km(lat, lon, a[0], a[1], b[0], b[1])
        if d < best:
            best = d
    if best is math.inf and route:
        best = detection.haversine_km(route[0][0], route[0][1], lat, lon)
    return best


def _route_clears_all_cyclones(route: list[list[float]], cyclones: list[dict]) -> bool:
    for c in cyclones:
        try:
            c_lat, c_lon = float(c["lat"]), float(c["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        radius_km = float(c.get("radius_km") or 300.0)
        if _min_distance_to_route_km(route, c_lat, c_lon) <= radius_km + _buffer_km():
            return False
    return True


# Public alias - used by reroute_orchestrator to check whether a
# *previously applied* alternate route still clears current cyclones,
# so it knows not to keep re-triggering a fresh reroute every monitor
# tick (see propose_reroute_for_voyage's _already_safely_rerouted guard).
route_clears_all_cyclones = _route_clears_all_cyclones


def route_is_blocked(origin_lat, origin_lon, dest_lat, dest_lon, cyclones: list[dict]) -> dict | None:
    """Is ANY point along the ship's actual remaining path - the same
    curved, land-avoiding searoute path the ship is really following,
    not a straight line from current position to destination - within
    any active cyclone's radius + safety buffer? Returns the blocking
    cyclone dict if so, else None.

    This is deliberately checked against the FULL remaining route (every
    upcoming leg), not just the ship's current point, so a cyclone
    sitting on a later stretch of the voyage triggers a reroute well
    before the ship ever gets close to it - not only once the ship has
    already arrived at the danger radius. Straight-line distance from
    current position to destination understated risk on curved,
    coast-hugging paths (a cyclone could sit right on a bend the ship
    hasn't reached yet, far from that straight line), which is exactly
    what let ships sail into a cyclone before a reroute was proposed.

    Cached in ocean_route_service, so this is cheap on repeated ticks for
    the same voyage corridor.
    """
    try:
        route = get_ocean_route(origin_lat, origin_lon, dest_lat, dest_lon)["route"]
    except ValueError:
        route = [[origin_lat, origin_lon], [dest_lat, dest_lon]]

    for c in cyclones:
        try:
            c_lat, c_lon = float(c["lat"]), float(c["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        radius_km = float(c.get("radius_km") or 300.0)
        if _min_distance_to_route_km(route, c_lat, c_lon) <= radius_km + _buffer_km():
            return c
    return None


def find_alternate_route(origin_lat, origin_lon, dest_lat, dest_lon, cyclones: list[dict]) -> dict | None:
    """Tries the direct sea route first, then a series of detour
    waypoints pushed outward from each blocking cyclone, until one clears
    every active cyclone by SAFETY_BUFFER_KM. Returns
    {"waypoints": [[lat,lon],...], "distance_km": float} or None if
    nothing tried clears all cyclones.
    """
    try:
        direct = get_ocean_route(origin_lat, origin_lon, dest_lat, dest_lon)
    except ValueError:
        direct = None

    if direct and _route_clears_all_cyclones(direct["route"], cyclones):
        return {"waypoints": direct["route"], "distance_km": direct["distance_km"]}

    # Find the cyclone that's actually blocking the direct path (the one
    # closest to it) and try routing around it via a detour waypoint.
    blocking = None
    if direct:
        best_margin = math.inf
        for c in cyclones:
            try:
                c_lat, c_lon = float(c["lat"]), float(c["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            radius_km = float(c.get("radius_km") or 300.0)
            margin = _min_distance_to_route_km(direct["route"], c_lat, c_lon) - radius_km
            if margin < best_margin:
                best_margin = margin
                blocking = c
    if blocking is None and cyclones:
        blocking = cyclones[0]
    if blocking is None:
        # No cyclones at all - direct route (even if searoute failed to
        # compute, there's nothing to route around).
        return {"waypoints": direct["route"], "distance_km": direct["distance_km"]} if direct else None

    c_lat, c_lon = float(blocking["lat"]), float(blocking["lon"])
    radius_km = float(blocking.get("radius_km") or 300.0)
    detour_radius_km = radius_km + _buffer_km() + 25.0

    for bearing in _CANDIDATE_BEARINGS:
        d_lat = (detour_radius_km * math.cos(math.radians(bearing))) / 111.0
        d_lon = (detour_radius_km * math.sin(math.radians(bearing))) / (111.0 * math.cos(math.radians(c_lat)) or 1.0)
        waypoint_lat = c_lat + d_lat
        waypoint_lon = c_lon + d_lon

        try:
            # The detour waypoint is a synthetic point (simple trig
            # offset from the cyclone's center) - it can land anywhere,
            # including on land or an island. Do NOT pin it onto the
            # route as an exact coordinate (pin_dest=False): that would
            # inject a straight, unvalidated segment from an arbitrary
            # point to the nearest real sea-lane node, which is exactly
            # what let alternate routes cut across land. Instead take
            # leg1 unpinned at its far end, so it stops at searoute's own
            # graph-snapped coordinate near the waypoint - guaranteed to
            # be on navigable water - and start leg2 from THAT exact
            # snapped point (pinned, since it's already water-valid),
            # keeping the two legs joined with no gap and no land cut.
            leg1 = get_ocean_route(origin_lat, origin_lon, waypoint_lat, waypoint_lon, pin_dest=False)
            snapped_lat, snapped_lon = leg1["route"][-1]
            leg2 = get_ocean_route(snapped_lat, snapped_lon, dest_lat, dest_lon)
        except (ValueError, IndexError):
            continue

        combined_route = leg1["route"] + leg2["route"][1:]

        # Validate before ever returning this as a candidate: every
        # segment must stay on the maritime graph (guaranteed here, since
        # every point came straight out of searoute with no pinned
        # synthetic coordinates) AND clear every active cyclone's danger
        # radius + buffer. If it doesn't clear the cyclones, discard it
        # and try the next candidate bearing rather than displaying it.
        if _route_clears_all_cyclones(combined_route, cyclones):
            return {
                "waypoints": combined_route,
                "distance_km": leg1["distance_km"] + leg2["distance_km"],
            }

    return None


def find_safest_port(current_lat, current_lon, cyclones: list[dict], exclude_port_name: str | None = None) -> dict | None:
    """Nearest world port that is NOT inside any active cyclone's radius
    (+ safety buffer). Returns a port dict (name/country/lat/lon) plus
    distance_km, or None if literally every port is currently unsafe.
    """
    candidates = []
    for port in WORLD_PORTS:
        if exclude_port_name and port["name"] == exclude_port_name:
            continue
        safe = True
        for c in cyclones:
            try:
                c_lat, c_lon = float(c["lat"]), float(c["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            radius_km = float(c.get("radius_km") or 300.0)
            if detection.haversine_km(port["lat"], port["lon"], c_lat, c_lon) <= radius_km + _buffer_km():
                safe = False
                break
        if not safe:
            continue
        dist = detection.haversine_km(current_lat, current_lon, port["lat"], port["lon"])
        candidates.append((dist, port))

    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    dist, port = candidates[0]
    return {**port, "distance_km": round(dist, 1)}
