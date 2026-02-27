"""Unit tests for campaign_worker.py - Lease-based campaign execution loop."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.campaign_worker import (
    CampaignWorkerLoop,
    get_worker_id,
    get_worker_instance,
    run_campaign_worker_iteration,
)
from app.models import CampaignStatus


def utc_now():
    return datetime.now(timezone.utc)


class TestCampaignWorkerLoop:
    """Tests for CampaignWorkerLoop class."""
    
    def test_init_default_worker_id(self):
        """Worker should use hostname as default ID."""
        with patch('app.services.campaign_worker.socket.gethostname', return_value='test-host'):
            worker = CampaignWorkerLoop()
            assert worker.worker_id == 'test-host'
            assert worker.max_campaigns == 3
    
    def test_init_custom_worker_id(self):
        """Worker should accept custom ID."""
        worker = CampaignWorkerLoop(worker_id='custom-worker', max_campaigns=5)
        assert worker.worker_id == 'custom-worker'
        assert worker.max_campaigns == 5
    
    def test_available_slots(self):
        """Available slots should track capacity."""
        worker = CampaignWorkerLoop(max_campaigns=3)
        assert worker.available_slots == 3
        
        # Simulate holding campaigns
        worker._held_campaigns.add(uuid4())
        assert worker.available_slots == 2
        
        worker._held_campaigns.add(uuid4())
        worker._held_campaigns.add(uuid4())
        assert worker.available_slots == 0
        
        # Don't go negative
        worker._held_campaigns.add(uuid4())
        assert worker.available_slots == 0
    
    def test_current_campaign_count(self):
        """Current campaign count should track held campaigns."""
        worker = CampaignWorkerLoop()
        assert worker.current_campaign_count == 0
        
        worker._held_campaigns.add(uuid4())
        assert worker.current_campaign_count == 1


class TestWorkerRegister:
    """Tests for worker registration."""
    
    @pytest.mark.asyncio
    async def test_register_success(self):
        """Worker should register successfully."""
        worker = CampaignWorkerLoop(worker_id='test-worker')
        
        mock_db = AsyncMock()
        mock_worker = MagicMock()
        
        with patch('app.services.campaign_worker.register_local_worker', 
                   return_value=mock_worker) as mock_register:
            result = await worker.register(mock_db)
            
            assert result is True
            mock_register.assert_called_once_with(
                mock_db,
                worker_id='test-worker',
                max_campaigns=3,
            )
    
    @pytest.mark.asyncio
    async def test_register_failure(self):
        """Worker should handle registration failure."""
        worker = CampaignWorkerLoop(worker_id='test-worker')
        
        mock_db = AsyncMock()
        
        with patch('app.services.campaign_worker.register_local_worker', 
                   return_value=None):
            result = await worker.register(mock_db)
            assert result is False


class TestClaimCampaigns:
    """Tests for campaign claiming."""
    
    @pytest.mark.asyncio
    async def test_claim_campaigns_success(self):
        """Worker should claim available campaigns."""
        worker = CampaignWorkerLoop(worker_id='test-worker', max_campaigns=2)
        
        mock_db = AsyncMock()
        campaign1 = MagicMock(id=uuid4())
        campaign2 = MagicMock(id=uuid4())
        
        with patch('app.services.campaign_worker.get_claimable_campaigns',
                   return_value=[campaign1, campaign2]) as mock_get:
            with patch('app.services.campaign_worker.acquire_lease',
                       return_value=True) as mock_acquire:
                with patch('app.services.campaign_worker.increment_campaign_count',
                           return_value=True):
                    claimed = await worker.claim_campaigns(mock_db)
                    
                    assert len(claimed) == 2
                    assert campaign1.id in claimed
                    assert campaign2.id in claimed
                    assert campaign1.id in worker._held_campaigns
                    assert campaign2.id in worker._held_campaigns
                    
                    mock_get.assert_called_once_with(mock_db, limit=2)
    
    @pytest.mark.asyncio
    async def test_claim_campaigns_partial_success(self):
        """Worker should handle partial claim success (race condition)."""
        worker = CampaignWorkerLoop(worker_id='test-worker', max_campaigns=2)
        
        mock_db = AsyncMock()
        campaign1 = MagicMock(id=uuid4())
        campaign2 = MagicMock(id=uuid4())
        
        # First lease succeeds, second fails (claimed by another worker)
        lease_results = [True, False]
        
        with patch('app.services.campaign_worker.get_claimable_campaigns',
                   return_value=[campaign1, campaign2]):
            with patch('app.services.campaign_worker.acquire_lease',
                       side_effect=lease_results):
                with patch('app.services.campaign_worker.increment_campaign_count',
                           return_value=True):
                    claimed = await worker.claim_campaigns(mock_db)
                    
                    assert len(claimed) == 1
                    assert campaign1.id in claimed
                    assert campaign2.id not in claimed
    
    @pytest.mark.asyncio
    async def test_claim_campaigns_at_capacity(self):
        """Worker should not claim when at capacity."""
        worker = CampaignWorkerLoop(worker_id='test-worker', max_campaigns=1)
        worker._held_campaigns.add(uuid4())  # Already at capacity
        
        mock_db = AsyncMock()
        
        with patch('app.services.campaign_worker.get_claimable_campaigns') as mock_get:
            claimed = await worker.claim_campaigns(mock_db)
            
            assert len(claimed) == 0
            mock_get.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_claim_campaigns_during_shutdown(self):
        """Worker should not claim during shutdown."""
        worker = CampaignWorkerLoop(worker_id='test-worker')
        worker._shutting_down = True
        
        mock_db = AsyncMock()
        
        with patch('app.services.campaign_worker.get_claimable_campaigns') as mock_get:
            claimed = await worker.claim_campaigns(mock_db)
            
            assert len(claimed) == 0
            mock_get.assert_not_called()


class TestReleaseCampaign:
    """Tests for campaign release."""
    
    @pytest.mark.asyncio
    async def test_release_campaign_success(self):
        """Worker should release held campaign."""
        worker = CampaignWorkerLoop(worker_id='test-worker')
        campaign_id = uuid4()
        worker._held_campaigns.add(campaign_id)
        
        mock_db = AsyncMock()
        
        with patch('app.services.campaign_worker.release_lease',
                   return_value=True) as mock_release:
            with patch('app.services.campaign_worker.decrement_campaign_count',
                       return_value=True):
                result = await worker.release_campaign(mock_db, campaign_id)
                
                assert result is True
                assert campaign_id not in worker._held_campaigns
                mock_release.assert_called_once_with(
                    mock_db, 'test-worker', campaign_id, None
                )
    
    @pytest.mark.asyncio
    async def test_release_campaign_with_status(self):
        """Worker should release with new status."""
        worker = CampaignWorkerLoop(worker_id='test-worker')
        campaign_id = uuid4()
        worker._held_campaigns.add(campaign_id)
        
        mock_db = AsyncMock()
        
        with patch('app.services.campaign_worker.release_lease',
                   return_value=True):
            with patch('app.services.campaign_worker.decrement_campaign_count',
                       return_value=True):
                result = await worker.release_campaign(
                    mock_db, 
                    campaign_id, 
                    new_status=CampaignStatus.PAUSED
                )
                
                assert result is True
    
    @pytest.mark.asyncio
    async def test_release_campaign_not_held(self):
        """Worker should reject release of unowned campaign."""
        worker = CampaignWorkerLoop(worker_id='test-worker')
        campaign_id = uuid4()  # Not in _held_campaigns
        
        mock_db = AsyncMock()
        
        with patch('app.services.campaign_worker.release_lease') as mock_release:
            result = await worker.release_campaign(mock_db, campaign_id)
            
            assert result is False
            mock_release.assert_not_called()


class TestSendHeartbeat:
    """Tests for heartbeat sending."""
    
    @pytest.mark.asyncio
    async def test_send_heartbeat_success(self):
        """Worker should renew all leases."""
        worker = CampaignWorkerLoop(worker_id='test-worker')
        campaign1 = uuid4()
        campaign2 = uuid4()
        worker._held_campaigns = {campaign1, campaign2}
        
        mock_db = AsyncMock()
        
        with patch('app.services.campaign_worker.renew_lease',
                   return_value=True):
            with patch('app.services.campaign_worker.update_worker_heartbeat',
                       return_value=True):
                renewed = await worker.send_heartbeat(mock_db)
                
                assert renewed == 2
                assert worker._last_heartbeat is not None
    
    @pytest.mark.asyncio
    async def test_send_heartbeat_lost_lease(self):
        """Worker should remove campaigns with failed renewals."""
        worker = CampaignWorkerLoop(worker_id='test-worker')
        campaign1 = uuid4()
        campaign2 = uuid4()
        worker._held_campaigns = {campaign1, campaign2}
        
        mock_db = AsyncMock()
        
        # First succeeds, second fails
        renew_results = [True, False]
        
        with patch('app.services.campaign_worker.renew_lease',
                   side_effect=renew_results):
            with patch('app.services.campaign_worker.update_worker_heartbeat',
                       return_value=True):
                with patch('app.services.campaign_worker.decrement_campaign_count',
                           return_value=True):
                    renewed = await worker.send_heartbeat(mock_db)
                    
                    assert renewed == 1
                    # One campaign should be removed
                    assert len(worker._held_campaigns) == 1


class TestProcessCampaign:
    """Tests for campaign processing."""
    
    @pytest.mark.asyncio
    async def test_process_campaign_not_held(self):
        """Worker should reject processing unowned campaign."""
        worker = CampaignWorkerLoop(worker_id='test-worker')
        campaign_id = uuid4()  # Not held
        
        mock_db = AsyncMock()
        
        result = await worker.process_campaign(mock_db, campaign_id)
        
        assert "error" in result
        assert "not held" in result["error"]
    
    @pytest.mark.asyncio
    async def test_process_campaign_not_found(self):
        """Worker should handle missing campaign."""
        worker = CampaignWorkerLoop(worker_id='test-worker')
        campaign_id = uuid4()
        worker._held_campaigns.add(campaign_id)
        
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        
        result = await worker.process_campaign(mock_db, campaign_id)
        
        assert "error" in result
        assert "not found" in result["error"]
        assert campaign_id not in worker._held_campaigns
    
    @pytest.mark.asyncio
    async def test_process_campaign_releases_paused(self):
        """Worker should release paused campaigns."""
        worker = CampaignWorkerLoop(worker_id='test-worker')
        campaign_id = uuid4()
        worker._held_campaigns.add(campaign_id)
        
        mock_campaign = MagicMock()
        mock_campaign.id = campaign_id
        mock_campaign.status = CampaignStatus.PAUSED
        mock_campaign.user_id = uuid4()
        
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_campaign
        mock_db.execute.return_value = mock_result
        
        with patch.object(worker, 'release_campaign', return_value=True) as mock_release:
            result = await worker.process_campaign(mock_db, campaign_id)
            
            assert result.get("released") is True
            mock_release.assert_called_once()


class TestWorkLoopIteration:
    """Tests for the complete work loop iteration."""
    
    @pytest.mark.asyncio
    async def test_work_loop_iteration(self):
        """Work loop should heartbeat, claim, and process."""
        worker = CampaignWorkerLoop(worker_id='test-worker', max_campaigns=2)
        
        mock_db = AsyncMock()
        campaign_id = uuid4()
        
        with patch.object(worker, 'send_heartbeat', return_value=0):
            with patch.object(worker, 'claim_campaigns', return_value=[campaign_id]):
                with patch.object(worker, 'process_campaign', 
                                  return_value={"success": True, "message": "OK"}):
                    # Add the claimed campaign to _held_campaigns as claim_campaigns would
                    worker._held_campaigns.add(campaign_id)
                    
                    result = await worker.run_work_loop_iteration(mock_db)
                    
                    assert result["worker_id"] == 'test-worker'
                    assert str(campaign_id) in result["claimed"]
                    assert len(result["processed"]) == 1


class TestGracefulShutdown:
    """Tests for graceful shutdown."""
    
    @pytest.mark.asyncio
    async def test_graceful_shutdown(self):
        """Worker should release all campaigns on shutdown."""
        worker = CampaignWorkerLoop(worker_id='test-worker')
        campaign1 = uuid4()
        campaign2 = uuid4()
        worker._held_campaigns = {campaign1, campaign2}
        
        mock_db = AsyncMock()
        
        with patch.object(worker, 'release_campaign', return_value=True) as mock_release:
            with patch('app.services.campaign_worker_service.disconnect_worker', 
                       return_value=True):
                await worker.graceful_shutdown(mock_db)
                
                assert worker._shutting_down is True
                assert mock_release.call_count == 2


class TestGetWorkerInstance:
    """Tests for singleton worker instance."""
    
    def test_get_worker_instance_singleton(self):
        """Should return same instance."""
        # Reset singleton
        import app.services.campaign_worker as cw
        cw._worker_instance = None
        
        with patch('app.services.campaign_worker.socket.gethostname', 
                   return_value='singleton-test'):
            instance1 = get_worker_instance()
            instance2 = get_worker_instance()
            
            assert instance1 is instance2
            
        # Clean up
        cw._worker_instance = None


class TestRunCampaignWorkerIteration:
    """Tests for the main entry point."""
    
    @pytest.mark.asyncio
    async def test_run_iteration_registers_new_worker(self):
        """Should register worker if not exists."""
        # Reset singleton
        import app.services.campaign_worker as cw
        cw._worker_instance = None
        
        mock_db = AsyncMock()
        
        with patch('app.services.campaign_worker.socket.gethostname', 
                   return_value='test-host'):
            with patch('app.services.campaign_worker.get_worker_by_id', 
                       return_value=None):
                with patch('app.services.campaign_worker.register_local_worker',
                           return_value=MagicMock()):
                    with patch.object(CampaignWorkerLoop, 'run_work_loop_iteration',
                                      return_value={"worker_id": "test-host"}):
                        result = await run_campaign_worker_iteration(mock_db)
                        
                        assert result["worker_id"] == "test-host"
        
        # Clean up
        cw._worker_instance = None
