# MarineGuard Backend

FastAPI backend for the maritime vessel monitoring and blockchain security
platform. Implements real ECC/ECDSA/AES-256/ECDH cryptography, a genuine
SHA-256 hash-linked blockchain ledger, and anomaly detection (GPS spoofing,
route deviation, restricted-zone entry, speed anomalies, cyclone risk).

There is no seed/demo data anywhere in this codebase. Every ship, voyage,
position, alert, and blockchain block is created only by a real action
taken through the API.

## Quickstart (full stack)

```bash
# 1. Postgres
sudo -u postgres psql -c "CREATE USER marineguard WITH PASSWORD 'marineguard';"
sudo -u postgres psql -c "CREATE DATABASE marineguard OWNER marineguard;"

# 2. Backend
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --port 8000

# 3. Open the app
# http://localhost:8000  -  the backend serves frontend/index.html directly
```

That's the whole stack: one server process, one database, no separate
frontend build or dev server. Register a ship, click "Simulate Event" a
few times, and the tracking map, blockchain ledger, and alerts all
populate from real signed actions.

## Project layout

```
backend/
  app/
    main.py                  Application factory, router wiring, startup
    config/
      settings.py             Environment-variable-driven configuration
                               (DATABASE_URL, etc.)
    core/
      logging_config.py       Centralized logging setup
      dependencies.py         Async DB session dependency + websocket registry
    middleware/
      request_logger.py       Logs every request/response/duration
      error_handler.py        Converts all errors to a consistent JSON shape
    crypto/
      cryptocore.py            ECC, ECDSA, ECDH, AES-256-CBC, SHA-256, bcrypt
    blockchain/
      ledger.py                Append-only hash-linked block storage + verification
    detection/
      detection.py             Route deviation, GPS spoofing, geofencing, speed,
                               cyclone risk - real geometry/physics, no randomness
    db/
      models.py                SQLAlchemy 2.0 ORM models (ships, positions,
                               alerts, restricted_zones, weather_readings,
                               used_nonces, blocks)
      session.py               Async engine/session factory (asyncpg)
    services/
      position_service.py      Canonical message builder for signed position reports
      alert_service.py         Alert creation -> blockchain logging -> broadcast
      detection_service.py     Runs the automatic detection suite on each report
    schemas/                  Pydantic request models, one file per domain
    routers/                  One FastAPI router per domain (ships, positions,
                               alerts, detect, zones, weather, blockchain, crypto,
                               dashboard, websocket, auth, comms)
  migrations/                Alembic migration environment and versions/
  alembic.ini
  requirements.txt
  .env.example
  README.md

frontend/
  index.html                 Single-file dashboard UI (no build step). Served
                              automatically by the backend at "/" once it
                              detects this folder next to backend/ - see
                              app/main.py.
```

## Frontend

`frontend/index.html` is a single static file (HTML + CSS + vanilla JS,
plus Leaflet for maps and Chart.js for the one chart on the dashboard).
There is no build step and no separate frontend server - the FastAPI app
serves it directly at `/` when the `frontend/` folder exists next to
`backend/`. Every number, table, and map marker it shows comes from a live
API call; nothing is seeded or hard-coded into the page itself.

If you want to run the frontend against a backend on a different host or
port, edit the `API_BASE` constant near the top of the `<script>` block in
`index.html`.

## Database

PostgreSQL only - there is no SQLite fallback. Schema is managed entirely
through Alembic migrations, never created ad hoc by the application at
startup.

## Setup

Requires a running PostgreSQL server. Create the database and user (adjust
names/password as you like, then update `DATABASE_URL` to match):

```bash
sudo -u postgres psql -c "CREATE USER marineguard WITH PASSWORD 'marineguard';"
sudo -u postgres psql -c "CREATE DATABASE marineguard OWNER marineguard;"
```

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # adjust DATABASE_URL if your setup differs
```

Apply migrations before starting the app for the first time:

```bash
alembic upgrade head
```

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

The API is then available at `http://localhost:8000`. Interactive docs
(Swagger UI) are at `http://localhost:8000/docs`.

Startup fails fast with a clear log message if PostgreSQL isn't reachable
or migrations haven't been applied - check that `alembic upgrade head` has
been run and that `DATABASE_URL` is correct.

