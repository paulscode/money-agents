from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker, AsyncEngine
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator, Optional
import asyncio
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

# Store engine per event loop to handle Celery's multiple loops
_engines: dict = {}
_session_makers: dict = {}
_lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None


def _get_loop_id() -> int:
    """Get a unique identifier for the current event loop."""
    try:
        loop = asyncio.get_running_loop()
        return id(loop)
    except RuntimeError:
        # No running loop
        return 0


def get_engine() -> AsyncEngine:
    """Get or create the async engine for the current event loop."""
    loop_id = _get_loop_id()
    if loop_id not in _engines:
        # GAP-21: Apply SSL mode when configured
        connect_args = {}
        if settings.db_ssl_mode:
            connect_args["ssl"] = settings.db_ssl_mode
        # Use moderate pool sizes - each event loop (FastAPI, Celery workers) gets its own pool
        _engines[loop_id] = create_async_engine(
            settings.database_url,
            echo=settings.database_echo,
            future=True,
            pool_pre_ping=True,
            pool_size=5,        # Base pool size per event loop
            max_overflow=10,    # Allow overflow for bursts
            pool_recycle=300,   # Recycle connections after 5 minutes
            pool_timeout=30,    # Wait max 30 seconds for a connection
            connect_args=connect_args,
        )
        logger.debug(f"Created new engine for loop {loop_id}")
    return _engines[loop_id]


def get_session_maker() -> async_sessionmaker:
    """Get or create the async session maker for the current event loop."""
    loop_id = _get_loop_id()
    if loop_id not in _session_makers:
        _session_makers[loop_id] = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _session_makers[loop_id]


def async_session_factory() -> AsyncSession:
    """Create a new async session. Alias for get_session_maker()().
    
    Usage: async with async_session_factory() as session: ...
    """
    return get_session_maker()()


class Base(DeclarativeBase):
    """Base class for all database models."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency to get database session."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager to get database session (for use outside FastAPI deps)."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database (create tables)."""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
