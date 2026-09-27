"""
comms.py (router)
---------------------
Control Center <-> ship messaging, genuinely encrypted: a session key is
derived via real ECDH between the Control Center's identity and the
target ship's registered key pair, then the message is encrypted with
real AES-256-CBC using that derived key (app.crypto.cryptocore), exactly
like the existing /api/crypto/aes and /api/crypto/ecdh sandbox endpoints,
but wired together end-to-end and persisted as a genuine message log.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import cryptocore as crypto
from app.blockchain import ledger
from app.schemas.comms import SendMessageRequest
from app.core.dependencies import get_db
from app.db.models import Ship, Message
from app.routers.auth import get_or_create_control_center_identity

router = APIRouter(prefix="/api/comms", tags=["comms"])


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _message_to_dict(message: Message) -> dict:
    return {
        "message_id": message.message_id,
        "ship_id": message.ship_id,
        "ship_name": message.ship_name,
        "direction": message.direction,
        "message_type": message.message_type,
        "ciphertext_hex": message.ciphertext_hex,
        "iv_hex": message.iv_hex,
        "plaintext": message.plaintext_preview,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


@router.post("/send")
async def send_message(req: SendMessageRequest, request: Request, session: AsyncSession = Depends(get_db)):
    ship = await session.get(Ship, req.ship_id)
    if not ship:
        raise HTTPException(404, "Ship not found")

    control_identity = await get_or_create_control_center_identity(session)
    session_key = crypto.ecdh_derive_session_key(control_identity.private_key_pem, ship.public_key)

    encrypted = crypto.aes_encrypt(session_key, req.message)

    message = Message(
        ship_id=ship.ship_id,
        ship_name=ship.name,
        direction="to_ship",
        message_type=req.message_type,
        ciphertext_hex=encrypted["ciphertext"],
        iv_hex=encrypted["iv"],
        plaintext_preview=req.message,
        created_at=_now_utc(),
    )
    session.add(message)
    await session.commit()

    block = await ledger.append_block(
        session, ship_id=ship.ship_id, ship_name=ship.name, event_type="COMM",
        lat=None, lon=None, speed=None,
        payload={"direction": "to_ship", "message_type": req.message_type},
        signature=None, signer_pub_key=control_identity.public_key,
    )

    registry = request.app.state.connection_registry
    await registry.broadcast({
        "kind": "new_message", "message": _message_to_dict(message), "block_index": block["block_index"],
    })

    return _message_to_dict(message)


@router.get("")
async def list_messages(ship_id: str | None = None, limit: int = 50, session: AsyncSession = Depends(get_db)):
    query = select(Message)
    if ship_id:
        query = query.where(Message.ship_id == ship_id)
    query = query.order_by(Message.created_at.asc()).limit(limit)
    result = await session.execute(query)
    messages = result.scalars().all()
    return [_message_to_dict(m) for m in messages]
