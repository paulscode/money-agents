"""Unit tests for broker_service tool dispatch functionality."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from app.services.broker_service import BrokerService, ConnectedAgent


class TestCampaignToolDispatch:
    """Tests for campaign tool dispatch functionality."""
    
    @pytest.fixture
    def broker(self):
        """Create a broker service instance."""
        return BrokerService()
    
    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        db.execute = AsyncMock()
        return db
    
    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        ws.send = AsyncMock()
        return ws
    
    @pytest.fixture
    def requesting_agent(self, mock_websocket):
        """Create a requesting agent (campaign worker)."""
        agent_id = uuid4()
        agent = ConnectedAgent(
            agent_id=agent_id,
            websocket=mock_websocket,
            hostname="campaign-worker-host",
            display_name="Campaign Worker",
            capabilities={"max_concurrent_jobs": 2}
        )
        agent.is_campaign_worker = True
        agent.campaign_worker_id = "campaign-worker-1"
        return agent
    
    @pytest.fixture
    def target_agent(self):
        """Create a target agent (resource agent)."""
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        ws.send = AsyncMock()
        agent_id = uuid4()
        agent = ConnectedAgent(
            agent_id=agent_id,
            websocket=ws,
            hostname="resource-agent-host",
            display_name="Resource Agent",
            capabilities={"max_concurrent_jobs": 5}
        )
        return agent
    
    def _create_agent(self, hostname: str, max_jobs: int = 5, running_jobs: int = 0):
        """Helper to create test agents with configurable load."""
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        ws.send = AsyncMock()
        agent = ConnectedAgent(
            agent_id=uuid4(),
            websocket=ws,
            hostname=hostname,
            display_name=f"Agent {hostname}",
            capabilities={"max_concurrent_jobs": max_jobs}
        )
        # Add running jobs to simulate load
        for _ in range(running_jobs):
            agent.running_jobs.add(uuid4())
        return agent
    
    @pytest.fixture
    def mock_tool(self):
        """Create a mock tool."""
        tool = MagicMock()
        tool.id = uuid4()
        tool.slug = "web_search"
        tool.name = "Web Search"
        tool.interface_type = "rest_api"
        tool.interface_config = {"url": "http://example.com"}
        tool.timeout_seconds = 300
        tool.is_distributed = MagicMock(return_value=True)
        return tool
    
    @pytest.mark.asyncio
    async def test_tool_dispatch_to_remote_agent(
        self, broker, mock_db, requesting_agent, target_agent, mock_tool
    ):
        """Test dispatching a tool to a remote agent."""
        # Register agents
        broker._connected_agents[requesting_agent.agent_id] = requesting_agent
        broker._connected_agents[target_agent.agent_id] = target_agent
        
        # Mock database query for tool lookup
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tool
        mock_db.execute.return_value = mock_result
        
        # Mock get_online_agents_for_tool to return target agent
        broker.get_online_agents_for_tool = AsyncMock(return_value=[target_agent])
        broker.check_resources_available_for_tool = AsyncMock(return_value=True)
        
        # Dispatch tool
        exec_id = str(uuid4())
        dispatch_data = {
            "execution_id": exec_id,
            "worker_id": "campaign-worker-1",
            "campaign_id": str(uuid4()),
            "tool_slug": "web_search",
            "params": {"query": "test query"},
        }
        
        await broker.campaign_tool_dispatch(
            mock_db, requesting_agent.agent_id, dispatch_data
        )
        
        # Verify dispatch was tracked
        assert exec_id in broker._pending_tool_dispatches
        dispatch_info = broker._pending_tool_dispatches[exec_id]
        assert dispatch_info["requesting_agent_id"] == requesting_agent.agent_id
        assert dispatch_info["target_agent_id"] == target_agent.agent_id
        assert dispatch_info["tool_slug"] == "web_search"
        
        # Verify job was sent to target agent
        target_agent.websocket.send.assert_called_once()
        sent_data = target_agent.websocket.send.call_args[0][0]
        import json
        job_data = json.loads(sent_data)
        assert job_data["type"] == "job_assigned"
        assert job_data["data"]["tool_slug"] == "web_search"
        assert job_data["data"]["is_campaign_dispatch"] is True
    
    @pytest.mark.asyncio
    async def test_tool_dispatch_local_fallback_no_remote_agents(
        self, broker, mock_db, requesting_agent, mock_tool
    ):
        """Test falling back to local execution when no remote agents available."""
        broker._connected_agents[requesting_agent.agent_id] = requesting_agent
        
        # Mock database query for tool lookup
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tool
        mock_db.execute.return_value = mock_result
        
        # No available agents
        broker.get_online_agents_for_tool = AsyncMock(return_value=[])
        
        # Mock local execution
        with patch.object(broker, '_execute_tool_locally', new_callable=AsyncMock) as mock_local:
            exec_id = str(uuid4())
            dispatch_data = {
                "execution_id": exec_id,
                "worker_id": "campaign-worker-1",
                "campaign_id": str(uuid4()),
                "tool_slug": "web_search",
                "params": {"query": "test query"},
            }
            
            await broker.campaign_tool_dispatch(
                mock_db, requesting_agent.agent_id, dispatch_data
            )
            
            # Verify local execution was called
            mock_local.assert_called_once()
            call_args = mock_local.call_args
            assert call_args[0][0] == mock_db
            assert call_args[0][1] == requesting_agent
            assert call_args[0][2] == exec_id
    
    @pytest.mark.asyncio
    async def test_tool_dispatch_local_only_tool(
        self, broker, mock_db, requesting_agent, mock_tool
    ):
        """Test local-only tools are executed locally."""
        broker._connected_agents[requesting_agent.agent_id] = requesting_agent
        
        # Make tool local-only
        mock_tool.is_distributed = MagicMock(return_value=False)
        
        # Mock database query for tool lookup
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tool
        mock_db.execute.return_value = mock_result
        
        # Mock local execution
        with patch.object(broker, '_execute_tool_locally', new_callable=AsyncMock) as mock_local:
            exec_id = str(uuid4())
            dispatch_data = {
                "execution_id": exec_id,
                "worker_id": "campaign-worker-1",
                "campaign_id": str(uuid4()),
                "tool_slug": "local_tool",
                "params": {},
            }
            
            await broker.campaign_tool_dispatch(
                mock_db, requesting_agent.agent_id, dispatch_data
            )
            
            # Verify local execution was called
            mock_local.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_tool_dispatch_unknown_tool(
        self, broker, mock_db, requesting_agent
    ):
        """Test error handling for unknown tool."""
        broker._connected_agents[requesting_agent.agent_id] = requesting_agent
        
        # Tool not found
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        
        exec_id = str(uuid4())
        dispatch_data = {
            "execution_id": exec_id,
            "worker_id": "campaign-worker-1",
            "campaign_id": str(uuid4()),
            "tool_slug": "unknown_tool",
            "params": {},
        }
        
        await broker.campaign_tool_dispatch(
            mock_db, requesting_agent.agent_id, dispatch_data
        )
        
        # Verify error was sent back to requesting agent
        requesting_agent.websocket.send_json.assert_called_once()
        sent_data = requesting_agent.websocket.send_json.call_args[0][0]
        assert sent_data["type"] == "tool_result"
        assert sent_data["data"]["result"]["success"] is False
        assert "not found" in sent_data["data"]["result"]["error"]
    
    @pytest.mark.asyncio
    async def test_tool_dispatch_prefers_least_loaded_agent(
        self, broker, mock_db, requesting_agent, mock_tool
    ):
        """Test that tool dispatch prefers agents with lower load."""
        broker._connected_agents[requesting_agent.agent_id] = requesting_agent
        
        # Create three agents with different loads:
        # - busy_agent: 4/5 jobs running (80% load)
        # - medium_agent: 2/5 jobs running (40% load)
        # - idle_agent: 0/5 jobs running (0% load)
        busy_agent = self._create_agent("busy-host", max_jobs=5, running_jobs=4)
        medium_agent = self._create_agent("medium-host", max_jobs=5, running_jobs=2)
        idle_agent = self._create_agent("idle-host", max_jobs=5, running_jobs=0)
        
        # Register all agents
        broker._connected_agents[busy_agent.agent_id] = busy_agent
        broker._connected_agents[medium_agent.agent_id] = medium_agent
        broker._connected_agents[idle_agent.agent_id] = idle_agent
        
        # Mock database query for tool lookup
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tool
        mock_db.execute.return_value = mock_result
        
        # Return all agents in random order (busy first, to prove sorting works)
        broker.get_online_agents_for_tool = AsyncMock(
            return_value=[busy_agent, medium_agent, idle_agent]
        )
        broker.check_resources_available_for_tool = AsyncMock(return_value=True)
        
        exec_id = str(uuid4())
        dispatch_data = {
            "execution_id": exec_id,
            "worker_id": "campaign-worker-1",
            "campaign_id": str(uuid4()),
            "tool_slug": "web_search",
            "params": {"query": "test"},
        }
        
        await broker.campaign_tool_dispatch(
            mock_db, requesting_agent.agent_id, dispatch_data
        )
        
        # Verify dispatch was sent to the IDLE agent (lowest load)
        assert exec_id in broker._pending_tool_dispatches
        dispatch_info = broker._pending_tool_dispatches[exec_id]
        assert dispatch_info["target_agent_id"] == idle_agent.agent_id
        
        # Verify job was sent to idle agent, NOT busy or medium
        idle_agent.websocket.send.assert_called_once()
        busy_agent.websocket.send.assert_not_called()
        medium_agent.websocket.send.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_tool_result_routing(
        self, broker, mock_db, requesting_agent, target_agent, mock_tool
    ):
        """Test that tool results are routed back to requesting agent."""
        broker._connected_agents[requesting_agent.agent_id] = requesting_agent
        broker._connected_agents[target_agent.agent_id] = target_agent
        
        # Set up a pending dispatch
        exec_id = str(uuid4())
        broker._pending_tool_dispatches[exec_id] = {
            "requesting_agent_id": requesting_agent.agent_id,
            "target_agent_id": target_agent.agent_id,
            "tool_slug": "web_search",
            "dispatched_at": datetime.utcnow(),
        }
        
        # Simulate result from target agent
        result = {
            "success": True,
            "output": {"data": "search results"},
        }
        
        await broker.handle_dispatched_tool_result(
            mock_db, target_agent.agent_id, exec_id, result
        )
        
        # Verify result was sent back to requesting agent
        requesting_agent.websocket.send_json.assert_called_once()
        sent_data = requesting_agent.websocket.send_json.call_args[0][0]
        assert sent_data["type"] == "tool_result"
        assert sent_data["data"]["execution_id"] == exec_id
        
        # Verify dispatch was cleaned up
        assert exec_id not in broker._pending_tool_dispatches
    
    @pytest.mark.asyncio
    async def test_job_completed_routes_dispatched_tool(self, broker, mock_db):
        """Test that job_completed properly routes dispatched tool results."""
        # Set up agents
        requesting_ws = AsyncMock()
        requesting_ws.send_json = AsyncMock()
        requesting_agent = ConnectedAgent(
            agent_id=uuid4(),
            websocket=requesting_ws,
            hostname="campaign-worker",
            display_name="Campaign Worker",
            capabilities={}
        )
        
        target_ws = AsyncMock()
        target_agent = ConnectedAgent(
            agent_id=uuid4(),
            websocket=target_ws,
            hostname="resource-agent",
            display_name="Resource Agent",
            capabilities={}
        )
        
        broker._connected_agents[requesting_agent.agent_id] = requesting_agent
        broker._connected_agents[target_agent.agent_id] = target_agent
        
        # Set up pending dispatch
        exec_id = str(uuid4())
        broker._pending_tool_dispatches[exec_id] = {
            "requesting_agent_id": requesting_agent.agent_id,
            "target_agent_id": target_agent.agent_id,
            "tool_slug": "web_search",
            "dispatched_at": datetime.utcnow(),
        }
        
        # Add job to running jobs
        target_agent.running_jobs.add(UUID(exec_id))
        
        # Simulate job completion
        result = {"results": ["item1", "item2"]}
        
        await broker.job_completed(mock_db, target_agent.agent_id, UUID(exec_id), result)
        
        # Verify result was routed to requesting agent
        requesting_ws.send_json.assert_called()
        calls = requesting_ws.send_json.call_args_list
        
        # Find the tool_result call
        tool_result_call = None
        for call in calls:
            data = call[0][0]
            if data.get("type") == "tool_result":
                tool_result_call = data
                break
        
        assert tool_result_call is not None
        assert tool_result_call["data"]["execution_id"] == exec_id


class TestSendToolResult:
    """Tests for _send_tool_result method."""
    
    @pytest.fixture
    def broker(self):
        return BrokerService()
    
    @pytest.fixture
    def mock_agent(self):
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        return ConnectedAgent(
            agent_id=uuid4(),
            websocket=ws,
            hostname="test-host",
            display_name="Test Agent",
            capabilities={}
        )
    
    @pytest.mark.asyncio
    async def test_send_tool_result_success(self, broker, mock_agent):
        """Test sending a successful tool result."""
        exec_id = str(uuid4())
        result = {
            "success": True,
            "output": {"data": "result data"},
            "duration_ms": 150,
        }
        
        await broker._send_tool_result(mock_agent, exec_id, result)
        
        mock_agent.websocket.send_json.assert_called_once()
        sent_data = mock_agent.websocket.send_json.call_args[0][0]
        
        assert sent_data["type"] == "tool_result"
        assert sent_data["data"]["execution_id"] == exec_id
        assert sent_data["data"]["result"]["success"] is True
    
    @pytest.mark.asyncio
    async def test_send_tool_result_failure(self, broker, mock_agent):
        """Test sending a failed tool result."""
        exec_id = str(uuid4())
        result = {
            "success": False,
            "error": "Connection timeout",
        }
        
        await broker._send_tool_result(mock_agent, exec_id, result)
        
        mock_agent.websocket.send_json.assert_called_once()
        sent_data = mock_agent.websocket.send_json.call_args[0][0]
        
        assert sent_data["type"] == "tool_result"
        assert sent_data["data"]["result"]["success"] is False
        assert "timeout" in sent_data["data"]["result"]["error"]


class TestPendingToolDispatchTracking:
    """Tests for pending tool dispatch tracking."""
    
    @pytest.fixture
    def broker(self):
        return BrokerService()
    
    def test_pending_dispatch_initialization(self, broker):
        """Test that pending dispatches dict is initialized."""
        assert hasattr(broker, '_pending_tool_dispatches')
        assert isinstance(broker._pending_tool_dispatches, dict)
        assert len(broker._pending_tool_dispatches) == 0
    
    def test_dispatch_cleanup_on_result(self, broker):
        """Test that dispatches are cleaned up when results arrive."""
        exec_id = str(uuid4())
        broker._pending_tool_dispatches[exec_id] = {
            "requesting_agent_id": uuid4(),
            "target_agent_id": uuid4(),
            "tool_slug": "test_tool",
            "dispatched_at": datetime.utcnow(),
        }
        
        # Pop the dispatch info (simulating result handling)
        info = broker._pending_tool_dispatches.pop(exec_id, None)
        
        assert info is not None
        assert exec_id not in broker._pending_tool_dispatches


class TestToolDispatchTimeoutAndCleanup:
    """Tests for timeout and cleanup of pending tool dispatches."""
    
    @pytest.fixture
    def broker(self):
        return BrokerService()
    
    @pytest.fixture
    def mock_agent(self):
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        return ConnectedAgent(
            agent_id=uuid4(),
            websocket=ws,
            hostname="test-host",
            display_name="Test Agent",
            capabilities={}
        )
    
    @pytest.mark.asyncio
    async def test_cleanup_stale_dispatches(self, broker, mock_agent):
        """Test cleanup of timed-out dispatches."""
        broker._connected_agents[mock_agent.agent_id] = mock_agent
        
        # Create a stale dispatch (6 minutes old)
        exec_id = str(uuid4())
        stale_time = datetime.utcnow() - timedelta(minutes=6)
        broker._pending_tool_dispatches[exec_id] = {
            "requesting_agent_id": mock_agent.agent_id,
            "target_agent_id": uuid4(),
            "tool_slug": "slow_tool",
            "dispatched_at": stale_time,
        }
        
        # Create a fresh dispatch (1 minute old)
        fresh_exec_id = str(uuid4())
        broker._pending_tool_dispatches[fresh_exec_id] = {
            "requesting_agent_id": mock_agent.agent_id,
            "target_agent_id": uuid4(),
            "tool_slug": "fast_tool",
            "dispatched_at": datetime.utcnow() - timedelta(minutes=1),
        }
        
        # Run cleanup with 5 minute timeout
        await broker.cleanup_stale_tool_dispatches(timeout_seconds=300)
        
        # Stale dispatch should be cleaned up
        assert exec_id not in broker._pending_tool_dispatches
        
        # Fresh dispatch should still exist
        assert fresh_exec_id in broker._pending_tool_dispatches
        
        # Requesting agent should have received timeout notification
        mock_agent.websocket.send_json.assert_called_once()
        sent_data = mock_agent.websocket.send_json.call_args[0][0]
        assert sent_data["type"] == "tool_result"
        assert sent_data["data"]["result"]["success"] is False
        assert "timed out" in sent_data["data"]["result"]["error"]
        assert sent_data["data"]["result"]["timed_out"] is True
    
    @pytest.mark.asyncio
    async def test_cleanup_dispatches_for_disconnected_target(self, broker, mock_agent):
        """Test cleanup when target (executing) agent disconnects."""
        broker._connected_agents[mock_agent.agent_id] = mock_agent
        
        target_agent_id = uuid4()
        exec_id = str(uuid4())
        
        broker._pending_tool_dispatches[exec_id] = {
            "requesting_agent_id": mock_agent.agent_id,
            "target_agent_id": target_agent_id,
            "tool_slug": "web_search",
            "dispatched_at": datetime.utcnow(),
        }
        
        # Target agent disconnects
        await broker.cleanup_dispatches_for_agent(target_agent_id, role="target")
        
        # Dispatch should be cleaned up
        assert exec_id not in broker._pending_tool_dispatches
        
        # Requesting agent should be notified
        mock_agent.websocket.send_json.assert_called_once()
        sent_data = mock_agent.websocket.send_json.call_args[0][0]
        assert sent_data["type"] == "tool_result"
        assert sent_data["data"]["result"]["success"] is False
        assert "disconnected" in sent_data["data"]["result"]["error"]
        assert sent_data["data"]["result"]["agent_disconnected"] is True
    
    @pytest.mark.asyncio
    async def test_cleanup_dispatches_for_disconnected_requester(self, broker):
        """Test cleanup when requesting agent disconnects."""
        requesting_agent_id = uuid4()
        exec_id = str(uuid4())
        
        broker._pending_tool_dispatches[exec_id] = {
            "requesting_agent_id": requesting_agent_id,
            "target_agent_id": uuid4(),
            "tool_slug": "web_search",
            "dispatched_at": datetime.utcnow(),
        }
        
        # Requesting agent disconnects
        await broker.cleanup_dispatches_for_agent(requesting_agent_id, role="requesting")
        
        # Dispatch should be cleaned up (no notification needed)
        assert exec_id not in broker._pending_tool_dispatches
    
    @pytest.mark.asyncio
    async def test_no_cleanup_for_unrelated_agent(self, broker):
        """Test that cleanup doesn't affect unrelated dispatches."""
        exec_id = str(uuid4())
        broker._pending_tool_dispatches[exec_id] = {
            "requesting_agent_id": uuid4(),
            "target_agent_id": uuid4(),
            "tool_slug": "web_search",
            "dispatched_at": datetime.utcnow(),
        }
        
        # Unrelated agent disconnects
        unrelated_id = uuid4()
        await broker.cleanup_dispatches_for_agent(unrelated_id, role="target")
        await broker.cleanup_dispatches_for_agent(unrelated_id, role="requesting")
        
        # Dispatch should still exist
        assert exec_id in broker._pending_tool_dispatches
