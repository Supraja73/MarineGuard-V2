"""
alerts.py (schemas)
----------------------
Request models for alert acknowledgement and manual broadcast.
"""

from typing import Optional
from pydantic import BaseModel


class AckAlertRequest(BaseModel):
    alert_id: int


class BroadcastAlertRequest(BaseModel):
    ship_id: Optional[str] = None
    title: str
    detail: str
    severity: str = "warning"
    lat: Optional[float] = None
    lon: Optional[float] = None
