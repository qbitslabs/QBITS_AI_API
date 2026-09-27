# AI Postgres access: session.
# SQLAlchemy models/session for the dedicated AI database only.
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings
from app.db.base import Base

if "sqlite" in (settings.DATABASE_URL or "").lower():
    raise RuntimeError(
        "SQLite is removed from the AI service. Set DATABASE_URL to a postgresql:// or postgresql+asyncpg:// URL."
    )

engine = create_async_engine(
    settings.async_database_url,
    echo=getattr(settings, "DEBUG", False),
    future=True,
    pool_pre_ping=True,
    connect_args={
        "ssl": "require",
        "server_settings": {"search_path": "public"},
    },
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
