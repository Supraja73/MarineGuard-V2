"""
cryptocore.py
--------------
Real cryptographic primitives for the maritime security platform.
No simulated/fake math anywhere - every function here calls into the
`cryptography` library and produces output that is actually verifiable.

Algorithms used (matches the project's security stack):
  - ECC (SECP256R1 / NIST P-256)  -> ship identity key pairs
  - ECDSA                          -> message signing / verification
  - ECDH                           -> session key agreement
  - AES-256-CBC                    -> channel encryption
  - SHA-256                        -> hashing, blockchain block hashes
  - bcrypt (via passlib-style impl)-> operator password hashing
"""

import os
import hashlib
import hmac
import base64
import secrets

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidSignature
import bcrypt


CURVE = ec.SECP256R1()


# ───────────────────────── ECC KEY MANAGEMENT ─────────────────────────

def generate_keypair():
    """Generate a fresh ECC (NIST P-256) private/public key pair.
    Returns PEM-encoded private key and the public key as compressed hex.
    """
    private_key = ec.generate_private_key(CURVE)
    public_key = private_key.public_key()

    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    pub_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.CompressedPoint,
    )
    pub_hex = pub_bytes.hex()

    return priv_pem, pub_hex


def load_private_key(pem_str: str):
    return serialization.load_pem_private_key(pem_str.encode(), password=None)


def load_public_key_from_hex(pub_hex: str):
    pub_bytes = bytes.fromhex(pub_hex)
    return ec.EllipticCurvePublicKey.from_encoded_point(CURVE, pub_bytes)


# ───────────────────────── ECDSA SIGN / VERIFY ─────────────────────────

def sign_message(priv_pem: str, message: str) -> str:
    """Sign a UTF-8 message with ECDSA/SHA-256. Returns hex-encoded DER signature."""
    private_key = load_private_key(priv_pem)
    signature = private_key.sign(
        message.encode("utf-8"),
        ec.ECDSA(hashes.SHA256()),
    )
    return signature.hex()


def verify_signature(pub_hex: str, message: str, signature_hex: str) -> bool:
    """Verify an ECDSA signature. Returns True/False - never throws to caller."""
    try:
        public_key = load_public_key_from_hex(pub_hex)
        signature = bytes.fromhex(signature_hex)
        public_key.verify(
            signature,
            message.encode("utf-8"),
            ec.ECDSA(hashes.SHA256()),
        )
        return True
    except (InvalidSignature, ValueError, Exception):
        return False


# ───────────────────────── ECDH SESSION KEYS ─────────────────────────

def ecdh_derive_session_key(my_priv_pem: str, their_pub_hex: str) -> bytes:
    """Perform real ECDH key agreement and derive a 32-byte AES-256 key via HKDF."""
    private_key = load_private_key(my_priv_pem)
    their_public_key = load_public_key_from_hex(their_pub_hex)
    shared_secret = private_key.exchange(ec.ECDH(), their_public_key)

    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"marineguard-session-key",
    ).derive(shared_secret)
    return derived


# ───────────────────────── AES-256 ENCRYPTION ─────────────────────────

def aes_encrypt(key: bytes, plaintext: str) -> dict:
    """AES-256-CBC encrypt. Returns dict with hex iv + hex ciphertext."""
    iv = os.urandom(16)
    padded = _pkcs7_pad(plaintext.encode("utf-8"))
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return {"iv": iv.hex(), "ciphertext": ciphertext.hex()}


def aes_decrypt(key: bytes, iv_hex: str, ciphertext_hex: str) -> str:
    iv = bytes.fromhex(iv_hex)
    ciphertext = bytes.fromhex(ciphertext_hex)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    return _pkcs7_unpad(padded).decode("utf-8")


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len]) * pad_len


def _pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
    """Validates PKCS7 padding before removing it. A naive implementation
    that just reads the last byte as a length and slices is unsafe: when
    decrypting with the wrong key, the 'plaintext' is random garbage, and
    its last byte can coincidentally be a small number that slices off a
    plausible-looking chunk instead of raising an error - silently
    returning corrupted output dressed up as a successful decryption.
    Checking that (a) the padding length is in the valid 1..block_size
    range and (b) every one of those trailing bytes actually equals that
    length, turns "wrong key" into a clear, reported failure instead of a
    silent wrong answer.
    """
    if len(data) == 0 or len(data) % block_size != 0:
        raise ValueError("Invalid padded data length")

    pad_len = data[-1]
    if pad_len < 1 or pad_len > block_size:
        raise ValueError("Invalid PKCS7 padding")

    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("Invalid PKCS7 padding")

    return data[:-pad_len]


# ───────────────────────── SHA-256 HASHING ─────────────────────────

def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ───────────────────────── PASSWORD HASHING ─────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ───────────────────────── MISC ─────────────────────────

def random_token(n_bytes: int = 16) -> str:
    return secrets.token_hex(n_bytes)
