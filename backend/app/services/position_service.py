"""
position_service.py
----------------------
Logic shared by the positions router and the detection router: building
the canonical string that ECDSA signatures must cover for a position
report, and computing heading from consecutive points.
"""

import json

from app.detection import detection


def build_position_message(ship_id: str, lat: float, lon: float, speed: float,
                             timestamp: str, nonce: str) -> str:
    """The exact canonical string that must be signed for a position report.
    Built identically on the server (for verification) and expected to be
    built identically on the client before signing - this is what actually
    binds the ECDSA signature to these specific field values, so altering
    any one of them after signing invalidates the signature. Field order
    and formatting must stay byte-for-byte stable, which is why this is a
    single shared function rather than ad hoc string building in two places.
    """
    obj = {
        "ship_id": ship_id,
        "lat": round(float(lat), 6),
        "lon": round(float(lon), 6),
        "speed": round(float(speed), 2),
        "timestamp": timestamp,
        "nonce": nonce,
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def compute_heading(last_lat, last_lon, new_lat, new_lon):
    if last_lat is None or last_lon is None:
        return None
    return detection.bearing_deg(last_lat, last_lon, new_lat, new_lon)


def minutes_between(iso_a: str, iso_b: str) -> float:
    from datetime import datetime
    try:
        ta = datetime.fromisoformat(iso_a)
        tb = datetime.fromisoformat(iso_b)
        return abs((tb - ta).total_seconds()) / 60.0
    except Exception:
        return 9999.0
