"""Unit tests for broker_service campaign worker functionality."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from app.services.broker_service import BrokerService, ConnectedAgent


class TestConnectedAgentCampaignWorker:
    """Tests for ConnectedAgent campaign worker properties."""
    
    def test_default_campaign_worker_state(self):
        """New agent should not be a campaign worker by default."""
        agent = ConnectedAgent(
            agent_id=uuid4(),
            websocket=MagicMock(),
            hostname="test-host",
            display_name="Test Agent",
            capabilities={"max_concurrent_jobs": 2}
        )
        
        assert agent.is_campaign_worker is False
        assert agent.campaign_worker_id is None
        assert agent.campaign_capacity == 0
        assert len(agent.held_campaigns) == 0
        assert agent.is_campaign_available is False
    
    def test_campaign_worker_availability(self):
        """Campaign worker availability should track capacity and held campaigns."""
        agent = ConnectedAgent(
            agent_id=uuid4(),
            websocket=MagicMock(),
            hostname="test-host",
            display_name="Test Agent",
            capabilities={"max_concurrent_jobs": 2}
        )
        
        # Enable campaign worker mode
        agent.is_campaign_worker = True
        agent.campaign_worker_id = "remote-test-host"
        agent.campaign_capacity = 3
        
        # Should be available with no campaigns
        assert agent.is_campaign_available is True
        
        # Add campaigns
        agent.held_campaigns.add(uuid4())
        assert agent.is_campaign_available is True
        
        agent.held_campaigns.add(uuid4())
        assert agent.is_campaign_available is True
        
        # At capacity
        agent.held_campaigns.add(uuid4())
        assert agent.is_campaign_available is False
    
    def test_campaign_worker_not_available_when_disabled(self):
        """Non-campaign worker should never be campaign available."""
        agent = ConnectedAgent(
            agent_id=uuid4(),
            websocket=MagicMock(),
            hostname="test-host",
            display_name="Test Agent",
            capabilities={"max_concurrent_jobs": 2}
        )
        
        # Not a campaign worker, but has capacity
        agent.campaign_capacity = 3
        assert agent.is_campaign_available is False


class TestBrokerServiceCampaignWorker:
    """Tests for BrokerService campaign worker methods."""
    
    @pytest.fixture
    def broker(self):
        """Create a broker service instance."""
        return BrokerService()
    
    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()
    
    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        return ws
    
    @pytest.mark.asyncio
    async def test_register_campaign_worker(self, broker, mock_db, mock_websocket):
        """Test campaign worker registration."""
        agent_id = uuid4()
        
        # Set up connected agent
        connected = ConnectedAgent(
            agent_id=agent_id,
            websocket=mock_websocket,
            hostname="test-host",
            display_name="Test Agent",
            capabilities={"max_concurrent_jobs": 2}
        )
        broker._connected_agents[agent_id] = connected
        
        # Mock campaign_worker_service
        with patch("app.services.campaign_worker_service.register_worker") as mock_register:
            mock_worker = MagicMock()
            mock_register.return_value = mock_worker
            
            worker_data = {
                "worker_id": "remote-test-host",
                "max_campaigns": 3,
                "hostname": "test-host",
            }
            
            await broker.register_campaign_worker(mock_db, agent_id, worker_data)
            
            # Verify agent state updated
            assert connected.is_campaign_worker is True
            assert connected.campaign_worker_id == "remote-test-host"
            assert connected.campaign_capacity == 3
            
            # Verify lookup tables updated
            assert broker._worker_id_to_agent["remote-test-host"] == agent_id
            
            # Verify worker registered in database
            mock_register.assert_called_once()
            
            # Verify confirmation sent
            mock_websocket.send_json.assert_called_once()
            call_args = mock_websocket.send_json.call_args[0][0]
            assert call_args["type"] == "worker_registered"
            assert call_args["data"]["worker_id"] == "remote-test-host"
    
    @pytest.mark.asyncio
    async def test_campaign_accepted(self, broker, mock_db):
        """Test campaign acceptance tracking."""
        agent_id = uuid4()
        campaign_id = uuid4()
        
        # Set up campaign worker
        connected = ConnectedAgent(
            agent_id=agent_id,
            websocket=AsyncMock(),
            hostname="test-host",
            display_name="Test Agent",
            capabilities={}
        )
        connected.is_campaign_worker = True
        connected.campaign_worker_id = "remote-test-host"
        broker._connected_agents[agent_id] = connected
        
        accept_data = {
            "campaign_id": str(campaign_id),
            "worker_id": "remote-test-host",
        }
        
        await broker.campaign_accepted(mock_db, agent_id, accept_data)
        
        # Verify tracking
        assert campaign_id in connected.held_campaigns
        assert broker._campaign_to_worker[campaign_id] == "remote-test-host"
    
    @pytest.mark.asyncio
    async def test_campaign_release(self, broker, mock_db):
        """Test campaign release cleanup."""
        agent_id = uuid4()
        campaign_id = uuid4()
        
        # Set up campaign worker with held campaign
        connected = ConnectedAgent(
            agent_id=agent_id,
            websocket=AsyncMock(),
            hostname="test-host",
            display_name="Test Agent",
            capabilities={}
        )
        connected.is_campaign_worker = True
        connected.campaign_worker_id = "remote-test-host"
        connected.held_campaigns.add(campaign_id)
        broker._connected_agents[agent_id] = connected
        broker._campaign_to_worker[campaign_id] = "remote-test-host"
        
        with patch("app.services.campaign_lease_service.release_lease") as mock_release:
            mock_release.return_value = None
            
            release_data = {
                "campaign_id": str(campaign_id),
                "worker_id": "remote-test-host",
                "reason": "completed",
            }
            
            await broker.campaign_release(mock_db, agent_id, release_data)
            
            # Verify cleanup
            assert campaign_id not in connected.held_campaigns
            assert campaign_id not in broker._campaign_to_worker
    
    @pytest.mark.asyncio
    async def test_route_user_input_to_campaign(self, broker, mock_db, mock_websocket):
        """Test user input routing to remote worker."""
        agent_id = uuid4()
        campaign_id = uuid4()
        
        # Set up campaign worker
        connected = ConnectedAgent(
            agent_id=agent_id,
            websocket=mock_websocket,
            hostname="test-host",
            display_name="Test Agent",
            capabilities={}
        )
        connected.is_campaign_worker = True
        connected.campaign_worker_id = "remote-test-host"
        broker._connected_agents[agent_id] = connected
        broker._worker_id_to_agent["remote-test-host"] = agent_id
        broker._campaign_to_worker[campaign_id] = "remote-test-host"
        
        # Route message
        routed = await broker.route_user_input_to_campaign(
            mock_db, campaign_id, "Hello from user!"
        )
        
        assert routed is True
        
        # Verify message sent
        mock_websocket.send_json.assert_called_once()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["type"] == "campaign_user_input"
        assert call_args["data"]["campaign_id"] == str(campaign_id)
        assert call_args["data"]["message"] == "Hello from user!"
    
    @pytest.mark.asyncio
    async def test_route_user_input_no_worker(self, broker, mock_db):
        """Test user input routing returns False when no worker."""
        campaign_id = uuid4()
        
        # No worker registered for this campaign
        routed = await broker.route_user_input_to_campaign(
            mock_db, campaign_id, "Hello!"
        )
        
        assert routed is False
    
    @pytest.mark.asyncio
    async def test_assign_campaign_to_worker(self, broker, mock_db, mock_websocket):
        """Test campaign assignment to available worker."""
        agent_id = uuid4()
        campaign_id = uuid4()
        
        # Set up available campaign worker
        connected = ConnectedAgent(
            agent_id=agent_id,
            websocket=mock_websocket,
            hostname="test-host",
            display_name="Test Agent",
            capabilities={}
        )
        connected.is_campaign_worker = True
        connected.campaign_worker_id = "remote-test-host"
        connected.campaign_capacity = 3
        broker._connected_agents[agent_id] = connected
        
        campaign_data = {
            "status": "active",
            "current_phase": "executing",
            "proposal_title": "Test Campaign",
        }
        
        worker_id = await broker.assign_campaign_to_worker(
            mock_db, campaign_id, campaign_data
        )
        
        assert worker_id == "remote-test-host"
        
        # Verify assignment message sent
        mock_websocket.send_json.assert_called_once()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["type"] == "campaign_assigned"
        assert call_args["data"]["campaign_id"] == str(campaign_id)
    
    @pytest.mark.asyncio
    async def test_assign_campaign_no_workers(self, broker, mock_db):
        """Test campaign assignment returns None when no workers available."""
        campaign_id = uuid4()
        
        # No connected agents
        worker_id = await broker.assign_campaign_to_worker(
            mock_db, campaign_id, {}
        )
        
        assert worker_id is None
    
    def test_get_available_campaign_workers(self, broker, mock_websocket):
        """Test getting available campaign workers."""
        # Add some agents
        agent1_id = uuid4()
        agent1 = ConnectedAgent(
            agent_id=agent1_id,
            websocket=mock_websocket,
            hostname="host1",
            display_name=None,
            capabilities={}
        )
        agent1.is_campaign_worker = True
        agent1.campaign_capacity = 3
        broker._connected_agents[agent1_id] = agent1
        
        agent2_id = uuid4()
        agent2 = ConnectedAgent(
            agent_id=agent2_id,
            websocket=mock_websocket,
            hostname="host2",
            display_name=None,
            capabilities={}
        )
        # Not a campaign worker
        broker._connected_agents[agent2_id] = agent2
        
        agent3_id = uuid4()
        agent3 = ConnectedAgent(
            agent_id=agent3_id,
            websocket=mock_websocket,
            hostname="host3",
            display_name=None,
            capabilities={}
        )
        agent3.is_campaign_worker = True
        agent3.campaign_capacity = 2
        agent3.held_campaigns = {uuid4(), uuid4()}  # At capacity
        broker._connected_agents[agent3_id] = agent3
        
        available = broker.get_available_campaign_workers()
        
        # Only agent1 should be available
        assert len(available) == 1
        assert available[0].hostname == "host1"
    
    def test_get_connected_agents_includes_campaign_info(self, broker, mock_websocket):
        """Test that connected agents list includes campaign worker info."""
        agent_id = uuid4()
        campaign_id = uuid4()
        
        connected = ConnectedAgent(
            agent_id=agent_id,
            websocket=mock_websocket,
            hostname="test-host",
            display_name="Test Agent",
            capabilities={"max_concurrent_jobs": 2}
        )
        connected.is_campaign_worker = True
        connected.campaign_worker_id = "remote-test-host"
        connected.campaign_capacity = 3
        connected.held_campaigns = {campaign_id}
        broker._connected_agents[agent_id] = connected
        
        agents = broker.get_connected_agents()
        
        assert len(agents) == 1
        agent_info = agents[0]
        
        assert agent_info["is_campaign_worker"] is True
        assert agent_info["campaign_worker_id"] == "remote-test-host"
        assert agent_info["campaign_capacity"] == 3
        assert agent_info["held_campaigns"] == 1
        assert agent_info["is_campaign_available"] is True
