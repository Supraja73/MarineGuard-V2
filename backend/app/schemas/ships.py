"""
ships.py (schemas)
-------------------
Request/response models for ship registration and registry lookups.
"""

from pydantic import BaseModel, Field


class ShipRegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    imo_number: str = Field(min_length=3, max_length=20)
    ship_type: str
    origin_port: str
    origin_lat: float
    origin_lon: float
    dest_port: str
    dest_lat: float
    dest_lon: float
    password: str = Field(min_length=8, max_length=128)
    mmsi: str | None = None
    captain: str | None = None
    flag: str | None = None
    length_m: float | None = None
    beam_m: float | None = None
    initial_speed: float | None = None
    course: float | None = None
    heading: float | None = None
    eta: str | None = None
    image_url: str | None = None
    capacity_tons: float | None = None
