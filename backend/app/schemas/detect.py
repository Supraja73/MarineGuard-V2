"""
detect.py (schemas)
----------------------
Request models for the anomaly-detection check endpoints (GPS spoofing,
route deviation, restricted zones, speed anomaly).
"""

from typing import Optional
from pydantic import BaseModel


class SpoofCheckRequest(BaseModel):
    ship_id: str
    reported_lat: float
    reported_lon: float
    minutes_elapsed: float


class RouteCheckRequest(BaseModel):
    ship_id: str
    lat: float
    lon: float


class SpeedCheckRequest(BaseModel):
    ship_id: str
    reported_speed: float
    expected_speed: float
    location_label: Optional[str] = None


class SARClassifyRequest(BaseModel):
    sample_id: Optional[str] = None
    band_1: Optional[list[float]] = None
    band_2: Optional[list[float]] = None
    ship_id: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None

