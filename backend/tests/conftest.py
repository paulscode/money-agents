"""Pytest configuration and fixtures for Money Agents tests."""
import asyncio
import json
import os
from pathlib import Path
from typing import AsyncGenerator, Generator
from uuid import uuid4, UUID

# Compatibility shim: passlib 1.7.4 accesses bcrypt.__about__.__version__
# which was removed in bcrypt 4.x.  This must run before any passlib import.
import bcrypt as _bcrypt_mod
if not hasattr(_bcrypt_mod, "__about__"):
    _bcrypt_mod.__about__ = type("_about", (), {"__version__": _bcrypt_mod.__version__})()

import pytest
import pytest_asyncio
from sqlalchemy import event, TypeDecorator, CHAR, JSON, Text, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB, ARRAY
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models import Base

# Import additional models so their tables are created in the test DB
from app.models.boltz_swap import BoltzSwap  # noqa: F401


# ---------------------------------------------------------------------------
# Exclude tests/manual/ from pytest autodiscovery.
# Manual tests are standalone scripts (run via python -m) that accept plain
# function arguments (db, user, token) rather than pytest fixtures.
# ---------------------------------------------------------------------------
collect_ignore = [str(Path(__file__).parent / "manual")]

# Re-export environment detection so tests can use conftest directly too
from tests.helpers.paths import IN_DOCKER, BACKEND_ROOT, PROJECT_ROOT  # noqa: F401


# ---------------------------------------------------------------------------
# Disable rate limiter globally during tests (unless a test explicitly re-enables it)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    """Disable slowapi rate limiter so tests aren't throttled."""
    from app.core.rate_limit import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


# ---------------------------------------------------------------------------
# Custom markers for execution context
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "host_only: test requires full project tree (skipped inside Docker)",
    )
    config.addinivalue_line(
        "markers",
        "docker_only: test requires Docker container environment",
    )


def pytest_collection_modifyitems(config, items):
    from tests.helpers.paths import IN_DOCKER as _in_docker

    for item in items:
        marker_names = {m.name for m in item.iter_markers()}
        if "host_only" in marker_names and _in_docker:
            item.add_marker(pytest.mark.skip(
                reason="host_only: project-level files not available inside Docker"
            ))
        if "docker_only" in marker_names and not _in_docker:
            item.add_marker(pytest.mark.skip(
                reason="docker_only: test requires Docker container environment"
            ))


# Use in-memory SQLite for unit tests, real PostgreSQL for integration tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


