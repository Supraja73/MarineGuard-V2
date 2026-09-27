from pydantic import BaseModel, field_validator


class RegisterRequest(BaseModel):
    """Public self-registration. Only Ship Captains may use this endpoint -
    Control Center (control_station) accounts have no public registration
    path; they're created by an existing System Administrator via
    CreateControlStationRequest below. ship_id is intentionally not
    accepted here: a captain's ship assignment is only ever made by a
    Control Center operator during approval, never chosen by the
    registrant.
    """
    username: str
    password: str
    role: str  # must be "ship_captain"

    @field_validator("username")
    @classmethod
    def username_not_blank(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateControlStationRequest(BaseModel):
    """Used by an existing Control Station operator (acting as the System
    Administrator) to create another Control Center account. There is no
    public/self-service equivalent of this endpoint.
    """
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_not_blank(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        return v


class CaptainApproveRequest(BaseModel):
    ship_id: str  # the single ship this captain is being scoped to


class AssignShipRequest(BaseModel):
    ship_id: str


class ResetPasswordRequest(BaseModel):
    new_password: str
