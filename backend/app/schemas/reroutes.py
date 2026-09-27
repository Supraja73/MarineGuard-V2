from pydantic import BaseModel


class ProposeRerouteRequest(BaseModel):
    voyage_id: int
