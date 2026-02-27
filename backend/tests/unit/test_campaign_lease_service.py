"""Tests for Campaign Lease Service."""
import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import campaign_lease_service
from app.services.campaign_lease_service import (
    acquire_lease,
    renew_lease,
    release_lease,
    get_claimable_campaigns,
    get_expired_leases,
    force_release_expired_leases,
    get_worker_campaigns,
    count_worker_campaigns,
    transfer_lease,
    LeaseNotAvailableError,
    LeaseNotHeldError,
    CampaignNotFoundError,
    LEASE_TTL_SECONDS,
    LEASE_GRACE_PERIOD_SECONDS,
)
from app.models import Campaign, CampaignStatus


class TestAcquireLease:
    """Tests for acquire_lease function."""
    
    @pytest.mark.asyncio
    async def test_acquire_lease_success(self):
        """Test successful lease acquisition on claimable campaign."""
        db = AsyncMock()
        worker_id = "local-testhost"
        campaign_id = uuid4()
        
        # Mock campaign that is claimable
        mock_campaign = MagicMock(spec=Campaign)
        mock_campaign.id = campaign_id
        mock_campaign.status = CampaignStatus.INITIALIZING
        mock_campaign.leased_by = None
        mock_campaign.lease_expires_at = None
        mock_campaign.is_claimable.return_value = True
        mock_campaign.is_leased.return_value = False
        
        # Mock query result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_campaign
        db.execute.return_value = mock_result
        
        result = await acquire_lease(db, worker_id, campaign_id)
        
        assert result == mock_campaign
        assert mock_campaign.leased_by == worker_id
        assert mock_campaign.lease_acquired_at is not None
        assert mock_campaign.lease_expires_at is not None
        assert mock_campaign.lease_heartbeat_at is not None
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(mock_campaign)
    
    @pytest.mark.asyncio
    async def test_acquire_lease_campaign_not_found(self):
        """Test lease acquisition fails when campaign doesn't exist."""
        db = AsyncMock()
        worker_id = "local-testhost"
        campaign_id = uuid4()
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result
        
        with pytest.raises(CampaignNotFoundError):
            await acquire_lease(db, worker_id, campaign_id)
    
    @pytest.mark.asyncio
    async def test_acquire_lease_already_leased(self):
        """Test lease acquisition fails when campaign is already leased."""
        db = AsyncMock()
        worker_id = "local-testhost"
        other_worker = "remote-other"
        campaign_id = uuid4()
        
        mock_campaign = MagicMock(spec=Campaign)
        mock_campaign.id = campaign_id
        mock_campaign.status = CampaignStatus.EXECUTING
        mock_campaign.leased_by = other_worker
        mock_campaign.lease_expires_at = datetime.utcnow() + timedelta(minutes=5)
        mock_campaign.is_claimable.return_value = False
        mock_campaign.is_leased.return_value = True
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_campaign
        db.execute.return_value = mock_result
        
        with pytest.raises(LeaseNotAvailableError):
            await acquire_lease(db, worker_id, campaign_id)
    
    @pytest.mark.asyncio
    async def test_acquire_lease_renews_own_lease(self):
        """Test that acquiring a lease you already hold renews it."""
        db = AsyncMock()
        worker_id = "local-testhost"
        campaign_id = uuid4()
        
        old_expires = datetime.now(timezone.utc) + timedelta(minutes=1)
        
        mock_campaign = MagicMock(spec=Campaign)
        mock_campaign.id = campaign_id
        mock_campaign.status = CampaignStatus.EXECUTING
        mock_campaign.leased_by = worker_id  # Same worker
        mock_campaign.lease_expires_at = old_expires
        mock_campaign.is_claimable.return_value = False  # Not claimable because leased
        mock_campaign.is_leased.return_value = True
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_campaign
        db.execute.return_value = mock_result
        
        result = await acquire_lease(db, worker_id, campaign_id)
        
        # Should renew the lease, not reject
        assert result == mock_campaign
        assert mock_campaign.lease_expires_at > old_expires
        db.commit.assert_called_once()


