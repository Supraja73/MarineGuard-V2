"""
cyclone_monitor.py
---------------------
Background loop that is the actual "automatic" half of the reroute
workflow (requirements 1, 5, 6, 7): nobody has to click a button for the
system to notice a voyage has become unsafe, or that a ship holding at a
safe port can resume. Started once from app.main's startup event and
cancelled on shutdown.

Every tick:
  - Loads active cyclones once (skips everything if there are none).
  - For every in_progress voyage whose ship ISN'T already holding: cheap
    straight-line check (route_is_blocked) against the origin->dest
    corridor; if blocked, hands off to the same propose_reroute_for_voyage
    used by the manual endpoint.
  - For every in_progress voyage whose ship IS holding at a safe port:
    hands off to propose_resume_for_voyage, which itself no-ops unless
    the route home is actually clear again.

A voyage that already has a pending (unapproved) request is skipped by
propose_reroute_for_voyage/propose_resume_for_voyage themselves, so this
loop never piles up duplicate proposals while Control Station hasn't
acted yet.
"""

import asyncio

from sqlalchemy import select

from app.core.logging_config import get_logger
from app.db.models import Ship, Voyage
from app.db.session import session_scope
from app.services.cyclone_reroute_service import route_is_blocked
from app.services.reroute_orchestrator import get_active_cyclones, propose_reroute_for_voyage, propose_resume_for_voyage

logger = get_logger("app.services.cyclone_monitor")


async def _run_one_pass(registry) -> None:
    async with session_scope() as session:
        cyclones = await get_active_cyclones(session)
        if not cyclones:
            return

        result = await session.execute(select(Voyage).where(Voyage.status == "in_progress"))
        voyages = result.scalars().all()

        for voyage in voyages:
            ship = await session.get(Ship, voyage.ship_id)
            if not ship:
                continue

            try:
                if ship.status == "holding":
                    await propose_resume_for_voyage(session, registry, voyage, ship, cyclones=cyclones, source="auto-monitor")
                    continue

                origin_lat = voyage.current_lat if voyage.current_lat is not None else voyage.origin_lat
                origin_lon = voyage.current_lon if voyage.current_lon is not None else voyage.origin_lon
                blocking = route_is_blocked(origin_lat, origin_lon, voyage.dest_lat, voyage.dest_lon, cyclones)
                if blocking is not None:
                    await propose_reroute_for_voyage(session, registry, voyage, ship, cyclones=cyclones, source="auto-monitor")
            except ValueError:
                # No alternate route AND no safe port available at all
                # (every port in range is also inside a cyclone). Logged,
                # not raised - the monitor tries again next tick as the
                # storm moves. The ship's existing cyclone_route_alert
                # already tells Control Station to hold position.
                logger.warning(
                    "Voyage %s (ship %s) is blocked with no route or safe port currently available.",
                    voyage.voyage_id, voyage.ship_id,
                )
            except Exception:
                logger.exception("Cyclone monitor failed evaluating voyage %s", voyage.voyage_id)


async def cyclone_monitor_loop(app) -> None:
    from app.config.settings import get_settings
    interval = get_settings().cyclone_monitor_interval_seconds
    logger.info("Cyclone monitor started (interval=%ss)", interval)
    while True:
        try:
            await _run_one_pass(app.state.connection_registry)
        except Exception:
            logger.exception("Cyclone monitor pass failed")
        await asyncio.sleep(interval)
