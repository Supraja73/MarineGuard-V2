"""
models.py
-----------
SQLAlchemy 2.0 ORM models, replacing the hand-written SQLite schema that
used to live in storage.init_schema() / ledger.init_db(). Column types are
chosen to be Postgres-native (Float, not the SQLite-ish REAL string;
DateTime(timezone=True) rather than storing ISO strings in TEXT) while
keeping every field name identical to the old schema so the rest of the
codebase's field access patterns don't need to change.

Two things carried over deliberately from the SQLite version because they
encode real lessons, not arbitrary choices:

1. Blocks are still keyed by an application-assigned integer
   (`block_index`), not just an opaque surrogate primary key, because the
   hash chain's content string includes "block_index" and that value has
   to be stable and predictable (1, 2, 3, ...) for the hash to mean what
   the verification logic expects.
2. Numeric columns map to Python float consistently, both in SQLite and
   in Postgres (DOUBLE PRECISION), which matters because ledger.py hashes
   these values - see app.blockchain.ledger._normalize_number for why
   type stability across insert/read-back is load-bearing here, not
   cosmetic.
"""

from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, Text, DateTime, ForeignKey, UniqueConstraint, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ship(Base):
    __tablename__ = "ships"

    ship_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    imo_number: Mapped[str | None] = mapped_column(String, nullable=True)
    ship_type: Mapped[str | None] = mapped_column(String, nullable=True)
    origin_port: Mapped[str | None] = mapped_column(String, nullable=True)
    origin_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    dest_port: Mapped[str | None] = mapped_column(String, nullable=True)
    dest_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    dest_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    certificate_hash: Mapped[str] = mapped_column(Text, nullable=False)
    current_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_speed: Mapped[float | None] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String, default="registered")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Profile fields used by the Live Tracking / Ship Details UI. All
    # nullable because they're optional at registration time and older
    # rows won't have them.
    mmsi: Mapped[str | None] = mapped_column(String, nullable=True)
    captain: Mapped[str | None] = mapped_column(String, nullable=True)
    flag: Mapped[str | None] = mapped_column(String, nullable=True)
    length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    beam_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    course: Mapped[float | None] = mapped_column(Float, nullable=True)
    heading: Mapped[float | None] = mapped_column(Float, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    capacity_tons: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_moved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Voyage(Base):
    """A single trip for a ship: origin -> destination at a declared speed.

    Ship registration still bootstraps the ship's very first voyage (so
    existing registration flow keeps working unchanged), but voyages are
    a first-class concept on top of that: a ship can start a new voyage,
    replan its destination mid-trip, or complete a voyage, all without
    touching its registered identity (keys, certificate, password).
    """
    __tablename__ = "voyages"

    voyage_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ship_id: Mapped[str] = mapped_column(String, ForeignKey("ships.ship_id"), nullable=False, index=True)

    origin_port: Mapped[str] = mapped_column(String, nullable=False)
    origin_lat: Mapped[float] = mapped_column(Float, nullable=False)
    origin_lon: Mapped[float] = mapped_column(Float, nullable=False)
    dest_port: Mapped[str] = mapped_column(String, nullable=False)
    dest_lat: Mapped[float] = mapped_column(Float, nullable=False)
    dest_lon: Mapped[float] = mapped_column(Float, nullable=False)

    current_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_speed_kmh: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    geofence_radius_km: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    distance_total_km: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    distance_travelled_km: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    distance_remaining_km: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # in_progress | completed | replanned
    status: Mapped[str] = mapped_column(String, nullable=False, default="in_progress")
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Position(Base):
    __tablename__ = "positions"

    position_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ship_id: Mapped[str] = mapped_column(String, ForeignKey("ships.ship_id"), nullable=False, index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False)
    heading: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signature_valid: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    block_index: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ship_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    ship_name: Mapped[str | None] = mapped_column(String, nullable=True)
    alert_type: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    block_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Stateful-alert support: while a condition persists (e.g. a ship stays
    # off its route), the SAME row is reused instead of inserting a new one
    # every detection cycle. `resolved` flips to 1 the moment the condition
    # clears, at which point a future recurrence opens a fresh alert rather
    # than resurrecting the old one. `last_seen_at`/`occurrence_count` let the
    # UI show "still ongoing, last confirmed 30s ago (x14)" instead of a wall
    # of duplicate rows.
    resolved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RestrictedZone(Base):
    __tablename__ = "restricted_zones"

    zone_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    zone_type: Mapped[str] = mapped_column(String, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    radius_km: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WeatherReading(Base):
    __tablename__ = "weather_readings"

    reading_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    region: Mapped[str] = mapped_column(String, nullable=False)
    wind_speed_kmh: Mapped[float] = mapped_column(Float, nullable=False)
    pressure_hpa: Mapped[float] = mapped_column(Float, nullable=False)
    wave_height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    rainfall_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UsedNonce(Base):
    __tablename__ = "used_nonces"
    __table_args__ = (UniqueConstraint("ship_id", "nonce", name="uq_used_nonces_ship_nonce"),)

    # Postgres doesn't support multi-column primary keys via simple
    # mapped_column composition as cleanly as a dedicated surrogate key
    # plus a unique constraint, and the application only ever needs
    # existence checks, not the composite key itself - so this keeps the
    # same guarantee (a (ship_id, nonce) pair can be used at most once)
    # without fighting the ORM.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ship_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    nonce: Mapped[str] = mapped_column(String, nullable=False)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Block(Base):
    __tablename__ = "blocks"

    # block_index is the application-assigned chain position (1, 2, 3, ...)
    # referenced inside the hash content string itself - see ledger.py.
    block_index: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ship_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    ship_name: Mapped[str | None] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    signer_pub_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[str] = mapped_column(String, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String, nullable=False)
    block_hash: Mapped[str] = mapped_column(String, nullable=False)
    # PoET (Proof of Elapsed Time) leader-election metadata. Purely
    # informational — which simulated validator "won" the right to
    # propose this block, and how long its randomly-drawn wait was.
    # Deliberately NOT part of the hashed content (see ledger.py
    # _block_content_string) so it can't affect existing tamper-evidence
    # guarantees or break already-computed hashes.
    proposer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    poet_wait_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    poet_draws: Mapped[str | None] = mapped_column(Text, nullable=True)


class User(Base):
    """A real account, distinct from a Ship's ECC identity (Ship auth in
    app.routers.auth is the ship-to-control-center crypto handshake; this
    is the human login used by the web UI). Password is stored as a
    bcrypt hash, never plaintext.

    role: "control_station" (full operational authority) or
    "ship_captain" (scoped to their own assigned ship/voyages only).
    A ship_captain's ship_id points at the one ship they're allowed to
    see; it may be null if they registered before being assigned one.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # control_station | ship_captain
    ship_id: Mapped[str | None] = mapped_column(String, ForeignKey("ships.ship_id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Approval workflow (Ship Captains only). control_station accounts are
    # always "approved" (they're created directly by a System
    # Administrator / existing Control Station operator - never through
    # public self-registration - so there's nothing to approve).
    # ship_captain accounts start "pending" on self-registration and can't
    # log in or reach any operational page until a Control Station
    # operator approves them (which also assigns their one ship) or
    # rejects them. status: "pending" | "approved" | "rejected".
    status: Mapped[str] = mapped_column(String, nullable=False, default="approved")
    reviewed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # System Administrator account management (Control Center accounts
    # only, in practice - a captain who should lose access is rejected
    # instead, see status above). An existing Control Station operator
    # acts as the System Administrator (there's no separate admin login):
    # they can disable another Control Center account (e.g. an operator
    # who left) without deleting it, and reset its password. A disabled
    # account is blocked at login regardless of role.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UserSession(Base):
    """Server-side session token issued on login. The frontend sends the
    token back as `Authorization: Bearer <token>`; app.core.dependencies
    looks it up here (not a JWT - a real, revocable, DB-backed session,
    so logout / expiry actually take the token out of service instead of
    it remaining valid until a signature-only token would naturally
    expire).
    """
    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RerouteRequest(Base):
    """A proposed change to an in-progress voyage that a cyclone has put in
    the way of. Nothing here ever touches the live voyage until a Control
    Station operator approves it - this table is the pending/approved/
    rejected paper trail for that decision.

    kind = "alternate_route": a searoute path was found that clears every
        active cyclone by a safety buffer. `proposed_waypoints_json` holds
        the ordered [lat,lon] pairs to splice into the voyage.
    kind = "safe_port": no clear path to the original destination could be
        found, so the ship is proposed to divert and hold at the nearest
        port that isn't itself inside a cyclone's radius, until the storm
        clears. `proposed_port` / `proposed_port_lat` / `proposed_port_lon`
        describe that holding port. `original_dest_port/lat/lon` remember
        where the ship was actually headed so the voyage can resume once
        approved to do so.
    kind = "resume": once cleared, proposes resuming the original voyage
        destination from the holding port.
    """
    __tablename__ = "reroute_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    voyage_id: Mapped[int] = mapped_column(Integer, ForeignKey("voyages.voyage_id"), nullable=False, index=True)
    ship_id: Mapped[str] = mapped_column(String, ForeignKey("ships.ship_id"), nullable=False, index=True)

    kind: Mapped[str] = mapped_column(String, nullable=False)  # alternate_route | safe_port | resume
    cyclone_name: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    proposed_waypoints_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)

    proposed_port: Mapped[str | None] = mapped_column(String, nullable=True)
    proposed_port_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_port_lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    original_dest_port: Mapped[str | None] = mapped_column(String, nullable=True)
    original_dest_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_dest_lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    # pending | approved | rejected
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ControlCenterIdentity(Base):
    """Singleton table (always exactly one row, id=1) holding the Control
    Center's own ECC key pair. Ships authenticate to and exchange session
    keys with this identity via real ECDH (app.crypto.cryptocore), the
    same way two ships would establish a session key with each other in
    app.routers.crypto's existing /api/crypto/ecdh demo - except this is
    the one fixed "other party" used for ship<->Control communication.
    Generated lazily on first use, not seeded.
    """
    __tablename__ = "control_center_identity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthEvent(Base):
    """A record of a mutual-authentication attempt: a challenge string is
    signed with the ship's real ECDSA private key and verified with its
    real public key, exactly like the existing /api/crypto/sign + /verify
    flow, but persisted here so the Authentication page has a genuine
    history instead of a client-side-only fake log.
    """
    __tablename__ = "auth_events"

    auth_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ship_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    ship_name: Mapped[str] = mapped_column(String, nullable=False)
    challenge: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    session_key_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    success: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Message(Base):
    """A Control-Center <-> ship message. The message is genuinely
    AES-256-CBC encrypted using a session key derived via real ECDH
    between the Control Center's key pair and the ship's registered key
    pair (see app.crypto.cryptocore.ecdh_derive_session_key) - the
    ciphertext and IV stored here are real, verifiable encryption output,
    not placeholder text.

    The plaintext is ALSO stored (plaintext_preview), purely so the comms
    log can render message content without re-deriving the session key on
    every read. This mirrors the same deliberate, clearly-labeled exception
    already made for ship private keys elsewhere in this codebase (kept
    server-side for this educational/demo platform) - a production
    deployment would not retain plaintext server-side at all.
    """
    __tablename__ = "messages"

    message_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ship_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    ship_name: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)  # "to_ship" or "from_ship"
    message_type: Mapped[str] = mapped_column(String, nullable=False)
    ciphertext_hex: Mapped[str] = mapped_column(Text, nullable=False)
    iv_hex: Mapped[str] = mapped_column(String, nullable=False)
    plaintext_preview: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Cyclone(Base):
    __tablename__ = "cyclones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    center_lat: Mapped[float] = mapped_column(Float, nullable=False)
    center_lon: Mapped[float] = mapped_column(Float, nullable=False)
    radius_km: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
