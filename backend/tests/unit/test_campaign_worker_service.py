"""Tests for Campaign Worker Service."""
import pytest
from datetime import datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import campaign_worker_service
from app.services.campaign_worker_service import (
    register_worker,
    register_local_worker,
    update_worker_heartbeat,
    disconnect_worker,
    set_worker_draining,
    get_available_workers,
    get_best_worker_for_campaign,
    increment_campaign_count,
    decrement_campaign_count,
    detect_offline_workers,
    get_worker_by_id,
    get_all_workers,
    get_worker_stats,
    WorkerNotFoundError,
    WorkerCapacityError,
    DEFAULT_CAMPAIGN_CAPACITY,
    WORKER_OFFLINE_THRESHOLD_SECONDS,
)
from app.models.resource import CampaignWorker, CampaignWorkerStatus


class TestRegisterWorker:
    """Tests for worker registration."""
    
    @pytest.mark.asyncio
    async def test_register_new_worker(self):
        """Test registering a brand new worker."""
        db = AsyncMock()
        
        # Mock no existing worker
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result
        
        result = await register_worker(
            db=db,
            hostname="test-host",
            worker_type="local",
            campaign_capacity=5,
            ram_gb=64,
            cpu_threads=20
        )
        
        # Should have added a new worker
        db.add.assert_called_once()
        db.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_register_existing_worker_reconnect(self):
        """Test reconnecting an existing worker updates its info."""
        db = AsyncMock()
        
        # Mock existing worker
        mock_worker = MagicMock(spec=CampaignWorker)
        mock_worker.worker_id = "local-test-host"
        mock_worker.hostname = "test-host"
        mock_worker.status = CampaignWorkerStatus.OFFLINE.value
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_worker
        db.execute.return_value = mock_result
        
        result = await register_worker(
            db=db,
            hostname="test-host",
            worker_type="local",
            campaign_capacity=8,
            ram_gb=128
        )
        
        # Should have updated the existing worker
        assert mock_worker.campaign_capacity == 8
        assert mock_worker.ram_gb == 128
        assert mock_worker.status == CampaignWorkerStatus.ONLINE.value
        db.add.assert_not_called()  # Should not add new
        db.commit.assert_called_once()


class TestUpdateWorkerHeartbeat:
    """Tests for heartbeat updates."""
    
    @pytest.mark.asyncio
    async def test_heartbeat_success(self):
        """Test successful heartbeat update."""
        db = AsyncMock()
        worker_id = "local-testhost"
        
        mock_worker = MagicMock(spec=CampaignWorker)
        mock_worker.worker_id = worker_id
        mock_worker.status = CampaignWorkerStatus.ONLINE.value
        mock_worker.last_heartbeat_at = datetime.utcnow() - timedelta(minutes=1)
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_worker
        db.execute.return_value = mock_result
        
        result = await update_worker_heartbeat(db, worker_id, campaign_ids=[])
        
        assert result == mock_worker
        assert mock_worker.last_heartbeat_at is not None
        db.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_heartbeat_brings_offline_worker_online(self):
        """Test heartbeat from offline worker brings it online."""
        db = AsyncMock()
        worker_id = "local-testhost"
        
        mock_worker = MagicMock(spec=CampaignWorker)
        mock_worker.worker_id = worker_id
        mock_worker.status = CampaignWorkerStatus.OFFLINE.value
        mock_worker.last_heartbeat_at = None
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_worker
        db.execute.return_value = mock_result
        
        result = await update_worker_heartbeat(db, worker_id, campaign_ids=[])
        
        assert mock_worker.status == CampaignWorkerStatus.ONLINE.value
        assert mock_worker.connected_at is not None
    
    @pytest.mark.asyncio
    async def test_heartbeat_worker_not_found(self):
        """Test heartbeat for non-existent worker fails."""
        db = AsyncMock()
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result
        
        with pytest.raises(WorkerNotFoundError):
            await update_worker_heartbeat(db, "unknown-worker")


class TestDisconnectWorker:
    """Tests for worker disconnection."""
    
    @pytest.mark.asyncio
    async def test_disconnect_worker_success(self):
        """Test disconnecting a worker."""
        db = AsyncMock()
        worker_id = "local-testhost"
        
        mock_worker = MagicMock(spec=CampaignWorker)
        mock_worker.worker_id = worker_id
        mock_worker.status = CampaignWorkerStatus.ONLINE.value
        mock_worker.current_campaign_count = 2
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_worker
        db.execute.return_value = mock_result
        
        # Mock get_worker_campaigns to return empty list
        with patch.object(
            campaign_worker_service.campaign_lease_service,
            'get_worker_campaigns',
            return_value=[]
        ):
            result = await disconnect_worker(db, worker_id, release_campaigns=True)
        
        assert mock_worker.status == CampaignWorkerStatus.OFFLINE.value
        assert mock_worker.disconnected_at is not None
        assert mock_worker.current_campaign_count == 0
        db.commit.assert_called()


class TestWorkerDraining:
    """Tests for drain mode."""
    
    @pytest.mark.asyncio
    async def test_set_worker_draining(self):
        """Test putting worker in drain mode."""
        db = AsyncMock()
        worker_id = "local-testhost"
        
        mock_worker = MagicMock(spec=CampaignWorker)
        mock_worker.worker_id = worker_id
        mock_worker.status = CampaignWorkerStatus.ONLINE.value
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_worker
        db.execute.return_value = mock_result
        
        result = await set_worker_draining(db, worker_id, draining=True)
        
        assert mock_worker.status == CampaignWorkerStatus.DRAINING.value
        db.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_exit_drain_mode(self):
        """Test exiting drain mode."""
        db = AsyncMock()
        worker_id = "local-testhost"
        
        mock_worker = MagicMock(spec=CampaignWorker)
        mock_worker.worker_id = worker_id
        mock_worker.status = CampaignWorkerStatus.DRAINING.value
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_worker
        db.execute.return_value = mock_result
        
        result = await set_worker_draining(db, worker_id, draining=False)
        
        assert mock_worker.status == CampaignWorkerStatus.ONLINE.value


