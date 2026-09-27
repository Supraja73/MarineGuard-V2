"""
comms.py (schemas)
---------------------
Request models for the encrypted Control Center <-> ship messaging
endpoints.
"""

from pydantic import BaseModel


class SendMessageRequest(BaseModel):
    ship_id: str
    message: str
    message_type: str = "COMMAND"
