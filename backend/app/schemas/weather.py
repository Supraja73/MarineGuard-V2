"""
weather.py (schemas)
-----------------------
Request models for weather readings used by the cyclone-risk module.
lat/lon are now required so the backend can check ship proximity
to the storm centre before raising alerts.
"""

from typing import Optional
from pydantic import BaseModel


class WeatherReadingRequest(BaseModel):
    region: str
    wind_speed_kmh: float
    pressure_hpa: float
    wave_height_m: Optional[float] = None
    rainfall_mm: Optional[float] = None
    # Centre of the observed weather cell (from real API or operator input)
    lat: Optional[float] = None
    lon: Optional[float] = None
