"""
detection.py
------------
Real detection logic for the maritime monitoring modules. No randomness,
no canned outputs - every function takes real inputs (current/previous
positions, timestamps, weather readings) and computes a real result using
actual geometry and physics, the same way the design document specifies.
"""

import math


EARTH_RADIUS_KM = 6371.0
MAX_REALISTIC_SHIP_SPEED_KMH = 55.0  # generous ceiling for any commercial vessel


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two GPS points, in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial compass bearing from point 1 to point 2, in degrees (0-360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    x = math.sin(d_lambda) * math.cos(phi2)
    y = (math.cos(phi1) * math.sin(phi2)
         - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda))
    theta = math.atan2(x, y)
    return (math.degrees(theta) + 360) % 360


def point_to_segment_distance_km(p_lat, p_lon, a_lat, a_lon, b_lat, b_lon):
    """Approximate cross-track distance (km) from point P to the line
    segment A-B, using an equirectangular projection. This is accurate
    enough for route-corridor checking over distances of a few hundred km,
    which is the scale relevant to coastal shipping routes.
    """
    # Project lat/lon to a local flat plane (km) centered near the segment,
    # using the mean latitude to scale longitude correctly.
    mean_lat = math.radians((a_lat + b_lat) / 2.0)
    km_per_deg_lat = 111.32
    km_per_deg_lon = 111.32 * math.cos(mean_lat)

    ax, ay = a_lon * km_per_deg_lon, a_lat * km_per_deg_lat
    bx, by = b_lon * km_per_deg_lon, b_lat * km_per_deg_lat
    px, py = p_lon * km_per_deg_lon, p_lat * km_per_deg_lat

    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay

    seg_len_sq = abx * abx + aby * aby
    if seg_len_sq == 0:
        # Degenerate segment (A == B); fall back to point distance.
        return haversine_km(p_lat, p_lon, a_lat, a_lon)

    t = max(0.0, min(1.0, (apx * abx + apy * aby) / seg_len_sq))
    closest_x = ax + t * abx
    closest_y = ay + t * aby

    dx, dy = px - closest_x, py - closest_y
    return math.sqrt(dx * dx + dy * dy)


# ── Sea-lane chokepoints ──
# This is a Python port of the exact same waypoint table the frontend map
# uses (frontend/index.html: SEA_CHOKEPOINTS / seaRouteWaypoints) to bend a
# straight origin->destination line through real maritime corridors. Both
# sides must agree on the route shape: the frontend draws it and drives the
# simulated ship along it, and the backend needs to measure deviation from
# that *same* bent path - not the straight line the ship was never on -
# or every simulated voyage that legitimately passes through a strait or
# canal would be flagged as "off route" for the entire crossing.
SEA_CHOKEPOINTS = [
    {"name": "Suez", "gate": ((10, 20), (35, 45)), "via": (30.5, 32.3)},
    {"name": "Bab-el-Mandeb", "gate": ((8, 40), (16, 48)), "via": (12.6, 43.4)},
    {"name": "Hormuz", "gate": ((22, 50), (28, 60)), "via": (26.6, 56.3)},
    {"name": "Malacca", "gate": ((-2, 96), (8, 104)), "via": (2.8, 101.2)},
    {"name": "Cape of Good Hope", "gate": ((-40, 10), (-25, 30)), "via": (-34.8, 19.9)},
    {"name": "Panama", "gate": ((5, -83), (12, -77)), "via": (9.1, -79.7)},
    {"name": "Gibraltar", "gate": ((34, -8), (38, -3)), "via": (35.9, -5.6)},
]


def _crosses_region(lat1, lon1, lat2, lon2, gate):
    (g_lat1, g_lon1), (g_lat2, g_lon2) = gate
    min_lat, max_lat = min(lat1, lat2), max(lat1, lat2)
    min_lon, max_lon = min(lon1, lon2), max(lon1, lon2)
    return min_lat < g_lat2 and max_lat > g_lat1 and min_lon < g_lon2 and max_lon > g_lon1


def sea_route_waypoints(o_lat, o_lon, d_lat, d_lon):
    """Ordered [(lat, lon), ...] from origin to destination, inserting a
    chokepoint waypoint wherever the straight path would cross one of the
    strait/canal corridors above - the same lightweight stand-in for real
    land-avoiding marine routing used on the map."""
    hits = [c for c in SEA_CHOKEPOINTS if _crosses_region(o_lat, o_lon, d_lat, d_lon, c["gate"])]
    hits.sort(key=lambda c: haversine_km(o_lat, o_lon, c["via"][0], c["via"][1]))
    pts = [(o_lat, o_lon)] + [c["via"] for c in hits] + [(d_lat, d_lon)]
    return pts


