"""
ocean_route_service.py
-----------------------
Real sea-lane route computation, replacing the old frontend hack of
bending a straight origin->destination line through a handful of
hardcoded chokepoints (Suez, Malacca, Hormuz, ...).

Uses the open-source `searoute` package (https://pypi.org/project/searoute/),
which routes over a real global maritime network graph (shipping lanes,
straits, and canals) so the returned path never crosses land.

Movement math (speed, elapsed time, interpolation, animation) is NOT
touched here - this module only answers "what is the sea path between
these two points", the same question `seaRouteWaypoints()` used to
answer on the frontend with hardcoded waypoints.
"""

from functools import lru_cache

import searoute as sr

from app.core.logging_config import get_logger

logger = get_logger("app.services.ocean_route_service")

# Coordinates are rounded to 4 decimal places (~11m precision) before
# being used as a cache key. Ships, ports, and voyages use fixed
# origin/destination pairs for an entire voyage, so the same pair is
# requested on every animation tick - lru_cache means searoute's graph
# search only ever runs once per distinct voyage corridor, no matter how
# many times the frontend calls this endpoint.
_CACHE_SIZE = 512


def _round(v: float) -> float:
    return round(float(v), 4)


@lru_cache(maxsize=_CACHE_SIZE)
def _compute_raw_route_cached(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float):
    """Does the actual searoute call and returns the RAW graph route -
    append_orig_dest=False, so every coordinate here comes straight out
    of searoute's maritime network (marnet) graph with nothing pinned or
    forced. Every point returned is therefore guaranteed to sit on
    navigable water. Pinning of real endpoints (ports, a ship's live
    position) happens explicitly in get_ocean_route() below.
    """
    origin = (origin_lon, origin_lat)  # searoute takes (lon, lat)
    dest = (dest_lon, dest_lat)

    result = sr.searoute(origin, dest, units="km", append_orig_dest=False)
    coords = result["geometry"]["coordinates"]  # list of [lon, lat]

    route = [[lat, lon] for lon, lat in coords]
    distance_km = float(result["properties"]["length"])
    return route, distance_km


def get_ocean_route(
    origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float,
    pin_origin: bool = True, pin_dest: bool = True,
) -> dict:
    """Returns a realistic water-only route between two points as a dict:
        {
          "route": [[lat, lon], ...],   # ordered waypoints, origin -> destination
          "distance_km": float,
        }
    Raises ValueError if searoute cannot find a path (e.g. a coordinate
    is nowhere near any navigable water in its maritime network).

    pin_origin/pin_dest control whether the exact requested coordinate is
    forced onto the front/back of the returned route (the historical,
    default behavior). This is correct for real endpoints - ports and a
    ship's live position are always themselves on/adjacent to navigable
    water, so pinning them just makes map markers line up exactly with
    the start/end of the drawn line.

    It is NOT correct for a synthetic cyclone-detour waypoint computed by
    simple trig offset from a storm's center (see
    app.services.cyclone_reroute_service.find_alternate_route): that
    point can land anywhere, including on land or an island, and forcing
    it onto the route would inject a straight, unvalidated segment from
    that arbitrary point to the nearest real sea-lane node - exactly the
    bug that let alternate routes cut across land. Pass pin_origin=False
    / pin_dest=False for that side and the route instead starts/ends at
    searoute's own graph-snapped coordinate, which is guaranteed to sit
    on navigable water.
    """
    key_args = (_round(origin_lat), _round(origin_lon), _round(dest_lat), _round(dest_lon))
    try:
        raw_route, distance_km = _compute_raw_route_cached(*key_args)
    except Exception as exc:
        logger.warning(
            "searoute failed for (%s,%s) -> (%s,%s): %s",
            origin_lat, origin_lon, dest_lat, dest_lon, exc,
        )
        raise ValueError(f"Could not compute a sea route for this origin/destination: {exc}") from exc

    route = [list(pt) for pt in raw_route]
    if pin_origin:
        route = [[origin_lat, origin_lon]] + route
    if pin_dest:
        route = route + [[dest_lat, dest_lon]]

    if not route:
        route = [[origin_lat, origin_lon], [dest_lat, dest_lon]]

    return {"route": route, "distance_km": distance_km}
