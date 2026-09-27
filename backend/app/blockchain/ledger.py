"""
ledger.py
---------
A real append-only blockchain ledger. Every block's hash is computed from
its own content plus the previous block's hash (SHA-256 linked chain).
Tampering with any stored field changes the recomputed hash, so integrity
checking is genuine, not cosmetic.

This module now persists blocks via SQLAlchemy 2.0 / asyncpg against
PostgreSQL instead of raw sqlite3, but the hashing semantics are
unchanged from the SQLite version - see _normalize_number for why that
matters and _block_content_string for the exact canonical format. Schema
creation is handled by Alembic migrations, not by this module.

--------------------------------------------------------------------
NEW: live "PoET Validator Wait Time" broadcast (see poet.py for the
real-timer measurement change this depends on)
--------------------------------------------------------------------
Every call to append_block() already runs one PoET election (one
"consensus round"). This module now also: (1) prints that round to the
console in a simple, readable log, (2) keeps the most recent round in
memory so a freshly-loaded dashboard can fetch it once over REST, and
(3) pushes it to every connected websocket client the instant the block
commits, so the live Wait Time graph updates with no page refresh.
None of this touches the hash-chain, the election rule, or any existing
field — it only reads the election result that was already being
computed.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import cryptocore as crypto
from app.blockchain import poet
from app.db.models import Block

GENESIS_PREV_HASH = "0" * 64

# Set once at app startup (see app.main.on_startup) to the shared
# ConnectionRegistry's broadcast() coroutine. Kept as a plain module-level
# hook — rather than ledger.py importing the websocket router directly —
# to avoid a circular import (routers already import ledger).
_broadcaster: Optional[Callable[[dict], Awaitable[None]]] = None

# Snapshot of the most recent PoET round, so a dashboard that connects
# AFTER the last block was mined still has something to render instead
# of an empty graph (websocket broadcasts don't replay past events).
_latest_poet_round: Optional[dict] = None
# Rolling history of recent PoET rounds so the "PoET Wait-Time History"
# time-series chart can seed itself with real prior blocks on page load
# (websocket broadcasts don't replay past events). Bounded to keep memory flat.
_poet_history: list = []
_POET_HISTORY_MAX = 200


def set_broadcaster(fn: Callable[[dict], Awaitable[None]]) -> None:
    """Wire up the live event broadcaster. Called once from app startup."""
    global _broadcaster
    _broadcaster = fn


def get_latest_poet_round() -> Optional[dict]:
    """Most recent PoET consensus round (round number, every validator's
    REAL measured wait_ms, and the winner), or None if no block has been
    mined yet in this server process. Backs GET /api/blockchain/poet/latest.
    """
    return _latest_poet_round


def get_poet_history(limit: int = 30) -> list:
    """Return the last N PoET rounds (oldest → newest) for time-series charts."""
    if limit <= 0:
        return []
    return list(_poet_history[-limit:])





def _print_poet_round_log(round_no: int, election: dict) -> None:
    """Console log for one PoET consensus round, matching the existing
    style of the rest of this backend's blockchain logging."""
    print("------------------------------------------------")
    print(f"PoET Consensus Round {round_no}")
    print("------------------------------------------------")
    for vid in poet.VALIDATORS:
        label = poet.DISPLAY_NAMES.get(vid, vid)
        wait_ms = election["all_draws"][vid]
        print(label)
        print(f"Wait Time : {wait_ms:.2f} ms")
    winner_label = poet.DISPLAY_NAMES.get(election["proposer_id"], election["proposer_id"])
    print("Winner:")
    print(winner_label)
    print("Block Created Successfully")
    print("------------------------------------------------")

