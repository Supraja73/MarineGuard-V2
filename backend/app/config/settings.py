"""
settings.py
-----------
Centralized configuration loaded from environment variables (and a local
.env file during development). Every other module that needs a config
value reads it from here via get_settings() instead of calling
os.environ directly, so there is exactly one place that defines what
configuration the app accepts and what its defaults are.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MarineGuard Secure Maritime Platform"
    environment: str = "development"
    debug: bool = True

    host: str = "0.0.0.0"
    port: int = 8000

    cors_allow_origins: str = "*"

    # Async SQLAlchemy connection URL. Must use the asyncpg driver
    # (postgresql+asyncpg://...) since the app runs an async engine.
    # Alembic (see migrations/env.py) reads the same setting but swaps in
    # the sync psycopg driver for migrations, which run outside the
    # request/response async context.
    database_url: str = "postgresql+asyncpg://marineguard:marineguard@localhost:5432/marineguard"

    # Echoes every SQL statement to the log when true - useful for
    # debugging query behavior during development, noisy in production.
    sql_echo: bool = False

    log_level: str = "INFO"

    # This deployment simulates ship movement rather than ingesting live
    # AIS feeds. GPS-spoofing detection (comparing implied speed between
    # consecutive fixes) is only meaningful against real, physically-
    # constrained GPS hardware; simulated positions can legitimately
    # "jump" whenever the operator fast-forwards or replans a voyage, so
    # spoofing checks are skipped entirely while this is true. Flip to
    # False only when this deployment is wired to a real AIS/GPS feed.
    simulation_mode: bool = True

    hijack_stall_threshold_ms: int = 600000

    # How far outside a cyclone/storm's stated radius a route or port must
    # stay to be considered "clear" of it, for the automatic reroute /
    # safe-port workflow. Configurable so ops can tighten or loosen it
    # without a code change.
    cyclone_safety_buffer_km: float = 75.0

    # How often (seconds) the background cyclone monitor re-checks every
    # in-progress voyage for a newly-blocked route, and every ship
    # holding at a safe port for a newly-clear route home.
    # How often the automatic cyclone-monitor background loop re-checks
    # every in-progress voyage against active cyclones (see
    # app.services.cyclone_monitor). Kept in the same few-second range as
    # the frontend's position-report cadence (VOYAGE_TICK_SECONDS=4 in
    # main.js) so a voyage that becomes unsafe gets a pending reroute
    # alert within a couple of ticks, not up to a full minute later.
    cyclone_monitor_interval_seconds: int = 5

    # Anyone hitting /api/users/register with role=control_station would
    # otherwise be able to self-elevate to full fleet authority. A shared
    # signup code (set via env, not committed) is a low-effort gate
    # appropriate for a demo deployment - NOT a substitute for real
    # admin-invitation flow in an actual production system.
    control_station_signup_code: str = "marineguard-ops"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_allow_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    @property
    def sync_database_url(self) -> str:
        """The same database, addressed with a sync driver (psycopg) for
        Alembic migrations, which SQLAlchemy/Alembic run synchronously.
        """
        return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