class TestRenewLease:
    """Tests for renew_lease function."""
    
    @pytest.mark.asyncio
    async def test_renew_lease_success(self):
        """Test successful lease renewal."""
        db = AsyncMock()
        worker_id = "local-testhost"
        campaign_id = uuid4()
        
        mock_campaign = MagicMock(spec=Campaign)
        mock_campaign.id = campaign_id
        mock_campaign.leased_by = worker_id
        mock_campaign.lease_expires_at = datetime.utcnow() + timedelta(minutes=1)
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_campaign
        db.execute.return_value = mock_result
        
        result = await renew_lease(db, worker_id, campaign_id)
        
        assert result == mock_campaign
        assert mock_campaign.lease_heartbeat_at is not None
        db.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_renew_lease_not_held(self):
        """Test renewal fails when worker doesn't hold the lease."""
        db = AsyncMock()
        worker_id = "local-testhost"
        other_worker = "remote-other"
        campaign_id = uuid4()
        
        mock_campaign = MagicMock(spec=Campaign)
        mock_campaign.id = campaign_id
        mock_campaign.leased_by = other_worker  # Different worker
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_campaign
        db.execute.return_value = mock_result
        
        with pytest.raises(LeaseNotHeldError):
            await renew_lease(db, worker_id, campaign_id)


class TestReleaseLease:
    """Tests for release_lease function."""
    
    @pytest.mark.asyncio
    async def test_release_lease_success(self):
        """Test successful lease release."""
        db = AsyncMock()
        worker_id = "local-testhost"
        campaign_id = uuid4()
        
        mock_campaign = MagicMock(spec=Campaign)
        mock_campaign.id = campaign_id
        mock_campaign.leased_by = worker_id
        mock_campaign.status = CampaignStatus.EXECUTING
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_campaign
        db.execute.return_value = mock_result
        
        result = await release_lease(db, worker_id, campaign_id, reason="completed")
        
        assert result == mock_campaign
        assert mock_campaign.leased_by is None
        assert mock_campaign.lease_acquired_at is None
        assert mock_campaign.lease_expires_at is None
        assert mock_campaign.lease_heartbeat_at is None
        db.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_release_lease_with_status_change(self):
        """Test release lease with status update."""
        db = AsyncMock()
        worker_id = "local-testhost"
        campaign_id = uuid4()
        
        mock_campaign = MagicMock(spec=Campaign)
        mock_campaign.id = campaign_id
        mock_campaign.leased_by = worker_id
        mock_campaign.status = CampaignStatus.EXECUTING
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_campaign
        db.execute.return_value = mock_result
        
        result = await release_lease(
            db, worker_id, campaign_id,
            reason="paused",
            new_status=CampaignStatus.PAUSED
        )
        
        assert mock_campaign.status == CampaignStatus.PAUSED


class TestGetClaimableCampaigns:
    """Tests for get_claimable_campaigns function."""
    
    @pytest.mark.asyncio
    async def test_get_claimable_campaigns_returns_correct_campaigns(self):
        """Test that only claimable campaigns are returned."""
        db = AsyncMock()
        
        mock_campaigns = [
            MagicMock(spec=Campaign, status=CampaignStatus.INITIALIZING),
            MagicMock(spec=Campaign, status=CampaignStatus.EXECUTING),
        ]
        
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = mock_campaigns
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        
        result = await get_claimable_campaigns(db, limit=10)
        
        assert len(result) == 2
        db.execute.assert_called_once()


class TestForceReleaseExpiredLeases:
    """Tests for force_release_expired_leases function."""
    
    @pytest.mark.asyncio
    async def test_force_release_expired_leases(self):
        """Test force releasing expired leases."""
        db = AsyncMock()
        
        # Mock expired campaigns
        mock_campaign = MagicMock(spec=Campaign)
        mock_campaign.id = uuid4()
        mock_campaign.leased_by = "dead-worker"
        mock_campaign.status = CampaignStatus.EXECUTING
        mock_campaign.lease_expires_at = datetime.utcnow() - timedelta(hours=1)
        
        # Mock get_expired_leases to return our campaign
        with patch.object(
            campaign_lease_service,
            'get_expired_leases',
            return_value=[mock_campaign]
        ):
            count = await force_release_expired_leases(db, set_failover_status=True)
        
        assert count == 1
        assert mock_campaign.leased_by is None
        assert mock_campaign.status == CampaignStatus.PAUSED_FAILOVER
        db.commit.assert_called_once()


class TestTransferLease:
    """Tests for transfer_lease function."""
    
    @pytest.mark.asyncio
    async def test_transfer_lease_success(self):
        """Test successful lease transfer."""
        db = AsyncMock()
        from_worker = "worker-a"
        to_worker = "worker-b"
        campaign_id = uuid4()
        
        mock_campaign = MagicMock(spec=Campaign)
        mock_campaign.id = campaign_id
        mock_campaign.leased_by = from_worker
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_campaign
        db.execute.return_value = mock_result
        
        result = await transfer_lease(db, campaign_id, from_worker, to_worker)
        
        assert result == mock_campaign
        assert mock_campaign.leased_by == to_worker
        db.commit.assert_called_once()
