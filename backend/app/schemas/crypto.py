"""
crypto.py (schemas)
----------------------
Request models for the cryptography sandbox/demo endpoints: ECDSA
sign/verify, ECDH key exchange, AES-256 encrypt/decrypt, SHA-256 hashing.
"""

from typing import Optional
from pydantic import BaseModel


class SignDemoRequest(BaseModel):
    ship_id: str
    message: str


class VerifyDemoRequest(BaseModel):
    ship_id: str
    message: str
    signature: str


class EcdhDemoRequest(BaseModel):
    ship_a_id: str
    ship_b_id: str


class AesDemoRequest(BaseModel):
    key_hex: str
    plaintext: Optional[str] = None
    iv_hex: Optional[str] = None
    ciphertext_hex: Optional[str] = None
    mode: str  # "encrypt" or "decrypt"


class HashDemoRequest(BaseModel):
    data: str