def distance_from_sea_route_km(lat, lon, o_lat, o_lon, d_lat, d_lon):
    """Minimum distance (km) from a point to the actual multi-segment sea
    route between origin and destination (rather than the straight
    great-circle line, which a bent ocean route usually never touches)."""
    pts = sea_route_waypoints(o_lat, o_lon, d_lat, d_lon)
    return min(
        point_to_segment_distance_km(lat, lon, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
        for i in range(len(pts) - 1)
    )


def check_route_deviation(current_lat, current_lon,
                           origin_lat, origin_lon,
                           dest_lat, dest_lon,
                           corridor_km=10.0):
    """Check how far the ship is from its expected corridor - the real bent
    sea-lane route between its declared origin and destination (following
    the same chokepoints the map draws and the simulator sails through),
    not a naive straight line that legitimate ocean traffic would rarely
    ever be on. `is_deviation` is True if the ship is outside `corridor_km`
    of that route.
    """
    distance_from_corridor = distance_from_sea_route_km(
        current_lat, current_lon, origin_lat, origin_lon, dest_lat, dest_lon
    )
    return {
        "distance_from_corridor_km": round(distance_from_corridor, 2),
        "corridor_km": corridor_km,
        "is_deviation": distance_from_corridor > corridor_km,
    }


def check_gps_spoofing(prev_lat, prev_lon, curr_lat, curr_lon,
                        minutes_elapsed, max_speed_kmh=MAX_REALISTIC_SHIP_SPEED_KMH):
    """Cross-check the reported jump in position against the maximum
    distance physically coverable in the elapsed time. If the implied
    speed required to cover that distance exceeds what any vessel can
    realistically achieve, the reading is flagged as spoofed.
    """
    distance_km = haversine_km(prev_lat, prev_lon, curr_lat, curr_lon)
    hours_elapsed = max(minutes_elapsed, 0.0001) / 60.0
    implied_speed_kmh = distance_km / hours_elapsed
    max_possible_km = max_speed_kmh * hours_elapsed

    return {
        "distance_km": round(distance_km, 2),
        "minutes_elapsed": minutes_elapsed,
        "implied_speed_kmh": round(implied_speed_kmh, 1),
        "max_possible_distance_km": round(max_possible_km, 2),
        "max_realistic_speed_kmh": max_speed_kmh,
        "is_spoofed": distance_km > max_possible_km,
    }


def check_restricted_zone(lat, lon, zones):
    """zones: list of dicts with keys name, lat, lon, radius_km, zone_type.
    Returns the first zone the point falls inside, or None.
    """
    for zone in zones:
        dist = haversine_km(lat, lon, zone["lat"], zone["lon"])
        if dist <= zone["radius_km"]:
            return {
                "zone_name": zone["name"],
                "zone_type": zone["zone_type"],
                "distance_from_center_km": round(dist, 2),
                "radius_km": zone["radius_km"],
            }
    return None


def check_speed_anomaly(reported_speed_kmh, expected_speed_kmh, stop_threshold_ratio=0.15):
    """Flags an unexpected stop or severe slowdown relative to the
    voyage's expected cruising speed.
    """
    if expected_speed_kmh <= 0:
        return {"is_anomaly": False, "reason": None}

    ratio = reported_speed_kmh / expected_speed_kmh
    if reported_speed_kmh <= 0.01:
        return {
            "is_anomaly": True,
            "reason": "unexpected_stop",
            "ratio_of_expected": round(ratio, 3),
        }
    if ratio < stop_threshold_ratio:
        return {
            "is_anomaly": True,
            "reason": "severe_slowdown",
            "ratio_of_expected": round(ratio, 3),
        }
    return {"is_anomaly": False, "reason": None, "ratio_of_expected": round(ratio, 3)}


def cyclone_risk_score(wind_speed_kmh, pressure_hpa):
    """Risk scoring against published cyclone formation thresholds:
      - Tropical storm force wind begins at 63 km/h (34 kt)
      - Severe cyclonic storm intensifies further past 89 km/h (48 kt)
      - Standard atmospheric pressure is ~1013 hPa; drops below 990
        indicate strong low-pressure systems, below 970 indicate intense ones.
    These are standard meteorological thresholds (IMD / WMO classifications),
    not arbitrary numbers.
    """
    score = 0
    if wind_speed_kmh >= 63:
        score += 40
    if wind_speed_kmh >= 89:
        score += 20
    if wind_speed_kmh >= 118:
        score += 10

    if pressure_hpa <= 990:
        score += 20
    if pressure_hpa <= 970:
        score += 10

    score = min(100, score)

    if score >= 80:
        level = "EXTREME"
    elif score >= 60:
        level = "HIGH"
    elif score >= 40:
        level = "MODERATE"
    else:
        level = "LOW"

    return {"score": score, "level": level}
