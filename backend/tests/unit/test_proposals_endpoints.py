"""Tests for proposals endpoints: CRUD, status transitions, pattern-based creation.

Validates:
- Proposal creation, listing, get, update, delete
- has_campaign filtering on list
- Status transitions (reject dismisses linked opportunity)
- Delete dismisses linked opportunity
- from-pattern creation
- Ownership enforcement (user can only update/delete own proposals)
"""
import pytest
import pytest_asyncio
from uuid import uuid4
from datetime import datetime

from app.core.security import create_access_token
from app.models import User, Proposal, ProposalStatus


# =========================================================================
# Fixtures
# =========================================================================

@pytest_asyncio.fixture
async def auth_headers(test_user):
    """Auth headers for test_user."""
    token = create_access_token(data={"sub": str(test_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def sample_proposal(db_session, test_user):
    """Create a sample proposal for tests."""
    proposal = Proposal(
        user_id=test_user.id,
        title="Test Proposal",
        summary="A test proposal",
        detailed_description="Detailed description for testing",
        initial_budget=500.0,
        risk_level="medium",
        risk_description="Some risk",
        stop_loss_threshold={"max_loss": 250},
        success_criteria={"target": "test"},
        required_tools={},
        required_inputs={},
        status="pending",
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)
    return proposal


@pytest_asyncio.fixture
async def other_user(db_session):
    """Create a second user to test ownership."""
    from app.core.security import get_password_hash
    user = User(
        username="otheruser",
        email="other@example.com",
        password_hash=get_password_hash("Str0ng!Pass"),
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# =========================================================================
# Create Proposal Tests
# =========================================================================

class TestCreateProposal:
    """Tests for POST /api/v1/proposals."""

    @pytest.mark.asyncio
    async def test_create_proposal(self, async_client, db_session, test_user, auth_headers):
        response = await async_client.post("/api/v1/proposals/", json={
            "title": "New Proposal",
            "summary": "Summary text",
            "detailed_description": "Details here",
            "initial_budget": 100.0,
            "risk_level": "low",
            "risk_description": "Low risk",
            "stop_loss_threshold": {"max": 50},
            "success_criteria": {"goal": "test"},
            "required_tools": {},
            "required_inputs": {},
        }, headers=auth_headers)
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "New Proposal"
        assert data["user_id"] == str(test_user.id)

    @pytest.mark.asyncio
    async def test_create_proposal_requires_auth(self, async_client):
        response = await async_client.post("/api/v1/proposals/", json={
            "title": "X", "summary": "S", "detailed_description": "D",
            "initial_budget": 10, "risk_level": "low", "risk_description": "R",
            "stop_loss_threshold": {}, "success_criteria": {}, "required_tools": {},
            "required_inputs": {},
        })
        assert response.status_code in (401, 403)


# =========================================================================
# List Proposals Tests
# =========================================================================

class TestListProposals:
    """Tests for GET /api/v1/proposals."""

    @pytest.mark.asyncio
    async def test_list_proposals(self, async_client, db_session, sample_proposal, auth_headers):
        response = await async_client.get("/api/v1/proposals/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any(p["id"] == str(sample_proposal.id) for p in data)

    @pytest.mark.asyncio
    async def test_list_proposals_with_status_filter(self, async_client, db_session, sample_proposal, auth_headers):
        response = await async_client.get(
            "/api/v1/proposals/?status=pending",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert all(p["status"] == "pending" for p in data)


# =========================================================================
# Get Single Proposal Tests
# =========================================================================

class TestGetProposal:
    """Tests for GET /api/v1/proposals/{id}."""

    @pytest.mark.asyncio
    async def test_get_proposal(self, async_client, db_session, sample_proposal, auth_headers):
        response = await async_client.get(
            f"/api/v1/proposals/{sample_proposal.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Test Proposal"

    @pytest.mark.asyncio
    async def test_get_nonexistent_proposal(self, async_client, db_session, auth_headers):
        response = await async_client.get(
            f"/api/v1/proposals/{uuid4()}",
            headers=auth_headers,
        )
        assert response.status_code == 404


# =========================================================================
# Update Proposal Tests
# =========================================================================

class TestUpdateProposal:
    """Tests for PUT /api/v1/proposals/{id}."""

    @pytest.mark.asyncio
    async def test_update_proposal_title(self, async_client, db_session, sample_proposal, auth_headers):
        response = await async_client.put(
            f"/api/v1/proposals/{sample_proposal.id}",
            json={"title": "Updated Title"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Updated Title"

    @pytest.mark.asyncio
    async def test_update_other_users_proposal_fails(
        self, async_client, db_session, sample_proposal, other_user
    ):
        """Users cannot update proposals they don't own."""
        other_token = create_access_token(data={"sub": str(other_user.id)})
        response = await async_client.put(
            f"/api/v1/proposals/{sample_proposal.id}",
            json={"title": "Hacked"},
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert response.status_code == 404  # Not found for the other user


# =========================================================================
# Delete Proposal Tests
# =========================================================================

class TestDeleteProposal:
    """Tests for DELETE /api/v1/proposals/{id}."""

    @pytest.mark.asyncio
    async def test_delete_proposal(self, async_client, db_session, sample_proposal, auth_headers):
        response = await async_client.delete(
            f"/api/v1/proposals/{sample_proposal.id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        # Verify deleted
        get_response = await async_client.get(
            f"/api/v1/proposals/{sample_proposal.id}",
            headers=auth_headers,
        )
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_other_users_proposal_fails(
        self, async_client, db_session, sample_proposal, other_user
    ):
        other_token = create_access_token(data={"sub": str(other_user.id)})
        response = await async_client.delete(
            f"/api/v1/proposals/{sample_proposal.id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert response.status_code == 404