## Working with migrations

```bash
# After changing app/db/models.py, generate a new migration:
alembic revision --autogenerate -m "describe the change"

# Review the generated file in migrations/versions/ before applying -
# autogenerate is a strong starting point, not a guarantee, especially
# for renames (it sees a drop+add, not a rename) and data migrations.

alembic upgrade head      # apply
alembic downgrade -1      # roll back one revision
alembic current           # show the currently applied revision
```

## Security notes specific to this implementation

- **Signature binding**: position reports are verified against a message
  the *server* reconstructs from the structured fields it received
  (`app.services.position_service.build_position_message`), never against
  free-form text supplied by the client. This is what actually prevents an
  attacker from tampering with `lat`/`lon`/`speed` while keeping an old
  valid signature attached.
- **Replay protection**: every position report includes a single-use
  `nonce`. A `(ship_id, nonce)` pair can only be consumed once; resubmitting
  a captured, fully-valid signed message is rejected with 409.
- **Chain integrity**: `/api/blockchain/verify` recomputes every block's
  hash from its stored fields and checks the prev-hash linkage - it is not
  a cached flag, so it genuinely detects tampering with stored rows.
- **AES-256 padding validation**: PKCS7 unpadding fully validates the
  padding bytes rather than trusting the last byte blindly, so decrypting
  with the wrong key reliably raises an error instead of occasionally
  returning silently-corrupted plaintext.
- **Concurrent block appends**: `ledger.append_block` is serialized with an
  `asyncio.Lock`, not a `threading.Lock`. A thread lock held across an
  `await` (the database round-trip) only blocks other threads, not other
  coroutines on the same event loop - two concurrent requests could both
  read the same "last block" before either committed and append two blocks
  claiming the same `prev_hash`. The async lock actually prevents that.

## Endpoints

See `http://localhost:8000/docs` for the complete, always-current list with
request/response schemas. Major groups:

- `POST /api/ships/register`, `GET /api/ships`, `GET /api/ships/{id}`,
  `GET /api/ships/{id}/positions` (full position history for route playback)
- `POST /api/positions/report`, `GET /api/positions/build_message`
- `GET /api/alerts`, `POST /api/alerts/acknowledge`, `POST /api/alerts/broadcast`
- `POST /api/detect/spoofing|route|zone|speed`
- `GET/POST /api/zones`, `DELETE /api/zones/{id}`
- `POST /api/weather/report`, `GET /api/weather/latest|history`
- `GET /api/blockchain/blocks|stats|verify`, `POST /api/blockchain/tamper_demo`
- `POST /api/crypto/sign|verify|ecdh|aes|hash|keypair`
- `GET /api/auth/control_center_public_key`, `POST /api/auth/authenticate`,
  `GET /api/auth/log`, `POST /api/auth/rotate_keys`
- `POST /api/comms/send`, `GET /api/comms`
- `GET /api/dashboard/summary`
- `WS /ws/live` - live event feed (new positions, alerts, ships, blocks)

### Authentication & key rotation

`/api/auth/authenticate` is a real mutual-authentication exchange: the
caller supplies a challenge string, the server signs it with the ship's
actual ECDSA private key, verifies that signature with the ship's actual
public key, and separately derives a real ECDH session key between the
ship and a persistent Control Center identity (generated once, stored in
`control_center_identity`, reused after that - a fresh key pair every call
would make the "two sides derive the same secret" property meaningless).
Every attempt is persisted to `auth_events`, success or failure.

`/api/auth/rotate_keys` genuinely regenerates a ship's ECC key pair,
replaces the stored public/private keys, and logs a `KEY_ROTATION` block
to the blockchain referencing a fingerprint of the old key.

### Comms

`/api/comms/send` encrypts a message with AES-256-CBC using a session key
derived via real ECDH between the Control Center identity and the target
ship's registered public key - the same primitives as the crypto sandbox,
wired into a persisted conversation log (`messages` table). The plaintext
is also stored alongside the ciphertext purely so the comms log can render
without re-deriving the key on every read; a production deployment would
not retain plaintext server-side (the same documented exception already
made for ship private keys elsewhere in this codebase).