# An asyncio.Lock, not threading.Lock: the previous SQLite-backed version
# used a thread lock, which is the wrong primitive once append_block does
# real awaited I/O (the Postgres round-trip). A thread lock held across an
# `await` only blocks other *threads*, not other coroutines running on the
# same event loop - two concurrent requests could both read "last block"
# before either commits, and append two blocks that both claim the same
# prev_hash. An asyncio.Lock actually serializes coroutines on this loop.
_lock = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_number(value):
    """Numeric columns round-trip through Postgres as Python floats even
    when an int was inserted (e.g. speed=0 comes back as 0.0) - the same
    behavior the old SQLite REAL columns had. Hashing must use a
    representation that is stable across insert and read-back, otherwise
    a freshly inserted block would fail its own integrity check. Casting
    every numeric field to float (or None) before hashing keeps the
    content string identical at insert time and at verification time.
    """
    if value is None:
        return None
    return float(value)


def _block_content_string(block_index, ship_id, ship_name, event_type,
                           lat, lon, speed, payload, signature, timestamp, prev_hash):
    """Canonical, order-stable string representation used for hashing.
    Any change to any field changes this string, and therefore the hash.
    Unchanged from the SQLite version - this format is what every
    previously-computed block_hash in the database was hashed with, so it
    cannot be altered without invalidating every existing chain.
    """
    obj = {
        "block_index": block_index,
        "ship_id": ship_id,
        "ship_name": ship_name,
        "event_type": event_type,
        "lat": _normalize_number(lat),
        "lon": _normalize_number(lon),
        "speed": _normalize_number(speed),
        "payload": payload,
        "signature": signature,
        "timestamp": timestamp,
        "prev_hash": prev_hash,
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _block_to_dict(block: Block) -> dict:
    return {
        "block_index": block.block_index,
        "ship_id": block.ship_id,
        "ship_name": block.ship_name,
        "event_type": block.event_type,
        "lat": block.lat,
        "lon": block.lon,
        "speed": block.speed,
        "payload": block.payload,
        "signature": block.signature,
        "signer_pub_key": block.signer_pub_key,
        "timestamp": block.timestamp,
        "prev_hash": block.prev_hash,
        "block_hash": block.block_hash,
        "proposer_id": block.proposer_id,
        "poet_wait_ms": block.poet_wait_ms,
        "poet_draws": json.loads(block.poet_draws) if block.poet_draws else None,
    }


async def get_last_block(session: AsyncSession) -> Block | None:
    result = await session.execute(
        select(Block).order_by(Block.block_index.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def append_block(session: AsyncSession, ship_id, ship_name, event_type,
                        lat=None, lon=None, speed=None, payload=None,
                        signature=None, signer_pub_key=None) -> dict:
    """Append a new block to the chain. Serialized via an asyncio.Lock so
    concurrent requests can't both read the same "last block" and append
    two blocks that claim the same prev_hash.
    """
    # PoET leader election: decides which simulated validator is credited
    # with proposing this block. Its result is provenance metadata, kept
    # OUT of the hash content string below — it's not part of what makes
    # the block's integrity verifiable, so it doesn't need next_index or
    # prev_hash to already be known.
    #
    # CHANGED: this now runs a REAL, timed wait per validator (see
    # poet.py) rather than an instant computation, so it's run BEFORE
    # acquiring _lock instead of inside it — there's no reason to
    # serialize other concurrent block appends behind one validator race.
    election = await poet.elect_proposer()

    async with _lock:
        last = await get_last_block(session)
        prev_hash = last.block_hash if last else GENESIS_PREV_HASH
        next_index = (last.block_index + 1) if last else 1
        timestamp = _now_iso()
        payload_str = json.dumps(payload, sort_keys=True) if payload is not None else None

        content = _block_content_string(
            next_index, ship_id, ship_name, event_type, lat, lon, speed,
            payload_str, signature, timestamp, prev_hash
        )
        block_hash = crypto.sha256_hex(content)

        block = Block(
            block_index=next_index,
            ship_id=ship_id,
            ship_name=ship_name,
            event_type=event_type,
            lat=lat,
            lon=lon,
            speed=speed,
            payload=payload_str,
            signature=signature,
            signer_pub_key=signer_pub_key,
            timestamp=timestamp,
            prev_hash=prev_hash,
            block_hash=block_hash,
            proposer_id=election["proposer_id"],
            poet_wait_ms=election["wait_time_ms"],
            poet_draws=json.dumps(election["all_draws"]),
        )
        session.add(block)
        await session.commit()

        result = _block_to_dict(block)

    # ------------------------------------------------------------------
    # NEW: live "PoET Validator Wait Time" feature. Everything above this
    # line is unchanged blockchain/consensus behavior; everything below
    # only *reads* the election result that was already computed, so it
    # can never affect the ledger, the hash chain, or the winner.
    # ------------------------------------------------------------------
    global _latest_poet_round
    poet_round = {
        "round": result["block_index"],
        "validators": [
            {"id": poet.DISPLAY_NAMES.get(vid, vid), "wait_ms": wait_ms}
            for vid, wait_ms in election["all_draws"].items()
        ],
        "winner": poet.DISPLAY_NAMES.get(election["proposer_id"], election["proposer_id"]),
    }
    _latest_poet_round = poet_round
    _poet_history.append(poet_round)
    if len(_poet_history) > _POET_HISTORY_MAX:
        del _poet_history[: len(_poet_history) - _POET_HISTORY_MAX]
    _print_poet_round_log(result["block_index"], election)

    if _broadcaster is not None:
        try:
            await _broadcaster({"kind": "poet_round", **poet_round})
        except Exception:
            # A dead/slow websocket client must never break block
            # creation - the ledger write above already succeeded.
            pass

    return result


async def get_block_by_index(session: AsyncSession, index: int) -> dict | None:
    result = await session.execute(select(Block).where(Block.block_index == index))
    block = result.scalar_one_or_none()
    return _block_to_dict(block) if block else None


async def get_all_blocks(session: AsyncSession, limit: int = None, ship_id: str = None) -> list[dict]:
    query = select(Block)
    if ship_id:
        query = query.where(Block.ship_id == ship_id)
    query = query.order_by(Block.block_index.asc())
    if limit:
        query = query.limit(limit)
    result = await session.execute(query)
    return [_block_to_dict(b) for b in result.scalars().all()]


async def verify_chain_integrity(session: AsyncSession):
    """Recompute every block's hash from its stored fields and check the
    linkage. Returns (is_valid: bool, broken_at: int|None, details: list).
    This is a genuine recomputation, not a stored flag - if a row is
    edited directly in the database, this function will catch it.
    """
    blocks = await get_all_blocks(session)
    details = []
    is_valid = True
    broken_at = None
    prev_hash_expected = GENESIS_PREV_HASH

    for block in blocks:
        content = _block_content_string(
            block["block_index"], block["ship_id"], block["ship_name"],
            block["event_type"], block["lat"], block["lon"], block["speed"],
            block["payload"], block["signature"], block["timestamp"],
            block["prev_hash"]
        )
        recomputed_hash = crypto.sha256_hex(content)

        hash_ok = recomputed_hash == block["block_hash"]
        link_ok = block["prev_hash"] == prev_hash_expected

        block_valid = hash_ok and link_ok
        if not block_valid and is_valid:
            is_valid = False
            broken_at = block["block_index"]

        details.append({
            "block_index": block["block_index"],
            "hash_matches_content": hash_ok,
            "link_matches_previous": link_ok,
            "valid": block_valid,
        })
        prev_hash_expected = block["block_hash"]

    return is_valid, broken_at, details


async def chain_stats(session: AsyncSession) -> dict:
    count_result = await session.execute(select(func.count()).select_from(Block))
    total_blocks = count_result.scalar_one()
    last = await get_last_block(session)
    return {
        "total_blocks": total_blocks,
        "last_event": last.event_type if last else None,
        "last_timestamp": last.timestamp if last else None,
    }
