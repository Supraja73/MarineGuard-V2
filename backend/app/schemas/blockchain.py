"""
blockchain.py (schemas)
--------------------------
Request models for blockchain ledger endpoints.
"""

from typing import Optional
from pydantic import BaseModel


class TamperRequest(BaseModel):
    block_index: int
    new_lat: Optional[float] = None
