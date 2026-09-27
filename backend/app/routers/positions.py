"""
positions.py (router)
-------------------------
Receives signed position reports from ships. The server rebuilds the
canonical message from the structured fields it received and verifies the
ECDSA signature against that reconstruction (never against client-supplied
free text), then enforces a one-time-use nonce to block replay attacks,
before accepting the reading, logging it to the blockchain, and running
the automatic anomaly-detection suite.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import cryptocore as crypto
from app.blockchain import ledger
from app.schemas.positions import PositionReportRequest
from app.core.dependencies import get_db
from app.db.models import Ship, Position, UsedNonce, Voyage
from app.services.position_service import build_position_message, compute_heading
from app.services.alert_service import raise_alert
from app.services.detection_service import run_detection_suite
from app.services.voyage_service import update_voyage_progress, voyage_to_dict
from app.core.logging_config import get_logger

router = APIRouter(prefix="/api/positions", tags=["positions"])
logger = get_logger("app.routers.positions")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/report")
async def report_position(req: PositionReportRequest, request: Request, session: AsyncSession = Depends(get_db)):
    registry = request.app.state.connection_registry

    ship = await session.get(Ship, req.ship_id)
    if not ship:
        raise HTTPException(404, "Ship not found")

    # The server rebuilds the canonical message from the structured fields
    # it actually received and verifies the signature against THAT string -
    # never against a free-form string supplied by the client. If the
    # signature were checked against client-supplied text instead, an
    # attacker could keep a valid old signature attached to a forged
    # message and the check would still pass. Binding the signature to the
    # server's own reconstruction of lat/lon/speed/timestamp/nonce is what
    # actually defends against message tampering.
    canonical_message = build_position_message(
        req.ship_id, req.lat, req.lon, req.speed, req.timestamp, req.nonce
    )
    sig_valid = crypto.verify_signature(ship.public_key, canonical_message, req.signature)

    recorded_at = _now_utc()
    last_pos_result = await session.execute(
        select(Position).where(Position.ship_id == req.ship_id).order_by(Position.recorded_at.desc()).limit(1)
    )
    last_pos = last_pos_result.scalar_one_or_none()

    heading = compute_heading(
        last_pos.lat if last_pos else None,
        last_pos.lon if last_pos else None,
        req.lat, req.lon,
    )

    if not sig_valid:
        await raise_alert(
            session, registry,
            ship_id=req.ship_id, ship_name=ship.name, alert_type="signature_failure",
            severity="critical", title="ECDSA Signature Verification Failed",
            detail=f"Position report for {ship.name} failed signature verification. "
                   f"The signature does not match the reported lat/lon/speed/timestamp. "
                   f"Reading rejected and not stored as a trusted position.",
            lat=req.lat, lon=req.lon,
        )
        await ledger.append_block(
            session, ship_id=req.ship_id, ship_name=ship.name, event_type="ANOMALY",
            lat=req.lat, lon=req.lon, speed=req.speed,
            payload={"reason": "signature_verification_failed"},
            signature=req.signature, signer_pub_key=ship.public_key,
        )
        raise HTTPException(400, "ECDSA signature verification failed - reading rejected")

    # Replay-attack defense: a (ship_id, nonce) pair can only ever be
    # consumed once. Resubmitting a previously valid, fully-signed message
    # (a captured-and-resent attack) is rejected even though its signature
    # is perfectly valid, because the signature being valid only proves the
    # ship authored it once - not that this submission is fresh.
    already_used_result = await session.execute(
        select(UsedNonce).where(UsedNonce.ship_id == req.ship_id, UsedNonce.nonce == req.nonce)
    )
    already_used = already_used_result.scalar_one_or_none()
    if already_used:
        await raise_alert(
            session, registry,
            ship_id=req.ship_id, ship_name=ship.name, alert_type="replay_attack",
            severity="critical", title="Replay Attack Detected",
            detail=f"A position report for {ship.name} reused a nonce that was already "
                   f"consumed. The message signature is valid but the submission itself is a "
                   f"duplicate/replay and has been rejected.",
            lat=req.lat, lon=req.lon,
        )
        raise HTTPException(409, "Replay detected - this signed message has already been consumed")

    session.add(UsedNonce(ship_id=req.ship_id, nonce=req.nonce, used_at=recorded_at))

    # Routine, successful position updates are NOT written to the
    # blockchain. A simulated ship reporting a fresh position every few
    # seconds would otherwise grow the chain by hundreds of blocks per
    # voyage for an event with no real significance - the ledger is meant
    # to record meaningful events (registration, voyage lifecycle, alerts,
    # security incidents), not a raw telemetry firehose. The position is
    # still fully recorded in the `positions` table (with its verified
    # signature) for track history and the Security & Verification panel;
    # it just isn't also chained.
    session.add(Position(
        ship_id=req.ship_id, lat=req.lat, lon=req.lon, speed=req.speed, heading=heading,
        recorded_at=recorded_at, signature_valid=1, block_index=None,
    ))

    ship.current_lat = req.lat
    ship.current_lon = req.lon
    ship.current_speed = req.speed
    ship.status = "online"

    # Keep the ship's active voyage's live progress (distance travelled/
    # remaining, ETA) in sync with every accepted position report, and
    # auto-complete the voyage the moment the ship arrives - rather than
    # requiring a separate manual "I've arrived" action.
    voyage_dict = None
    voyage_completed = False
    voyage_result = await session.execute(
        select(Voyage).where(Voyage.ship_id == req.ship_id, Voyage.status == "in_progress").limit(1)
    )
    active_voyage = voyage_result.scalar_one_or_none()
    if active_voyage:
        arrived = update_voyage_progress(active_voyage, req.lat, req.lon, req.speed)
        if arrived:
            was_already_holding = ship.status == "holding"
            if ship.status == "holding":
                # Arrived at a temporary cyclone-diversion safe port, NOT
                # the voyage's real final destination. Completing the
                # voyage here would permanently strand the ship: the
                # cyclone monitor only re-checks voyages with
                # status == "in_progress" (see
                # app.services.cyclone_monitor), so a "completed" voyage
                # would never be re-evaluated for auto-resume once the
                # storm clears. Stay in_progress/holding instead - the
                # monitor keeps checking every tick and will propose a
                # "resume" RerouteRequest the moment the route to the
                # ORIGINAL destination is clear again (see
                # propose_resume_for_voyage).
                active_voyage.distance_remaining_km = 0
                ship.status = "holding"
                if not was_already_holding:
                    await ledger.append_block(
                        session, ship_id=ship.ship_id, ship_name=ship.name, event_type="HOLDING_AT_SAFE_PORT",
                        lat=lat, lon=lon,
                        payload={"voyage_id": active_voyage.voyage_id, "safe_port": active_voyage.dest_port},
                    )
            else:
                active_voyage.status = "completed"
                active_voyage.completed_at = recorded_at
                active_voyage.distance_remaining_km = 0
                ship.status = "docked"
                voyage_completed = True
        voyage_dict = voyage_to_dict(active_voyage)

    await session.commit()

    if voyage_completed:
        await raise_alert(
            session, registry,
            ship_id=ship.ship_id, ship_name=ship.name, alert_type="voyage_completed",
            severity="info", title=f"\u2705 {ship.name} has reached destination",
            detail=f"Arrived at {active_voyage.dest_port}.",
            lat=active_voyage.dest_lat, lon=active_voyage.dest_lon,
        )
        await ledger.append_block(
            session, ship_id=ship.ship_id, ship_name=ship.name, event_type="DESTINATION_REACHED",
            lat=active_voyage.dest_lat, lon=active_voyage.dest_lon,
            payload={"voyage_id": active_voyage.voyage_id, "dest_port": active_voyage.dest_port},
        )
        await ledger.append_block(
            session, ship_id=ship.ship_id, ship_name=ship.name, event_type="VOYAGE_COMPLETED",
            lat=active_voyage.dest_lat, lon=active_voyage.dest_lon,
            payload={"voyage_id": active_voyage.voyage_id},
        )
        await registry.broadcast({"kind": "voyage_completed", "ship_id": ship.ship_id, "voyage_id": active_voyage.voyage_id})

    triggered_alerts = await run_detection_suite(session, registry, ship, req.lat, req.lon, req.speed, last_pos)

    await registry.broadcast({
        "kind": "position_update", "ship_id": req.ship_id, "lat": req.lat, "lon": req.lon,
        "speed": req.speed, "heading": heading,
        "alerts_triggered": len(triggered_alerts),
    })

    return {
        "accepted": True,
        "signature_valid": True,
        "heading": heading,
        "alerts_triggered": triggered_alerts,
        "voyage": voyage_dict,
        "voyage_completed": voyage_completed,
    }


@router.get("/build_message")
async def get_message_template(ship_id: str, lat: float, lon: float, speed: float, timestamp: str, nonce: str):
    """Lets the frontend ask the server for the exact canonical string it
    will later verify against, so the browser-side signing step (done with
    the ship's private key the registration step handed back) signs
    byte-for-byte the same thing the server checks. This keeps the
    canonical format defined in exactly one place
    (app.services.position_service.build_position_message) instead of
    being duplicated and risking drift between frontend and backend.
    """
    return {"message": build_position_message(ship_id, lat, lon, speed, timestamp, nonce)}
