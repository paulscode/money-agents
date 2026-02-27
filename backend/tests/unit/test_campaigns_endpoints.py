"""Tests for campaigns endpoints: CRUD, ownership, status transitions.

Validates:
- Campaign creation from approved proposals
- Campaign listing with status filter
- Get, update, delete campaigns
- Ownership enforcement (user can only see/modify own campaigns)
- Status validation (only approved proposals can create campaigns)
"""
import pytest
import pytest_asyncio
from uuid import uuid4

from app.core.security import create_access_token
from app.models import User, Proposal, Campaign, ProposalStatus


# =========================================================================
# Fixtures
# =========================================================================

@pytest_asyncio.fixture
async def auth_headers(test_user):
    token = create_access_token(data={"sub": str(test_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def approved_proposal(db_session, test_user):
    """Create an approved proposal for campaign creation."""
    proposal = Proposal(
        user_id=test_user.id,
        title="Approved Proposal",
        summary="Ready for campaign",
        detailed_description="An approved proposal",
        initial_budget=1000.0,
        risk_level="medium",
        risk_description="Moderate",
        stop_loss_threshold={"max_loss": 500},
        success_criteria={"target": "revenue"},
        required_tools={},
        required_inputs={},
        status=ProposalStatus.APPROVED,
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)
    return proposal


@pytest_asyncio.fixture
async def draft_proposal(db_session, test_user):
    """Create a draft (not approved) proposal."""
    proposal = Proposal(
        user_id=test_user.id,
        title="Draft Proposal",
        summary="Still a draft",
        detailed_description="Not yet approved",
        initial_budget=100.0,
        risk_level="low",
        risk_description="Low",
        stop_loss_threshold={},
        success_criteria={},
        required_tools={},
        required_inputs={},
        status=ProposalStatus.PENDING,
    )
    db_session.add(proposal)
    await db_session.commit()
    await db_session.refresh(proposal)
    return proposal


@pytest_asyncio.fixture
async def sample_campaign(db_session, test_user, approved_proposal):
    """Create a sample campaign for tests."""
    campaign = Campaign(
        user_id=test_user.id,
        proposal_id=approved_proposal.id,
        budget_allocated=1000.0,
        success_metrics={"target": "revenue"},
        requirements_checklist=["step1", "step2"],
    )
    db_session.add(campaign)
    await db_session.commit()
    await db_session.refresh(campaign)
    return campaign


@pytest_asyncio.fixture
async def other_user(db_session):
    from app.core.security import get_password_hash
    user = User(
        username="campaignother",
        email="campaignother@example.com",
        password_hash=get_password_hash("Str0ng!Pass"),
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# =========================================================================
# Create Campaign Tests
# =========================================================================

class TestCreateCampaign:
    """Tests for POST /api/v1/campaigns."""

    @pytest.mark.asyncio
    async def test_create_campaign_from_approved_proposal(
        self, async_client, db_session, approved_proposal, auth_headers
    ):
        response = await async_client.post("/api/v1/campaigns/", json={
            "proposal_id": str(approved_proposal.id),
            "budget_allocated": 500.0,
            "success_metrics": {"goal": "test"},
            "requirements_checklist": ["item1"],
        }, headers=auth_headers)
        assert response.status_code == 201
        data = response.json()
        assert data["proposal_id"] == str(approved_proposal.id)
        assert data["budget_allocated"] == 500.0

    @pytest.mark.asyncio
    async def test_create_campaign_from_draft_proposal_fails(
        self, async_client, db_session, draft_proposal, auth_headers
    ):
        """Can only create campaigns from approved proposals."""
        response = await async_client.post("/api/v1/campaigns/", json={
            "proposal_id": str(draft_proposal.id),
            "budget_allocated": 100.0,
            "success_metrics": {},
            "requirements_checklist": [],
        }, headers=auth_headers)
        assert response.status_code == 400
        assert "approved" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_create_campaign_nonexistent_proposal_fails(
        self, async_client, db_session, auth_headers
    ):
        response = await async_client.post("/api/v1/campaigns/", json={
            "proposal_id": str(uuid4()),
            "budget_allocated": 100.0,
            "success_metrics": {},
            "requirements_checklist": [],
        }, headers=auth_headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_create_campaign_requires_auth(self, async_client, db_session, approved_proposal):
        response = await async_client.post("/api/v1/campaigns/", json={
            "proposal_id": str(approved_proposal.id),
            "budget_allocated": 100.0,
            "success_metrics": {},
            "requirements_checklist": [],
        })
        assert response.status_code in (401, 403)


# =========================================================================
# List Campaigns Tests
# =========================================================================

class TestListCampaigns:
    """Tests for GET /api/v1/campaigns."""

    @pytest.mark.asyncio
    async def test_list_campaigns(
        self, async_client, db_session, sample_campaign, auth_headers
    ):
        response = await async_client.get("/api/v1/campaigns/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    @pytest.mark.asyncio
    async def test_list_campaigns_only_own(
        self, async_client, db_session, sample_campaign, other_user
    ):
        """Users should only see their own campaigns."""
        other_token = create_access_token(data={"sub": str(other_user.id)})
        response = await async_client.get(
            "/api/v1/campaigns/",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0  # Other user has no campaigns


# =========================================================================
# Get Campaign Tests
# =========================================================================

class TestGetCampaign:
    """Tests for GET /api/v1/campaigns/{id}."""

    @pytest.mark.asyncio
    async def test_get_campaign(
        self, async_client, db_session, sample_campaign, auth_headers
    ):
        response = await async_client.get(
            f"/api/v1/campaigns/{sample_campaign.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["budget_allocated"] == 1000.0

    @pytest.mark.asyncio
    async def test_get_campaign_not_found(self, async_client, db_session, auth_headers):
        response = await async_client.get(
            f"/api/v1/campaigns/{uuid4()}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_other_users_campaign_not_found(
        self, async_client, db_session, sample_campaign, other_user
    ):
        other_token = create_access_token(data={"sub": str(other_user.id)})
        response = await async_client.get(
            f"/api/v1/campaigns/{sample_campaign.id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert response.status_code == 404


# =========================================================================
# Update Campaign Tests
# =========================================================================

class TestUpdateCampaign:
    """Tests for PUT /api/v1/campaigns/{id}."""

    @pytest.mark.asyncio
    async def test_update_campaign(
        self, async_client, db_session, sample_campaign, auth_headers
    ):
        response = await async_client.put(
            f"/api/v1/campaigns/{sample_campaign.id}",
            json={"current_phase": "executing"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["current_phase"] == "executing"

    @pytest.mark.asyncio
    async def test_update_other_users_campaign_fails(
        self, async_client, db_session, sample_campaign, other_user
    ):
        other_token = create_access_token(data={"sub": str(other_user.id)})
        response = await async_client.put(
            f"/api/v1/campaigns/{sample_campaign.id}",
            json={"current_phase": "hacked"},
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert response.status_code == 404


# =========================================================================
# Delete Campaign Tests
# =========================================================================

class TestDeleteCampaign:
    """Tests for DELETE /api/v1/campaigns/{id}."""

    @pytest.mark.asyncio
    async def test_delete_campaign(
        self, async_client, db_session, sample_campaign, auth_headers
    ):
        response = await async_client.delete(
            f"/api/v1/campaigns/{sample_campaign.id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_other_users_campaign_fails(
        self, async_client, db_session, sample_campaign, other_user
    ):
        other_token = create_access_token(data={"sub": str(other_user.id)})
        response = await async_client.delete(
            f"/api/v1/campaigns/{sample_campaign.id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert response.status_code == 404
