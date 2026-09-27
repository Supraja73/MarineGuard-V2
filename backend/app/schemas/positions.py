"""
positions.py (schemas)
------------------------
Request models for ship position reports. The signature must cover the
server's own canonical reconstruction of (ship_id, lat, lon, speed,
timestamp, nonce) - see app.services.position_service.build_position_message.
"""

from pydantic import BaseModel


class PositionReportRequest(BaseModel):
    ship_id: str
    lat: float
    lon: float
    speed: float
    timestamp: str  # ISO 8601 timestamp the ship attached when signing
    nonce: str       # random per-message token the ship attached when signing, blocks replay
    signature: str   # hex ECDSA signature over the server's canonical reconstruction of the above fields
