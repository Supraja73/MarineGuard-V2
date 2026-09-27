"""
voyages.py (schemas)
-----------------------
Request models for creating, replanning, and completing voyages.

Speed validation: the maritime modification spec calls for a hard
0-70 km/h range on the voyage's declared current speed, rejected with a
validation error outside that range - enforced here with a Pydantic
field constraint so it's impossible to get a Voyage row into the
database with an out-of-range speed via this API, no matter what the
client sends.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class VoyageCreateRequest(BaseModel):
    ship_id: str
    origin_port: str
    dest_port: str
    current_speed_kmh: float = Field(ge=0, le=70)
    geofence_radius_km: float = Field(gt=0, le=50)
    expected_departure: Optional[datetime] = None
    remarks: Optional[str] = Field(default=None, max_length=500)


class VoyageReplanRequest(BaseModel):
    origin_port: Optional[str] = None
    dest_port: Optional[str] = None
    dest_lat: Optional[float] = None
    dest_lon: Optional[float] = None
    current_speed_kmh: Optional[float] = Field(default=None, ge=0, le=70)
    geofence_radius_km: Optional[float] = Field(default=None, gt=0, le=50)
    remarks: Optional[str] = Field(default=None, max_length=500)
