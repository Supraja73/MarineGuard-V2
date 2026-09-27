"""
users.py (router)
--------------------
Real human authentication for the web UI - separate from the ship ECC
handshake in app.routers.auth. Issues DB-backed session tokens (see
app.services.user_auth_service) that app.core.dependencies verifies on
every protected request. This is what makes "only Control Station can
approve a reroute" (etc.) an actual server-side guarantee instead of a
client-side role label.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_current_user, require_control_station
from app.db.models import User, UserSession, Ship
from app.schemas.users import (
    RegisterRequest, LoginRequest, CreateControlStationRequest,
    CaptainApproveRequest, AssignShipRequest, ResetPasswordRequest,
)
from app.services import user_auth_service as auth_svc

router = APIRouter(prefix="/api/users", tags=["users"])


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "role_label": "Control Station" if u.role == "control_station" else "Ship Captain",
        "ship_id": u.ship_id,
        "status": u.status,
        "active": u.active,
        "reviewed_by": u.reviewed_by,
        "reviewed_at": u.reviewed_at.isoformat() if u.reviewed_at else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("/bootstrap_status")
async def bootstrap_status(session: AsyncSession = Depends(get_db)):
    """Public, unauthenticated. Lets the frontend know whether any Control
    Station account exists yet, so it can offer a one-time first-run
    'Create System Administrator Account' form instead of hiding
    registration entirely (see create_control_station's bootstrap case
    above). Reveals only a boolean - no account data.
    """
    count_result = await session.execute(select(func.count()).select_from(User).where(User.role == "control_station"))
    return {"needs_bootstrap": count_result.scalar_one() == 0}


@router.post("/register")
async def register(payload: RegisterRequest, session: AsyncSession = Depends(get_db)):
    """Public self-registration - Ship Captains only. Control Center
    accounts have no public registration path (see require_control_station
    guard on /admin/create_control_station below); the System
    Administrator creates those directly.

    A newly-registered captain is NOT active: the account is created with
    status="pending", has no ship assigned yet, and cannot log in until a
    Control Station operator reviews it via the Captain Management page
    (approve + assign a ship, or reject).
    """
    if payload.role != "ship_captain":
        raise HTTPException(
            403,
            "Control Center accounts cannot be created through public registration. "
            "Contact your System Administrator.",
        )

    weak = auth_svc.validate_password_strength(payload.password)
    if weak:
        raise HTTPException(400, weak)

    existing = await session.execute(select(User).where(User.username == payload.username))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "That username is already taken")

    user = User(
        username=payload.username,
        password_hash=auth_svc.hash_password(payload.password),
        role="ship_captain",
        ship_id=None,  # only ever set by a Control Station operator on approval
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return _user_to_dict(user)


@router.post("/admin/create_control_station")
async def create_control_station(
    payload: CreateControlStationRequest,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
):
    """Creates a new Control Center account.

    Bootstrap case: if NO Control Station account exists in the database
    yet (a brand-new deployment - see app.main's note that no seed/demo
    data is created anywhere), this endpoint allows creating exactly that
    first one without authentication, since there is by definition no
    existing operator who could log in and create it. The instant one
    exists, this door closes: every account after the first requires an
    authenticated, already-approved Control Station operator (checked
    manually below rather than via the require_control_station dependency,
    so the same endpoint can serve both the bootstrap and normal cases).
    """
    existing_admin_count = await session.execute(select(func.count()).select_from(User).where(User.role == "control_station"))
    is_bootstrap = existing_admin_count.scalar_one() == 0

    admin_username = "system-bootstrap"
    if not is_bootstrap:
        # Not the bootstrap case anymore - a real, currently logged-in,
        # active Control Station operator is required, same as every
        # other admin endpoint in this file.
        current_user = await get_current_user(authorization=authorization, session=session)
        if current_user.role != "control_station":
            raise HTTPException(403, "Only Control Station may create another Control Center account")
        admin_username = current_user.username

    weak = auth_svc.validate_password_strength(payload.password)
    if weak:
        raise HTTPException(400, weak)

    existing = await session.execute(select(User).where(User.username == payload.username))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "That username is already taken")

    user = User(
        username=payload.username,
        password_hash=auth_svc.hash_password(payload.password),
        role="control_station",
        ship_id=None,
        status="approved",
        reviewed_by=admin_username,
        reviewed_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return _user_to_dict(user)


@router.post("/login")
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(User).where(User.username == payload.username))
    user = result.scalar_one_or_none()

    # Deliberately identical error for "no such user" and "wrong password"
    # so login can't be used to enumerate valid usernames.
    if not user or not auth_svc.verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")

    # System Administrator account management: a disabled account (see
    # /admin/control_stations/{id}/disable) can never log in, regardless
    # of role or approval status.
    if not user.active:
        raise HTTPException(403, "This account has been disabled by the System Administrator.")

    # Ship Captain approval gate. Only an "approved" captain (one who has
    # been reviewed and assigned a ship by Control Center) may log in.
    # Control Station accounts are always "approved" (see registration/
    # admin-creation above) so this never affects them.
    if user.role == "ship_captain":
        if user.status == "pending":
            raise HTTPException(403, "Your account is awaiting Control Center approval.")
        if user.status == "rejected":
            raise HTTPException(403, "Your registration was rejected. Please contact the administrator.")

    token = auth_svc.new_session_token()
    session.add(UserSession(token=token, user_id=user.id, created_at=datetime.now(timezone.utc), expires_at=auth_svc.session_expiry()))
    await session.commit()

    return {"token": token, "user": _user_to_dict(user)}


@router.post("/logout")
async def logout(authorization: str | None = Header(default=None), session: AsyncSession = Depends(get_db)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
        existing = await session.get(UserSession, token)
        if existing:
            await session.delete(existing)
            await session.commit()
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return _user_to_dict(user)


# ═══════════════════════════════════════════════════════════════════════════
# CAPTAIN MANAGEMENT - Control Station only. Reuses the existing User /
# UserSession models and RBAC guards; no new tables, no new auth system.
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/captains")
async def list_captains(
    status: str | None = None,  # pending | approved | rejected | omit for all
    _admin: User = Depends(require_control_station),
    session: AsyncSession = Depends(get_db),
):
    query = select(User).where(User.role == "ship_captain")
    if status:
        if status not in ("pending", "approved", "rejected"):
            raise HTTPException(400, "status must be 'pending', 'approved', or 'rejected'")
        query = query.where(User.status == status)
    query = query.order_by(User.created_at.desc())
    result = await session.execute(query)
    captains = result.scalars().all()
    return [_user_to_dict(c) for c in captains]


async def _get_pending_or_any_captain(user_id: int, session: AsyncSession) -> User:
    captain = await session.get(User, user_id)
    if not captain or captain.role != "ship_captain":
        raise HTTPException(404, "Ship Captain account not found")
    return captain


@router.post("/captains/{user_id}/approve")
async def approve_captain(
    user_id: int,
    payload: CaptainApproveRequest,
    admin: User = Depends(require_control_station),
    session: AsyncSession = Depends(get_db),
):
    """Approves a pending Ship Captain and assigns them to exactly one
    ship in the same step - an approved captain with no ship makes no
    sense in this RBAC model, so both happen together.
    """
    captain = await _get_pending_or_any_captain(user_id, session)

    ship = await session.get(Ship, payload.ship_id)
    if not ship:
        raise HTTPException(404, f"Ship '{payload.ship_id}' not found")

    captain.status = "approved"
    captain.ship_id = ship.ship_id
    captain.reviewed_by = admin.username
    captain.reviewed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(captain)
    return _user_to_dict(captain)


@router.post("/captains/{user_id}/reject")
async def reject_captain(
    user_id: int,
    admin: User = Depends(require_control_station),
    session: AsyncSession = Depends(get_db),
):
    captain = await _get_pending_or_any_captain(user_id, session)
    captain.status = "rejected"
    captain.ship_id = None
    captain.reviewed_by = admin.username
    captain.reviewed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(captain)
    return _user_to_dict(captain)


@router.post("/captains/{user_id}/assign_ship")
async def assign_ship(
    user_id: int,
    payload: AssignShipRequest,
    admin: User = Depends(require_control_station),
    session: AsyncSession = Depends(get_db),
):
    """Re-assigns (or changes) the ship for an already-approved captain.
    Approving a pending captain already assigns a ship in one step (see
    /approve above); this endpoint is for adjusting an existing
    assignment afterwards.
    """
    captain = await _get_pending_or_any_captain(user_id, session)
    ship = await session.get(Ship, payload.ship_id)
    if not ship:
        raise HTTPException(404, f"Ship '{payload.ship_id}' not found")

    captain.ship_id = ship.ship_id
    if captain.status != "approved":
        captain.status = "approved"
    captain.reviewed_by = admin.username
    captain.reviewed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(captain)
    return _user_to_dict(captain)


@router.post("/captains/{user_id}/remove_assignment")
async def remove_assignment(
    user_id: int,
    _admin: User = Depends(require_control_station),
    session: AsyncSession = Depends(get_db),
):
    """Clears a captain's ship assignment without changing their approval
    status. Since the RBAC model scopes a captain strictly to their
    assigned ship, an approved captain with no ship_id can no longer
    reach any ship-scoped data until re-assigned (existing ships.py /
    voyages.py / alerts.py / reroutes.py checks already enforce this).
    """
    captain = await _get_pending_or_any_captain(user_id, session)
    captain.ship_id = None
    await session.commit()
    await session.refresh(captain)
    return _user_to_dict(captain)


# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM ADMINISTRATOR - manage Control Center accounts. There is no
# separate System Administrator login/role: per spec, any existing
# Control Station operator acts in that capacity (they're the only ones
# who can create a Control Center account in the first place - see
# /admin/create_control_station above). These endpoints round out that
# same responsibility: disable/enable an account, or reset its password,
# without deleting it or touching any other functionality.
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/control_stations")
async def list_control_stations(
    _admin: User = Depends(require_control_station),
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(
        select(User).where(User.role == "control_station").order_by(User.created_at.asc())
    )
    return [_user_to_dict(u) for u in result.scalars().all()]


async def _get_control_station_or_404(user_id: int, session: AsyncSession) -> User:
    account = await session.get(User, user_id)
    if not account or account.role != "control_station":
        raise HTTPException(404, "Control Center account not found")
    return account


@router.post("/control_stations/{user_id}/disable")
async def disable_control_station(
    user_id: int,
    admin: User = Depends(require_control_station),
    session: AsyncSession = Depends(get_db),
):
    if user_id == admin.id:
        raise HTTPException(400, "You cannot disable your own account while logged in as it.")
    account = await _get_control_station_or_404(user_id, session)
    account.active = False
    await session.commit()
    await session.refresh(account)
    return _user_to_dict(account)


@router.post("/control_stations/{user_id}/enable")
async def enable_control_station(
    user_id: int,
    _admin: User = Depends(require_control_station),
    session: AsyncSession = Depends(get_db),
):
    account = await _get_control_station_or_404(user_id, session)
    account.active = True
    await session.commit()
    await session.refresh(account)
    return _user_to_dict(account)


@router.post("/control_stations/{user_id}/reset_password")
async def reset_control_station_password(
    user_id: int,
    payload: ResetPasswordRequest,
    _admin: User = Depends(require_control_station),
    session: AsyncSession = Depends(get_db),
):
    account = await _get_control_station_or_404(user_id, session)
    weak = auth_svc.validate_password_strength(payload.new_password)
    if weak:
        raise HTTPException(400, weak)
    account.password_hash = auth_svc.hash_password(payload.new_password)
    # Invalidate existing sessions for this account so a reset password
    # can't be bypassed by an already-open session elsewhere.
    await session.execute(delete(UserSession).where(UserSession.user_id == account.id))
    await session.commit()
    return {"reset": True, "username": account.username}
