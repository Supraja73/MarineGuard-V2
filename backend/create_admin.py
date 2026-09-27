"""
create_admin.py
----------------
Run this once to guarantee a working Control Station login, no matter
what state the database is in (empty, already has accounts, whatever).

- If a Control Station account with this username already exists, its
  password is reset to the one below.
- If not, a brand new one is created.

Usage (from the backend/ folder, with your virtualenv active):
    python create_admin.py

Then sign in with:
    Username: admin
    Password: MarineGuard@2026
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config.settings import get_settings
from app.db.models import User
from app.services import user_auth_service as auth_svc

USERNAME = "admin"
PASSWORD = "MarineGuard@2026"


async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        result = await session.execute(select(User).where(User.username == USERNAME))
        user = result.scalar_one_or_none()

        if user:
            user.password_hash = auth_svc.hash_password(PASSWORD)
            user.role = "control_station"
            user.status = "approved"
            user.active = True
            print(f"Existing user '{USERNAME}' found - password reset and re-activated as Control Station.")
        else:
            user = User(
                username=USERNAME,
                password_hash=auth_svc.hash_password(PASSWORD),
                role="control_station",
                status="approved",
                active=True,
                created_at=datetime.now(timezone.utc),
            )
            session.add(user)
            print(f"Created new Control Station account '{USERNAME}'.")

        await session.commit()

    await engine.dispose()
    print(f"\nSign in with:\n  Username: {USERNAME}\n  Password: {PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
