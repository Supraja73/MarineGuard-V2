"""
ships.py (router)
--------------------
Ship registration (issues ECC identity), registry lookups, and the
position-history endpoint that powers the live movement playback /
expected-vs-actual route visualization on the frontend.
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Depends
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import cryptocore as crypto
from app.blockchain import ledger
from app.schemas.ships import ShipRegisterRequest
from app.core.dependencies import get_db, get_current_user, get_current_user_optional, require_control_station
from app.core.logging_config import get_logger
from app.db.models import Ship, Position, Voyage, Alert, Message, AuthEvent, Block, UsedNonce

router = APIRouter(prefix="/api/ships", tags=["ships"])
logger = get_logger("app.routers.ships")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ship_to_public_dict(ship: Ship) -> dict:
    return {
        "ship_id": ship.ship_id,
        "name": ship.name,
        "imo_number": ship.imo_number,
        "ship_type": ship.ship_type,
        "origin_port": ship.origin_port,
        "origin_lat": ship.origin_lat,
        "origin_lon": ship.origin_lon,
        "dest_port": ship.dest_port,
        "dest_lat": ship.dest_lat,
        "dest_lon": ship.dest_lon,
        "public_key": ship.public_key,
        "certificate_hash": ship.certificate_hash,
        "current_lat": ship.current_lat,
        "current_lon": ship.current_lon,
        "current_speed": ship.current_speed,
        "status": ship.status,
        "created_at": ship.created_at.isoformat() if ship.created_at else None,
        "mmsi": ship.mmsi,
        "captain": ship.captain,
        "flag": ship.flag,
        "length_m": ship.length_m,
        "beam_m": ship.beam_m,
        "course": ship.course,
        "heading": ship.heading,
        "image_url": ship.image_url,
        "capacity_tons": ship.capacity_tons,
    }


async def _get_ship_or_404(session: AsyncSession, ship_id: str) -> Ship:
    ship = await session.get(Ship, ship_id)
    if not ship:
        raise HTTPException(404, "Ship not found")
    return ship


@router.post("/register")
async def register_ship(req: ShipRegisterRequest, request: Request, session: AsyncSession = Depends(get_db), _user=Depends(require_control_station)):
    registry = request.app.state.connection_registry

    ship_id = "MGS-" + crypto.random_token(4).upper()

    priv_pem, pub_hex = crypto.generate_keypair()
    password_hash = crypto.hash_password(req.password)

    cert_payload = json.dumps({
        "ship_id": ship_id, "name": req.name, "imo": req.imo_number,
        "public_key": pub_hex, "issued_at": _now_iso(),
    }, sort_keys=True)
    certificate_hash = crypto.sha256_hex(cert_payload)

    ship = Ship(
        ship_id=ship_id,
        name=req.name,
        imo_number=req.imo_number,
        ship_type=req.ship_type,
        origin_port=req.origin_port,
        origin_lat=req.origin_lat,
        origin_lon=req.origin_lon,
        dest_port=req.dest_port,
        dest_lat=req.dest_lat,
        dest_lon=req.dest_lon,
        public_key=pub_hex,
        private_key_pem=priv_pem,
        password_hash=password_hash,
        certificate_hash=certificate_hash,
        current_lat=req.origin_lat,
        current_lon=req.origin_lon,
        current_speed=req.initial_speed or 0,
        status="registered",
        created_at=_now_utc(),
        mmsi=req.mmsi,
        captain=req.captain,
        flag=req.flag,
        length_m=req.length_m,
        beam_m=req.beam_m,
        course=req.course,
        heading=req.heading if req.heading is not None else req.course,
        image_url=req.image_url,
        capacity_tons=req.capacity_tons,
    )
    session.add(ship)
    await session.commit()

    block = await ledger.append_block(
        session, ship_id=ship_id, ship_name=req.name, event_type="REGISTER",
        lat=req.origin_lat, lon=req.origin_lon, speed=0,
        payload={"imo": req.imo_number, "certificate_hash": certificate_hash},
        signature=None, signer_pub_key=pub_hex,
    )

    await registry.broadcast({
        "kind": "ship_registered", "ship_id": ship_id, "name": req.name,
        "block_index": block["block_index"],
    })

    logger.info("Registered ship %s (%s)", ship_id, req.name)

    return {
        "ship_id": ship_id,
        "public_key": pub_hex,
        # Private key is returned ONCE at registration time only, exactly as
        # a real PKI enrollment would hand the device its key material once.
        # No other endpoint exposes it for production use; see
        # /api/ships/{ship_id}/private_key for the explicit demo-only
        # exception used by this platform's cryptography sandbox page.
        "private_key_pem": priv_pem,
        "password_hash": password_hash,
        "certificate_hash": certificate_hash,
        "block_index": block["block_index"],
        "block_hash": block["block_hash"],
    }


@router.get("")
async def list_ships(session: AsyncSession = Depends(get_db), user=Depends(get_current_user_optional)):
    """Control Station sees the whole fleet. A Ship Captain only ever
    sees the single ship they're assigned to (or an empty list if not
    yet assigned) - enforced here, not just by hiding rows in the UI.
    Passengers aren't accounts at all (see login gate) - no token means
    the same public, read-only, full-fleet view this always had.
    """
    query = select(Ship).order_by(Ship.created_at.desc())
    if user is not None and user.role == "ship_captain":
        if not user.ship_id:
            return []
        query = select(Ship).where(Ship.ship_id == user.ship_id)
    result = await session.execute(query)
    ships = result.scalars().all()
    return [ship_to_public_dict(s) for s in ships]


@router.get("/{ship_id}")
async def get_ship(ship_id: str, session: AsyncSession = Depends(get_db), user=Depends(get_current_user_optional)):
    if user is not None and user.role == "ship_captain" and user.ship_id != ship_id:
        raise HTTPException(403, "Ship Captains may only view their own assigned ship")
    ship = await _get_ship_or_404(session, ship_id)
    return ship_to_public_dict(ship)


@router.get("/{ship_id}/private_key")
async def get_private_key_for_demo(ship_id: str, session: AsyncSession = Depends(get_db)):
    """Exposes the private key ONLY for the in-app cryptography sandbox so
    the demo can sign messages client-side without re-deriving keys. In a
    production deployment this endpoint would not exist - the private key
    would never leave the ship's hardware. It is kept here, clearly labeled,
    because this is an educational/demo platform that needs to show the
    signing step actually happening.
    """
    ship = await _get_ship_or_404(session, ship_id)
    return {"private_key_pem": ship.private_key_pem}


@router.get("/{ship_id}/positions")
async def get_ship_positions(ship_id: str, session: AsyncSession = Depends(get_db)):
    """Full position history for a ship - powers the live movement playback
    and the actual-vs-expected route visualization.
    """
    ship = await _get_ship_or_404(session, ship_id)

    result = await session.execute(
        select(Position).where(Position.ship_id == ship_id).order_by(Position.recorded_at.asc())
    )
    positions = result.scalars().all()

    return {
        "ship": ship_to_public_dict(ship),
        "expected_route": {
            "origin": {"lat": ship.origin_lat, "lon": ship.origin_lon, "label": ship.origin_port},
            "destination": {"lat": ship.dest_lat, "lon": ship.dest_lon, "label": ship.dest_port},
        },
        "actual_track": [
            {
                "lat": p.lat, "lon": p.lon, "speed": p.speed,
                "heading": p.heading,
                "recorded_at": p.recorded_at.isoformat() if p.recorded_at else None,
                "signature_valid": bool(p.signature_valid),
                "block_index": p.block_index,
            } for p in positions
        ],
    }


@router.delete("/{ship_id}")
async def delete_ship(ship_id: str, session: AsyncSession = Depends(get_db)):
    """Removes a ship and its operational records (voyages, position
    history, alerts, messages, auth events). Blockchain blocks already
    written for this ship are deliberately left in place rather than
    deleted - the chain is an append-only ledger, and rewriting history by
    deleting blocks would break the hash-chain guarantee for every block
    written after them. This mirrors how a real ledger would "archive"
    rather than erase a deregistered vessel's past entries.
    """
    ship = await _get_ship_or_404(session, ship_id)

    await session.execute(delete(Voyage).where(Voyage.ship_id == ship_id))
    await session.execute(delete(Position).where(Position.ship_id == ship_id))
    await session.execute(delete(Alert).where(Alert.ship_id == ship_id))
    await session.execute(delete(Message).where(Message.ship_id == ship_id))
    await session.execute(delete(AuthEvent).where(AuthEvent.ship_id == ship_id))
    await session.delete(ship)
    await session.commit()

    logger.info("ship_deleted", extra={"ship_id": ship_id})
    return {"status": "deleted", "ship_id": ship_id}


@router.post("/admin/reset-all")
async def reset_all_data(session: AsyncSession = Depends(get_db)):
    """Wipes every ship, voyage, position, alert, message, auth event,
    replay-protection nonce, and blockchain block. This is a full reset for
    starting the demo/dataset over from empty - unlike the per-ship DELETE
    endpoint, it also clears the blockchain, since the intent here is a
    clean slate rather than deregistering one vessel from a live ledger.
    Irreversible; intended for development/demo use.
    """
    for model in (Voyage, Position, Alert, Message, AuthEvent, UsedNonce, Block, Ship):
        await session.execute(delete(model))
    await session.commit()
    logger.info("full_data_reset")
    return {"status": "reset", "message": "All ships, voyages, alerts and blockchain data cleared."}
