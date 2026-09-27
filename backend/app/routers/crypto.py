"""
crypto.py (router)
----------------------
Live cryptography sandbox endpoints used by the platform's "Cryptography"
page: real ECDSA sign/verify, real ECDH key agreement between two
registered ships, real AES-256-CBC encrypt/decrypt, and real SHA-256
hashing. Every call here goes through app.crypto.cryptocore and produces
output that is actually verifiable, not illustrative/fake values.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import cryptocore as crypto
from app.schemas.crypto import (
    SignDemoRequest, VerifyDemoRequest, EcdhDemoRequest, AesDemoRequest, HashDemoRequest,
)
from app.core.dependencies import get_db
from app.db.models import Ship

router = APIRouter(prefix="/api/crypto", tags=["crypto"])


async def _get_ship_or_404(session: AsyncSession, ship_id: str) -> Ship:
    ship = await session.get(Ship, ship_id)
    if not ship:
        raise HTTPException(404, "Ship not found")
    return ship


@router.post("/sign")
async def crypto_sign(req: SignDemoRequest, session: AsyncSession = Depends(get_db)):
    ship = await _get_ship_or_404(session, req.ship_id)
    signature = crypto.sign_message(ship.private_key_pem, req.message)
    return {"signature": signature, "public_key": ship.public_key}


@router.post("/verify")
async def crypto_verify(req: VerifyDemoRequest, session: AsyncSession = Depends(get_db)):
    ship = await _get_ship_or_404(session, req.ship_id)
    is_valid = crypto.verify_signature(ship.public_key, req.message, req.signature)
    return {"is_valid": is_valid}


@router.post("/ecdh")
async def crypto_ecdh(req: EcdhDemoRequest, session: AsyncSession = Depends(get_db)):
    ship_a = await session.get(Ship, req.ship_a_id)
    ship_b = await session.get(Ship, req.ship_b_id)
    if not ship_a or not ship_b:
        raise HTTPException(404, "One or both ships not found")

    key_from_a = crypto.ecdh_derive_session_key(ship_a.private_key_pem, ship_b.public_key)
    key_from_b = crypto.ecdh_derive_session_key(ship_b.private_key_pem, ship_a.public_key)

    return {
        "ship_a_public_key": ship_a.public_key,
        "ship_b_public_key": ship_b.public_key,
        "derived_key_from_a_side": key_from_a.hex(),
        "derived_key_from_b_side": key_from_b.hex(),
        "keys_match": key_from_a == key_from_b,
    }


@router.post("/aes")
async def crypto_aes(req: AesDemoRequest):
    key = bytes.fromhex(req.key_hex)
    if len(key) != 32:
        raise HTTPException(400, "Key must be 32 bytes (64 hex chars) for AES-256")

    if req.mode == "encrypt":
        if not req.plaintext:
            raise HTTPException(400, "plaintext is required for encrypt mode")
        result = crypto.aes_encrypt(key, req.plaintext)
        return result
    elif req.mode == "decrypt":
        if not req.iv_hex or not req.ciphertext_hex:
            raise HTTPException(400, "iv_hex and ciphertext_hex are required for decrypt mode")
        try:
            plaintext = crypto.aes_decrypt(key, req.iv_hex, req.ciphertext_hex)
        except Exception:
            raise HTTPException(400, "Decryption failed - wrong key or corrupted ciphertext")
        return {"plaintext": plaintext}
    else:
        raise HTTPException(400, "mode must be 'encrypt' or 'decrypt'")


@router.post("/hash")
async def crypto_hash(req: HashDemoRequest):
    return {"sha256": crypto.sha256_hex(req.data), "input_length_bytes": len(req.data.encode("utf-8"))}


@router.post("/keypair")
async def crypto_new_keypair():
    """Generates a standalone ECC key pair not tied to any ship, for the
    sandbox's 'generate fresh keys' button.
    """
    priv_pem, pub_hex = crypto.generate_keypair()
    return {"private_key_pem": priv_pem, "public_key": pub_hex}
