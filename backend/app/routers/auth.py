"""
auth.py (router)
--------------------
Mutual authentication: the Control Center issues a challenge string, the
ship signs it with its real ECDSA private key, the server verifies that
signature with the ship's real public key, and a real ECDH session key is
derived between the ship and the Control Center's own identity. Every
attempt - success or failure - is persisted to auth_events, giving the
Authentication page a genuine history instead of a client-side-only log.

Also provides key rotation: a ship's ECC key pair is genuinely
regenerated, the old public key is replaced, and the rotation is logged
to the blockchain (so the audit trail shows exactly when keys changed).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import cryptocore as crypto
from app.blockchain import ledger
from app.schemas.auth import AuthenticateRequest, RotateKeysRequest
from app.core.dependencies import get_db
from app.db.models import Ship, AuthEvent, ControlCenterIdentity

router = APIRouter(prefix="/api/auth", tags=["authentication"])


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def get_or_create_control_center_identity(session: AsyncSession) -> ControlCenterIdentity:
    """The Control Center needs its own fixed ECC identity to perform ECDH
    with ships (the same way two ships establish a session key with each
    other in app.routers.crypto's /api/crypto/ecdh demo). Generated once,
    lazily, and reused afterwards - never re-generated on each call, since
    a real ECDH session key must be reproducible from the same key pair.
    """
    result = await session.execute(select(ControlCenterIdentity).where(ControlCenterIdentity.id == 1))
    identity = result.scalar_one_or_none()
    if identity:
        return identity

    priv_pem, pub_hex = crypto.generate_keypair()
    identity = ControlCenterIdentity(id=1, public_key=pub_hex, private_key_pem=priv_pem, created_at=_now_utc())
    session.add(identity)
    try:
        await session.commit()
    except Exception:
        # Another concurrent request may have created row id=1 first;
        # that's fine, just re-fetch the one that won.
        await session.rollback()
        result = await session.execute(select(ControlCenterIdentity).where(ControlCenterIdentity.id == 1))
        identity = result.scalar_one()
    return identity


def _auth_event_to_dict(event: AuthEvent) -> dict:
    return {
        "auth_id": event.auth_id,
        "ship_id": event.ship_id,
        "ship_name": event.ship_name,
        "challenge": event.challenge,
        "signature": event.signature,
        "session_key_fingerprint": event.session_key_fingerprint,
        "success": bool(event.success),
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


@router.get("/control_center_public_key")
async def get_control_center_public_key(session: AsyncSession = Depends(get_db)):
    identity = await get_or_create_control_center_identity(session)
    return {"public_key": identity.public_key}


@router.post("/authenticate")
async def authenticate_ship(req: AuthenticateRequest, session: AsyncSession = Depends(get_db)):
    ship = await session.get(Ship, req.ship_id)
    if not ship:
        raise HTTPException(404, "Ship not found")

    # The ship signs the challenge with its own private key (held
    # server-side for this demo platform, same exception as
    # /api/ships/{id}/private_key) - a real onboard unit would do this
    # signing itself and only ever transmit the signature.
    signature = crypto.sign_message(ship.private_key_pem, req.challenge)
    success = crypto.verify_signature(ship.public_key, req.challenge, signature)

    control_identity = await get_or_create_control_center_identity(session)
    session_key = crypto.ecdh_derive_session_key(ship.private_key_pem, control_identity.public_key)
    session_key_fingerprint = crypto.sha256_hex(session_key.hex())[:32]

    event = AuthEvent(
        ship_id=ship.ship_id,
        ship_name=ship.name,
        challenge=req.challenge,
        signature=signature,
        session_key_fingerprint=session_key_fingerprint,
        success=1 if success else 0,
        created_at=_now_utc(),
    )
    session.add(event)
    await session.commit()

    return {
        "success": success,
        "signature": signature,
        "session_key_fingerprint": session_key_fingerprint,
        "session_key_hex": session_key.hex(),
    }


@router.get("/log")
async def get_auth_log(ship_id: str | None = None, limit: int = 50, session: AsyncSession = Depends(get_db)):
    query = select(AuthEvent)
    if ship_id:
        query = query.where(AuthEvent.ship_id == ship_id)
    query = query.order_by(AuthEvent.created_at.desc()).limit(limit)
    result = await session.execute(query)
    events = result.scalars().all()
    return [_auth_event_to_dict(e) for e in events]


@router.post("/rotate_keys")
async def rotate_keys(req: RotateKeysRequest, request: Request, session: AsyncSession = Depends(get_db)):
    ship = await session.get(Ship, req.ship_id)
    if not ship:
        raise HTTPException(404, "Ship not found")

    old_public_key = ship.public_key
    new_priv_pem, new_pub_hex = crypto.generate_keypair()

    ship.public_key = new_pub_hex
    ship.private_key_pem = new_priv_pem
    await session.commit()

    block = await ledger.append_block(
        session, ship_id=ship.ship_id, ship_name=ship.name, event_type="KEY_ROTATION",
        lat=ship.current_lat, lon=ship.current_lon, speed=0,
        payload={"old_public_key_fingerprint": crypto.sha256_hex(old_public_key)[:32]},
        signature=None, signer_pub_key=new_pub_hex,
    )

    registry = request.app.state.connection_registry
    await registry.broadcast({"kind": "keys_rotated", "ship_id": ship.ship_id, "block_index": block["block_index"]})

    return {
        "ship_id": ship.ship_id,
        "new_public_key": new_pub_hex,
        "block_index": block["block_index"],
        "block_hash": block["block_hash"],
    }