class TestGetAvailableWorkers:
    """Tests for finding available workers."""
    
    @pytest.mark.asyncio
    async def test_get_available_workers(self):
        """Test getting workers with available capacity."""
        db = AsyncMock()
        
        mock_workers = [
            MagicMock(
                spec=CampaignWorker,
                status=CampaignWorkerStatus.ONLINE.value,
                campaign_capacity=5,
                current_campaign_count=2,
                preferences=[]
            ),
            MagicMock(
                spec=CampaignWorker,
                status=CampaignWorkerStatus.ONLINE.value,
                campaign_capacity=3,
                current_campaign_count=1,
                preferences=[]
            ),
        ]
        
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = mock_workers
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        
        result = await get_available_workers(db, min_capacity=1)
        
        assert len(result) == 2


class TestWorkerCapacity:
    """Tests for capacity management."""
    
    @pytest.mark.asyncio
    async def test_increment_campaign_count(self):
        """Test incrementing campaign count."""
        db = AsyncMock()
        worker_id = "local-testhost"
        
        mock_worker = MagicMock(spec=CampaignWorker)
        mock_worker.worker_id = worker_id
        mock_worker.campaign_capacity = 5
        mock_worker.current_campaign_count = 2
        mock_worker.has_capacity = True
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_worker
        db.execute.return_value = mock_result
        
        result = await increment_campaign_count(db, worker_id)
        
        assert mock_worker.current_campaign_count == 3
        db.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_increment_fails_at_capacity(self):
        """Test incrementing fails when worker is at capacity."""
        db = AsyncMock()
        worker_id = "local-testhost"
        
        mock_worker = MagicMock(spec=CampaignWorker)
        mock_worker.worker_id = worker_id
        mock_worker.campaign_capacity = 3
        mock_worker.current_campaign_count = 3
        mock_worker.has_capacity = False
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_worker
        db.execute.return_value = mock_result
        
        with pytest.raises(WorkerCapacityError):
            await increment_campaign_count(db, worker_id)
    
    @pytest.mark.asyncio
    async def test_decrement_campaign_count(self):
        """Test decrementing campaign count."""
        db = AsyncMock()
        worker_id = "local-testhost"
        
        mock_worker = MagicMock(spec=CampaignWorker)
        mock_worker.worker_id = worker_id
        mock_worker.current_campaign_count = 3
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_worker
        db.execute.return_value = mock_result
        
        result = await decrement_campaign_count(db, worker_id)
        
        assert mock_worker.current_campaign_count == 2
        db.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_decrement_doesnt_go_negative(self):
        """Test decrementing doesn't go below zero."""
        db = AsyncMock()
        worker_id = "local-testhost"
        
        mock_worker = MagicMock(spec=CampaignWorker)
        mock_worker.worker_id = worker_id
        mock_worker.current_campaign_count = 0
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_worker
        db.execute.return_value = mock_result
        
        result = await decrement_campaign_count(db, worker_id)
        
        assert mock_worker.current_campaign_count == 0


class TestDetectOfflineWorkers:
    """Tests for offline detection."""
    
    @pytest.mark.asyncio
    async def test_detect_offline_workers(self):
        """Test detecting workers that haven't sent heartbeats."""
        db = AsyncMock()
        
        stale_worker = MagicMock(spec=CampaignWorker)
        stale_worker.worker_id = "stale-worker"
        stale_worker.status = CampaignWorkerStatus.ONLINE.value
        stale_worker.last_heartbeat_at = datetime.utcnow() - timedelta(minutes=10)
        
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [stale_worker]
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        
        # Mock disconnect_worker to track calls
        with patch.object(
            campaign_worker_service,
            'disconnect_worker',
            return_value=stale_worker
        ) as mock_disconnect:
            result = await detect_offline_workers(db)
        
        assert len(result) == 1
        mock_disconnect.assert_called_once_with(db, "stale-worker", release_campaigns=True)


class TestGetWorkerStats:
    """Tests for worker statistics."""
    
    @pytest.mark.asyncio
    async def test_get_worker_stats(self):
        """Test getting aggregate worker statistics."""
        db = AsyncMock()
        
        online_worker = MagicMock(spec=CampaignWorker)
        online_worker.status = CampaignWorkerStatus.ONLINE.value
        online_worker.campaign_capacity = 5
        online_worker.current_campaign_count = 2
        
        offline_worker = MagicMock(spec=CampaignWorker)
        offline_worker.status = CampaignWorkerStatus.OFFLINE.value
        offline_worker.campaign_capacity = 3
        offline_worker.current_campaign_count = 0
        
        # Mock get_all_workers
        with patch.object(
            campaign_worker_service,
            'get_all_workers',
            return_value=[online_worker, offline_worker]
        ):
            result = await get_worker_stats(db)
        
        assert result["total_workers"] == 2
        assert result["online_workers"] == 1
        assert result["offline_workers"] == 1
        assert result["total_capacity"] == 5  # Only count online worker capacity
        assert result["used_capacity"] == 2
        assert result["available_capacity"] == 3
