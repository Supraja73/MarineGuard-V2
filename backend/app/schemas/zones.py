"""
zones.py (schemas)
--------------------
Request models for restricted/geo-fenced zone management and checks.
"""

from pydantic import BaseModel


class ZoneCreateRequest(BaseModel):
    name: str
    zone_type: str
    lat: float
    lon: float
    radius_km: float


class ZoneCheckRequest(BaseModel):
    lat: float
    lon: float
