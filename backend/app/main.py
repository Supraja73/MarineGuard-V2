"""
main.py
-------
Application factory and entry point for the maritime security platform
backend.

Run with:
    uvicorn app.main:app --reload --port 8000

No demo/seed data is created anywhere in this codebase. The database
starts empty; ships, voyages, positions, alerts, and blockchain blocks
only exist once a real action is taken through the API (or the frontend
that calls it).
"""

import os
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config.settings import get_settings
from app.core.logging_config import configure_logging, get_logger
from app.core.dependencies import ConnectionRegistry
from app.db.session import check_connection
from app.middleware.request_logger import RequestLoggingMiddleware
from app.middleware.error_handler import register_exception_handlers

from app.routers import (
    ships, positions, alerts, detect, zones, weather, blockchain, crypto, dashboard, websocket,
    auth, comms, voyages, cyclones, ocean_route, reroutes, users, mode, incidents,
)

settings = get_settings()
configure_logging()
logger = get_logger("app.main")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
    )

    # Shared state: one connection registry for the live websocket feed,
    # reachable from any router via request.app.state.connection_registry.
    app.state.connection_registry = ConnectionRegistry()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    register_exception_handlers(app)

    app.include_router(ships.router)
    app.include_router(positions.router)
    app.include_router(alerts.router)
    app.include_router(detect.router)
    app.include_router(zones.router)
    app.include_router(weather.router)
    app.include_router(blockchain.router)
    app.include_router(crypto.router)
    app.include_router(dashboard.router)
    app.include_router(websocket.router)
    app.include_router(auth.router)
    app.include_router(comms.router)
    app.include_router(voyages.router)
    app.include_router(cyclones.router)
    app.include_router(ocean_route.router)
    app.include_router(reroutes.router)
    app.include_router(users.router)
    app.include_router(mode.router)
    app.include_router(incidents.router)

    @app.on_event("startup")
    async def on_startup():
        # Verifies the configured Postgres database is actually reachable
        # before accepting traffic, rather than letting the first request
        # hit an opaque connection error. Table creation is handled by
        # Alembic migrations (see migrations/), not by application startup -
        # run `alembic upgrade head` before starting the server.
        try:
            await check_connection()
        except Exception as exc:
            logger.error(
                "Could not connect to the database at startup. Is PostgreSQL "
                "running and DATABASE_URL configured correctly? Have migrations "
                "been applied with 'alembic upgrade head'? Underlying error: %s",
                exc,
            )
            raise
        logger.info("%s starting up (env=%s)", settings.app_name, settings.environment)

        # Wire the ledger's PoET round broadcaster to the shared websocket
        # registry, so every block append's live "PoET Validator Wait
        # Time" round reaches connected dashboard clients immediately
        # (see app.blockchain.ledger.set_broadcaster).
        from app.blockchain import ledger
        ledger.set_broadcaster(app.state.connection_registry.broadcast)

        from app.services.cyclone_monitor import cyclone_monitor_loop
        app.state.cyclone_monitor_task = asyncio.create_task(cyclone_monitor_loop(app))

        # Pre-load PyTorch SAR Iceberg Classifier model into RAM
        from app.services.ml_classifier_service import init_sar_classifier
        init_sar_classifier()


    @app.on_event("shutdown")
    async def on_shutdown():
        task = getattr(app.state, "cyclone_monitor_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
    frontend_dir = os.path.abspath(frontend_dir)

    if os.path.isdir(frontend_dir):
        app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

        @app.get("/")
        async def serve_index():
            return FileResponse(os.path.join(frontend_dir, "html", "index.html"))
    else:
        @app.get("/")
        async def root():
            return {"message": settings.app_name, "status": "running"}

    return app


app = create_app()