class SQLiteCompatibleUUID(TypeDecorator):
    """UUID type that stores as string in SQLite."""
    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if isinstance(value, UUID):
                return str(value)
            return str(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            if not isinstance(value, UUID):
                return UUID(value)
            return value
        return value


def patch_postgres_types_for_sqlite():
    """Patch SQLAlchemy models to use SQLite-compatible types."""
    # Find all UUID and JSONB columns in Base and replace with SQLite-compatible types
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if table is not None:
            for column in table.columns:
                # Replace UUID with our custom type that handles conversion
                if isinstance(column.type, PG_UUID):
                    column.type = SQLiteCompatibleUUID()
                # Replace JSONB with JSON (SQLite supports JSON)
                elif isinstance(column.type, JSONB):
                    column.type = JSON()
                # Replace ARRAY with JSON (store as JSON array in SQLite)
                elif isinstance(column.type, ARRAY):
                    column.type = JSON()


# Apply the patch before any tests run
patch_postgres_types_for_sqlite()


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def async_engine():
    """Create async engine for tests."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    
    # Enable foreign keys for SQLite
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    
    # Re-patch any models imported after the initial patch (e.g. NostrIdentity
    # imported via test_nostr_service_helpers during collection)
    patch_postgres_types_for_sqlite()

    async with engine.begin() as conn:
        # Disable foreign keys to allow dropping tables with circular dependencies
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        # Drop all first to ensure clean slate (handles duplicate index issue)
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # Re-enable foreign keys
        await conn.execute(text("PRAGMA foreign_keys=ON"))
    
    yield engine
    
    async with engine.begin() as conn:
        # Disable foreign keys before dropping to handle circular dependencies
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create async session for tests."""
    async_session_maker = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def async_client(async_engine, db_session) -> AsyncGenerator:
    """Create async HTTP client for integration tests."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.core.database import get_db
    
    # Override the database dependency to use our test session
    async def override_get_db():
        yield db_session
    
    app.dependency_overrides[get_db] = override_get_db
    
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session) -> "User":
    """Create a test user for tests that need user_id."""
    from app.models import User
    from app.core.security import get_password_hash
    
    user = User(
        username="testuser",
        email="test@example.com",
        password_hash=get_password_hash("testpassword123"),
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_admin_user(db_session) -> "User":
    """Create a test admin user for tests that need admin access."""
    from app.models import User
    from app.core.security import get_password_hash
    
    user = User(
        username="testadmin",
        email="admin@example.com",
        password_hash=get_password_hash("testpassword123"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
def sample_opportunity_data():
    """Sample opportunity data for testing."""
    return {
        "title": "Test Opportunity",
        "summary": "A test opportunity for unit testing",
        "opportunity_type": "content",
        "source_type": "web_search",
        "source_query": "test query",
        "source_urls": ["https://example.com"],
        "raw_signal": "Test signal text",
    }


@pytest.fixture
def sample_strategy_data():
    """Sample strategy data for testing."""
    return {
        "name": "Test Strategy",
        "description": "A test discovery strategy",
        "strategy_type": "search",
        "search_queries": ["test query 1", "test query 2"],
        "source_types": ["web_search"],
        "filters": {"min_revenue": 100},
        "schedule": "daily",
    }


@pytest.fixture
def mock_llm_plan_response():
    """Mock LLM response for strategic planning."""
    return {
        "strategic_summary": "Test strategic plan",
        "strategies": [
            {
                "name": "Content Monetization Search",
                "description": "Search for content monetization opportunities",
                "strategy_type": "search",
                "search_queries": [
                    "content monetization strategies 2026",
                    "newsletter revenue opportunities",
                ],
                "source_types": ["web_search"],
                "filters": {"min_revenue_potential": 500},
                "schedule": "daily",
                "expected_success_rate": 0.15,
                "rationale": "Content is evergreen and has low barrier to entry",
            }
        ],
        "experiments": [],
        "focus_areas": ["content", "automation"],
        "avoid_areas": ["high-risk investments"],
    }


@pytest.fixture
def mock_llm_filter_response():
    """Mock LLM response for filtering search results."""
    return {
        "promising": [
            {
                "result_index": 1,
                "signal": "Newsletter opportunity with proven revenue model",
                "opportunity_type": "content",
                "revenue_potential": "medium",
                "time_sensitivity": "evergreen",
                "title": "Newsletter Monetization Guide",
                "source_url": "https://example.com/newsletter",
                "raw_snippet": "Learn how to monetize your newsletter...",
            }
        ],
        "rejected_count": 9,
        "quality_notes": "Most results were generic tutorials",
    }


@pytest.fixture
def mock_llm_eval_response():
    """Mock LLM response for opportunity evaluation."""
    return {
        "detailed_analysis": "This is a solid opportunity with proven revenue potential...",
        "score_breakdown": {
            "market_validation": 0.75,
            "competition_level": 0.6,
            "time_to_revenue": 0.8,
            "tool_alignment": 0.9,
            "effort_reward_ratio": 0.7,
            "risk_level": 0.5,
        },
        "overall_score": 0.72,
        "confidence_score": 0.85,
        "estimated_effort": "moderate",
        "estimated_revenue_potential": {
            "min": 500,
            "max": 3000,
            "timeframe": "monthly",
            "recurring": True,
        },
        "required_tools": ["content-writer", "email-sender"],
        "required_skills": ["writing", "marketing"],
        "blocking_requirements": [],
        "ranking_factors": {
            "strengths": ["proven model", "low startup cost"],
            "weaknesses": ["requires consistent effort"],
            "unique_angle": "Niche focus",
        },
        "recommendation": "approve",
        "recommendation_reason": "Good effort-to-reward ratio",
    }


@pytest.fixture
def mock_serper_response():
    """Mock Serper API response."""
    return {
        "organic": [
            {
                "title": "How to Make Money with Newsletters in 2026",
                "link": "https://example.com/newsletter-money",
                "snippet": "Discover proven strategies for newsletter monetization...",
            },
            {
                "title": "AI Content Business Ideas",
                "link": "https://example.com/ai-content",
                "snippet": "Explore profitable AI-powered content businesses...",
            },
        ],
        "news": [],
    }
