"""
WebSocket client for communicating with the central broker.

Handles:
- Connection and authentication
- Automatic reconnection with exponential backoff
- Heartbeat messages
- Job reception and result reporting
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, Callable, Any
from enum import Enum

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from config import Config
from capabilities import detect_capabilities, get_live_stats, Capabilities


logger = logging.getLogger(__name__)


class MessageType(str, Enum):
    """Message types for broker protocol."""
    # Agent -> Broker (Resource Agent)
    REGISTER = "register"           # Initial registration with capabilities
    HEARTBEAT = "heartbeat"         # Periodic alive signal with stats
    JOB_ACCEPTED = "job_accepted"   # Acknowledge job receipt
    JOB_PROGRESS = "job_progress"   # Progress update
    JOB_COMPLETED = "job_completed" # Job finished successfully
    JOB_FAILED = "job_failed"       # Job failed with error
    
    # Agent -> Broker (Campaign Worker)
    WORKER_REGISTER = "worker_register"       # Register as campaign worker
    WORKER_HEARTBEAT = "worker_heartbeat"     # Campaign worker heartbeat with campaign IDs
    WORKER_DISCONNECT = "worker_disconnect"   # Graceful disconnect
    CAMPAIGN_ACCEPTED = "campaign_accepted"   # Acknowledge campaign assignment
    CAMPAIGN_RELEASE = "campaign_release"     # Release campaign lease
    CAMPAIGN_PROGRESS = "campaign_progress"   # Campaign progress update
    CAMPAIGN_RESPONSE = "campaign_response"   # LLM response for campaign
    CAMPAIGN_ERROR = "campaign_error"         # Campaign processing error
    CAMPAIGN_USER_INPUT_REQUEST = "campaign_user_input_request"  # Request user input
    TOOL_DISPATCH = "tool_dispatch"           # Request tool execution
    
    # Broker -> Agent
    REGISTERED = "registered"         # Registration confirmed
    JOB_ASSIGNED = "job_assigned"     # New job to execute
    JOB_CANCELLED = "job_cancelled"   # Cancel running job
    PING = "ping"                     # Keep-alive from broker
    
    # Broker -> Agent (Campaign Worker)
    WORKER_REGISTERED = "worker_registered"         # Worker registration confirmed
    CAMPAIGN_ASSIGNED = "campaign_assigned"         # Campaign assigned to worker
    CAMPAIGN_REVOKED = "campaign_revoked"           # Campaign taken away
    CAMPAIGN_USER_INPUT = "campaign_user_input"     # User message for campaign
    CAMPAIGN_STATE_UPDATE = "campaign_state_update" # State sync from backend
    TOOL_RESULT = "tool_result"                     # Tool execution result


class BrokerClient:
    """
    WebSocket client for broker communication.
    
    Usage:
        client = BrokerClient(config, job_handler)
        await client.run()
    """
    
    def __init__(
        self,
        config: Config,
        on_job: Callable[[dict], Any],
        on_cancel: Optional[Callable[[str], Any]] = None,
        campaign_processor = None,  # Optional CampaignProcessor instance
    ):
        """
        Initialize broker client.
        
        Args:
            config: Agent configuration
            on_job: Callback when job is assigned (async or sync)
            on_cancel: Callback when job is cancelled
            campaign_processor: Optional CampaignProcessor for campaign worker mode
        """
        self.config = config
        self.on_job = on_job
        self.on_cancel = on_cancel
        self.campaign_processor = campaign_processor
        
        self._websocket = None
        self._capabilities: Optional[Capabilities] = None
        self._reconnect_delay = config.broker.reconnect_delay
        self._running = False
        self._agent_id: Optional[str] = None  # Assigned by broker
        
    @property
    def is_connected(self) -> bool:
        return self._websocket is not None and not self._websocket.close_code
    
    async def run(self):
        """
        Main run loop - connects and maintains connection to broker.
        
        Automatically reconnects on disconnect with exponential backoff.
        """
        self._running = True
        
        while self._running:
            try:
                await self._connect_and_run()
            except ConnectionClosed as e:
                logger.warning(f"Connection closed: {e}")
            except Exception as e:
                logger.error(f"Connection error: {e}")
            
            if self._running:
                logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                # Exponential backoff
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    self.config.broker.max_reconnect_delay
                )
    
    async def stop(self):
        """Stop the client and close connection."""
        self._running = False
        if self._websocket:
            await self._websocket.close()
    
    async def _connect_and_run(self):
        """Connect to broker and handle messages."""
        url = self.config.broker.url
        logger.info(f"Connecting to broker: {url}")
        
        # GAP-2: Use first-message auth instead of query parameter
        # (prevents API key leakage in server/proxy logs)
        
        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5
        ) as ws:
            self._websocket = ws
            self._reconnect_delay = self.config.broker.reconnect_delay  # Reset backoff
            
            # Send auth message as first message
            await self._send({
                "type": "auth",
                "data": {"api_key": self.config.broker.api_key}
            })
            
            # Register with broker (as resource agent)
            await self._register()
            
            # Start campaign processor if enabled
            if self.campaign_processor and self.campaign_processor.is_enabled:
                # Give campaign processor access to send messages
                self.campaign_processor._send_message = self._send
                await self.campaign_processor.start()
            
            # Run message handler and heartbeat concurrently
            await asyncio.gather(
                self._handle_messages(),
                self._heartbeat_loop()
            )
    
    async def _register(self):
        """Send registration message with capabilities."""
        logger.info("Detecting capabilities...")
        self._capabilities = detect_capabilities(
            storage_paths=self.config.capabilities.storage_paths or None,
            config=self.config.campaign_worker if self.config.campaign_worker.enabled else None,
        )
        
        # Check GPU override
        if self.config.capabilities.gpu_enabled is False:
            self._capabilities.gpus = []
        
        message = {
            "type": MessageType.REGISTER.value,
            "data": {
                "name": self.config.agent.get_name(),
                "description": self.config.agent.description,
                "tags": self.config.agent.tags,
                "max_concurrent_jobs": self.config.capabilities.max_concurrent_jobs,
                "capabilities": self._capabilities.to_dict()
            }
        }
        
        await self._send(message)
        logger.info(f"Registration sent for agent: {self.config.agent.get_name()}")
        
        # Log capabilities summary
        logger.info(f"  CPU: {self._capabilities.cpu.model} ({self._capabilities.cpu.cores_logical} cores)")
        logger.info(f"  Memory: {self._capabilities.memory.total_gb:.1f} GB")
        if self._capabilities.has_gpu:
            for gpu in self._capabilities.gpus:
                logger.info(f"  GPU {gpu.index}: {gpu.name} ({gpu.memory_total_mb} MB)")
        if self._capabilities.has_ollama:
            logger.info(f"  Ollama: {len(self._capabilities.ollama.available_models)} models available")
        else:
            logger.info("  GPU: None detected")
    
    async def _handle_messages(self):
        """Handle incoming messages from broker."""
        async for message in self._websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type")
                
                if msg_type == MessageType.REGISTERED.value:
                    self._agent_id = data.get("data", {}).get("agent_id")
                    logger.info(f"Registered with broker, agent_id: {self._agent_id}")
                    
                elif msg_type == MessageType.JOB_ASSIGNED.value:
                    await self._handle_job_assigned(data.get("data", {}))
                    
                elif msg_type == MessageType.JOB_CANCELLED.value:
                    job_id = data.get("data", {}).get("job_id")
                    logger.info(f"Job cancelled: {job_id}")
                    if self.on_cancel:
                        if asyncio.iscoroutinefunction(self.on_cancel):
                            await self.on_cancel(job_id)
                        else:
                            self.on_cancel(job_id)
                            
                elif msg_type == MessageType.PING.value:
                    # Broker keep-alive, respond with heartbeat
                    await self._send_heartbeat()
                
                # Campaign Worker Messages
                elif msg_type == MessageType.WORKER_REGISTERED.value:
                    worker_id = data.get("data", {}).get("worker_id")
                    logger.info(f"Campaign worker registered: {worker_id}")
                    
                elif msg_type == MessageType.CAMPAIGN_ASSIGNED.value:
                    if self.campaign_processor:
                        await self.campaign_processor.handle_campaign_assigned(
                            data.get("data", {})
                        )
                    else:
                        logger.warning("Campaign assigned but no processor configured")
                        
                elif msg_type == MessageType.CAMPAIGN_REVOKED.value:
                    if self.campaign_processor:
                        await self.campaign_processor.handle_campaign_revoked(
                            data.get("data", {})
                        )
                        
                elif msg_type == MessageType.CAMPAIGN_USER_INPUT.value:
                    if self.campaign_processor:
                        await self.campaign_processor.handle_user_input(
                            data.get("data", {})
                        )
                        
                elif msg_type == MessageType.CAMPAIGN_STATE_UPDATE.value:
                    if self.campaign_processor:
                        await self.campaign_processor.handle_campaign_state_update(
                            data.get("data", {})
                        )
                        
                elif msg_type == MessageType.TOOL_RESULT.value:
                    if self.campaign_processor:
                        await self.campaign_processor.handle_tool_result(
                            data.get("data", {})
                        )
                    
                else:
                    logger.warning(f"Unknown message type: {msg_type}")
                    
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON: {message[:100]}")
            except Exception as e:
                logger.error(f"Error handling message: {e}")
    
    async def _handle_job_assigned(self, job_data: dict):
        """Handle job assignment from broker."""
        job_id = job_data.get("job_id")
        logger.info(f"Job assigned: {job_id}")
        
        # Acknowledge receipt
        await self._send({
            "type": MessageType.JOB_ACCEPTED.value,
            "data": {"job_id": job_id}
        })
        
        # Execute job in background task
        asyncio.create_task(self._execute_job(job_data))
    
    async def _execute_job(self, job_data: dict):
        """Execute a job and report result."""
        job_id = job_data.get("job_id")
        
        try:
            # Call job handler
            if asyncio.iscoroutinefunction(self.on_job):
                result = await self.on_job(job_data)
            else:
                result = self.on_job(job_data)
            
            # Report success
            await self.report_job_completed(job_id, result)
            
        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            await self.report_job_failed(job_id, str(e))
    
    async def _heartbeat_loop(self):
        """Send periodic heartbeats."""
        while self._running and self.is_connected:
            await asyncio.sleep(self.config.broker.heartbeat_interval)
            if self.is_connected:
                await self._send_heartbeat()
    
    async def _send_heartbeat(self):
        """Send heartbeat with live stats."""
        stats = get_live_stats()
        await self._send({
            "type": MessageType.HEARTBEAT.value,
            "data": {
                "agent_id": self._agent_id,
                "stats": stats
            }
        })
    
    async def report_job_progress(self, job_id: str, progress: float, message: str = ""):
        """
        Report job progress.
        
        Args:
            job_id: Job identifier
            progress: Progress percentage (0-100)
            message: Optional progress message
        """
        await self._send({
            "type": MessageType.JOB_PROGRESS.value,
            "data": {
                "job_id": job_id,
                "progress": progress,
                "message": message,
                "timestamp": datetime.utcnow().isoformat()
            }
        })
    
    async def report_job_completed(self, job_id: str, result: Any):
        """Report successful job completion."""
        await self._send({
            "type": MessageType.JOB_COMPLETED.value,
            "data": {
                "job_id": job_id,
                "result": result,
                "timestamp": datetime.utcnow().isoformat()
            }
        })
        logger.info(f"Job {job_id} completed successfully")
    
    async def report_job_failed(self, job_id: str, error: str):
        """Report job failure."""
        await self._send({
            "type": MessageType.JOB_FAILED.value,
            "data": {
                "job_id": job_id,
                "error": error,
                "timestamp": datetime.utcnow().isoformat()
            }
        })
        logger.error(f"Job {job_id} failed: {error}")
    
    async def _send(self, message: dict):
        """Send message to broker."""
        if self.is_connected:
            await self._websocket.send(json.dumps(message))
