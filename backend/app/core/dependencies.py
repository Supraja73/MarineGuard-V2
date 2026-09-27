"""
dependencies.py
-----------------
Shared FastAPI dependencies used across routers:

- get_db(): FastAPI dependency that yields an AsyncSession bound to the
  Postgres database (see app.db.session), one per request, instead of the
  single long-lived SQLite connection the previous version shared across
  every request.
- ConnectionRegistry: tracks open websocket clients for the live event feed
  and exposes broadcast_event() so any router can push live updates without
  importing the websocket router directly (which would create a circular
  import between routers). Unchanged by the database migration.
"""

from typing import List, AsyncGenerator
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: a fresh AsyncSession per request, closed (and
    rolled back on error) when the request finishes. Routers declare
    `session: AsyncSession = Depends(get_db)` to use it.
    """
    async for session in get_session():
        yield session


async def get_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
):
    """Resolves the real logged-in user from a DB-backed session token
    (see app.services.user_auth_service / app.routers.users) sent as
    `Authorization: Bearer <token>`. Raises 401 if missing, unknown, or
    expired - this replaces the old header-only "trust whatever role the
    client claims" check with an actual verified identity.
    """
    from app.db.models import User, UserSession  # local import: avoids a circular import at module load time
    from app.services.user_auth_service import is_expired

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated - missing or malformed Authorization header")
    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(401, "Not authenticated")

    result = await session.execute(select(UserSession).where(UserSession.token == token))
    user_session = result.scalar_one_or_none()
    if not user_session:
        raise HTTPException(401, "Session not found or already logged out")
    if is_expired(user_session.expires_at):
        await session.delete(user_session)
        await session.commit()
        raise HTTPException(401, "Session expired - please log in again")

    user = await session.get(User, user_session.user_id)
    if not user:
        raise HTTPException(401, "User account no longer exists")

    # Defense in depth: same reasoning as the captain-approval check below
    # - if a System Administrator disables an account *after* it already
    # holds a valid session, every subsequent request must also refuse it.
    if not user.active:
        raise HTTPException(403, "This account has been disabled by the System Administrator.")

    # Defense in depth: login already refuses non-approved captains a
    # token, but if a captain is rejected (or unapproved) *after* already
    # holding a valid session, every subsequent request must also refuse
    # them - not just the login endpoint.
    if user.role == "ship_captain" and user.status != "approved":
        raise HTTPException(403, "Your Ship Captain account is not currently approved for access.")
    return user


async def require_control_station(user=Depends(get_current_user)):
    """Server-side guard for Control-Station-only endpoints (triggering /
    clearing cyclones, approving / rejecting / forcing reroutes, manual
    replan, fleet-wide monitoring views). A Ship Captain's token, even
    though it's a real authenticated session, is refused here with a 403
    - authentication alone isn't authorization.
    """
    if user.role != "control_station":
        raise HTTPException(403, "Only the Control Station may perform this action")
    return user


async def require_ship_captain(user=Depends(get_current_user)):
    """Guard for endpoints that only make sense for a Ship Captain acting
    on their own ship (e.g. viewing their own voyage). Control Station
    users don't hit these routes - they use the fleet-wide equivalents.
    """
    if user.role != "ship_captain":
        raise HTTPException(403, "This endpoint is for Ship Captain accounts only")
    return user


async def get_current_user_optional(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
):
    """Same lookup as get_current_user, but returns None instead of
    raising 401 when there's no token. Passenger is a deliberately
    account-less, read-only role (see frontend login gate) - endpoints
    that Passengers must still be able to browse (ship list, active
    voyages) use this instead of get_current_user, and treat `None` as
    "unauthenticated public/passenger view" while still fully enforcing
    Ship Captain / Control Station scoping when a real token is present.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        return await get_current_user(authorization=authorization, session=session)
    except HTTPException:
        return None


class ConnectionRegistry:
    """Holds active websocket connections and broadcasts JSON events to all
    of them. A single instance of this is created in app.main and shared
    via app.state so every router can reach it without circular imports.
    """

    def __init__(self):
        self._sockets: List[WebSocket] = []

    def add(self, websocket: WebSocket) -> None:
        self._sockets.append(websocket)

    def remove(self, websocket: WebSocket) -> None:
        if websocket in self._sockets:
            self._sockets.remove(websocket)

    async def broadcast(self, event: dict) -> None:
        dead = []
        for ws in self._sockets:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove(ws)
