"""
blockchain.py (router)
--------------------------
Read access to the append-only ledger, real integrity verification
(recomputes every block's hash from its stored fields - this is not a
cached flag), and a deliberately-labeled tamper-demo endpoint that exists
solely to give the verify endpoint something genuine to catch.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.blockchain import ledger
from app.schemas.blockchain import TamperRequest
from app.core.dependencies import get_db
from app.db.models import Block

router = APIRouter(prefix="/api/blockchain", tags=["blockchain"])


@router.get("/blocks")
async def get_blocks(limit: Optional[int] = None, ship_id: Optional[str] = None, session: AsyncSession = Depends(get_db)):
    return await ledger.get_all_blocks(session, limit=limit, ship_id=ship_id)


@router.get("/stats")
async def blockchain_stats(session: AsyncSession = Depends(get_db)):
    return await ledger.chain_stats(session)


@router.get("/poet/latest")
async def poet_latest_round():
    """Most recent PoET consensus round — real measured wait_ms per
    validator plus the winner. Backs the live Validator Wait Time graph's
    initial paint (before its first websocket "poet_round" event arrives).
    Returns null if no block has been mined yet in this server process.
    """
    return ledger.get_latest_poet_round()


@router.get("/poet/history")
async def poet_history(limit: int = 30):
    """Rolling window of the last N PoET rounds, oldest → newest. Seeds the
    'PoET Wait-Time History' time-series graph with real prior blocks so it
    isn't empty on page load."""
    return ledger.get_poet_history(limit=limit)




@router.get("/verify")
async def verify_chain(session: AsyncSession = Depends(get_db)):
    is_valid, broken_at, details = await ledger.verify_chain_integrity(session)
    return {"is_valid": is_valid, "broken_at_block": broken_at, "details": details}


@router.post("/tamper_demo")
async def tamper_demo(req: TamperRequest, session: AsyncSession = Depends(get_db)):
    """Directly mutates a stored block's lat field, bypassing the normal
    append-only API, purely so the integrity-verification endpoint has
    something real to catch. This is the only place in the backend that
    writes to the blocks table outside of ledger.append_block, and it
    exists specifically to demonstrate tamper detection.
    """
    new_lat = req.new_lat if req.new_lat is not None else 0.0
    await session.execute(
        update(Block).where(Block.block_index == req.block_index).values(lat=new_lat)
    )
    await session.commit()
    is_valid, broken_at, details = await ledger.verify_chain_integrity(session)
    return {"tampered_block": req.block_index, "is_valid": is_valid, "broken_at_block": broken_at}
