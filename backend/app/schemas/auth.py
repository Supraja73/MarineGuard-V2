"""
auth.py (schemas)
--------------------
Request models for the mutual-authentication (ECDSA challenge/response +
ECDH session key) and key-rotation endpoints.
"""

from pydantic import BaseModel


class AuthenticateRequest(BaseModel):
    ship_id: str
    challenge: str


class RotateKeysRequest(BaseModel):
    ship_id: str
