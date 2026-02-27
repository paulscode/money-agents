"""
Resource Broker Service.

Manages remote agent connections, job routing, and communication.
"""
import asyncio
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional, Dict, Any, Set
from uuid import UUID, uuid4

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resource import (
    RemoteAgent, RemoteAgentStatus,
    Resource, JobQueue
)
from app.schemas.resource import JobStatus

logger = logging.getLogger(__name__)


class ConnectedAgent:
    """Tracks a connected agent's WebSocket and state."""
    
    def __init__(
        self,
        agent_id: UUID,
        websocket,
        hostname: str,
        display_name: Optional[str],
        capabilities: dict
    ):
        self.agent_id = agent_id
        self.websocket = websocket
        self.hostname = hostname  # Primary identifier
        self.display_name = display_name
        self.capabilities = capabilities
        self.connected_at = utc_now()
        self.last_heartbeat = utc_now()
        self.running_jobs: Set[UUID] = set()
        self.max_concurrent_jobs = capabilities.get("max_concurrent_jobs", 1)
        
        # Campaign worker state
        self.is_campaign_worker = False
        self.campaign_worker_id: Optional[str] = None
        self.held_campaigns: Set[UUID] = set()
        self.campaign_capacity = 0
    
    @property
    def name(self) -> str:
        """Display name, falling back to hostname."""
        return self.display_name or self.hostname
    
    @property
    def is_available(self) -> bool:
        """Check if agent can accept more jobs."""
        return len(self.running_jobs) < self.max_concurrent_jobs
    
    @property
    def is_campaign_available(self) -> bool:
        """Check if agent can accept more campaigns."""
        return (
            self.is_campaign_worker and
            len(self.held_campaigns) < self.campaign_capacity
        )
    
    @property
    def has_gpu(self) -> bool:
        """Check if agent has GPU capability."""
        gpus = self.capabilities.get("capabilities", {}).get("gpus", [])
        return len(gpus) > 0
    
    def get_gpu_names(self) -> list[str]:
        """Get list of GPU names on this agent."""
        gpus = self.capabilities.get("capabilities", {}).get("gpus", [])
        return [gpu.get("name", "Unknown GPU") for gpu in gpus]


class BrokerService:
    """
    Central broker for managing remote resource agents.
    
    Responsibilities:
    - Agent registration and authentication
    - Job routing based on capabilities and tool availability
    - Connection and heartbeat management
    - Result collection and forwarding
    - Campaign worker management and user input routing
    
    Key Design:
    - Agents are identified primarily by HOSTNAME (human-readable, stable)
    - Resources are scoped to agents via (agent_hostname, local_name)
    - Tools declare which agents they can run on (available_on_agents)
    - Tools declare per-agent resource requirements (agent_resource_map)
    - Campaign workers can manage campaigns with LLM processing
    """
    
    def __init__(self):
        self._connected_agents: Dict[UUID, ConnectedAgent] = {}
        self._hostname_to_agent: Dict[str, UUID] = {}  # hostname -> agent_id lookup
        self._pending_jobs: Dict[UUID, asyncio.Future] = {}  # job_id -> Future for result
        
        # Campaign worker tracking
        self._worker_id_to_agent: Dict[str, UUID] = {}  # worker_id -> agent_id
        self._campaign_to_worker: Dict[UUID, str] = {}  # campaign_id -> worker_id
        
        # Tool dispatch tracking (for cross-worker tool execution)
        self._pending_tool_dispatches: Dict[str, dict] = {}  # exec_id -> dispatch info
    
    # =========================================================================
    # API Key Management
    # =========================================================================
    
    @staticmethod
    def generate_api_key() -> str:
        """Generate a new API key for an agent."""
        return secrets.token_urlsafe(32)
    
    @staticmethod
    def hash_api_key(api_key: str) -> str:
        """Hash an API key for storage."""
        return hashlib.sha256(api_key.encode()).hexdigest()
    
    @staticmethod
    def verify_api_key(api_key: str, api_key_hash: str) -> bool:
        """Verify an API key against its hash."""
        return hmac.compare_digest(
            hashlib.sha256(api_key.encode()).hexdigest(),
            api_key_hash,
        )
    
    # =========================================================================
    # Agent Registration
    # =========================================================================
    
    async def create_agent(
        self,
        db: AsyncSession,
        hostname: str,
        display_name: str = None,
        description: str = "",
        tags: list[str] = None
    ) -> tuple[RemoteAgent, str]:
        """
        Create a new remote agent registration.
        
        Args:
            hostname: Primary identifier - should match the actual hostname
            display_name: Optional friendly name (falls back to hostname)
            description: Optional description
            tags: Optional searchable tags
        
        Returns:
            Tuple of (agent, api_key) - API key is only shown once!
        """
        # Generate API key
        api_key = self.generate_api_key()
        api_key_hash = self.hash_api_key(api_key)
        
        agent = RemoteAgent(
            id=uuid4(),
            hostname=hostname,
            display_name=display_name,
            api_key_hash=api_key_hash,
            description=description,
            tags=tags or [],
            status=RemoteAgentStatus.OFFLINE.value
        )
        
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        
        logger.info(f"Created remote agent: {hostname}")
        return agent, api_key
    
    async def authenticate_agent(
        self,
        db: AsyncSession,
        api_key: str
    ) -> Optional[RemoteAgent]:
        """
        Authenticate an agent by API key.
        
        Returns:
            RemoteAgent if valid, None if invalid.
        """
        api_key_hash = self.hash_api_key(api_key)
        
        result = await db.execute(
            select(RemoteAgent).where(
                and_(
                    RemoteAgent.api_key_hash == api_key_hash,
                    RemoteAgent.is_enabled == True
                )
            )
        )
        return result.scalar_one_or_none()
    
    # =========================================================================
    # Connection Management
    # =========================================================================
    
    async def agent_connected(
        self,
        db: AsyncSession,
        agent: RemoteAgent,
        websocket,
        capabilities: dict
    ) -> ConnectedAgent:
        """
        Handle agent connection.
        
        Updates database and adds to connected agents registry.
        The actual hostname from the connecting agent should match the registered hostname.
        """
        # Get reported hostname from capabilities
        reported_hostname = capabilities.get("capabilities", {}).get("network", {}).get("hostname")
        
        # Update agent record - note hostname is now the primary key and shouldn't change
        # but we update other fields
        agent.status = RemoteAgentStatus.ONLINE.value
        agent.capabilities = capabilities.get("capabilities")
        agent.max_concurrent_jobs = capabilities.get("max_concurrent_jobs", 1)
        agent.connected_at = utc_now()
        agent.last_seen_at = utc_now()
        agent.ip_address = capabilities.get("capabilities", {}).get("network", {}).get("ip_address")
        
        # Warn if reported hostname doesn't match registered hostname
        if reported_hostname and reported_hostname != agent.hostname:
            logger.warning(
                f"Agent {agent.hostname} reporting different hostname: {reported_hostname}. "
                f"Consider updating the registration."
            )
        
        await db.commit()
        
        # Create connected agent tracker
        connected = ConnectedAgent(
            agent_id=agent.id,
            websocket=websocket,
            hostname=agent.hostname,
            display_name=agent.display_name,
            capabilities=capabilities
        )
        self._connected_agents[agent.id] = connected
        self._hostname_to_agent[agent.hostname] = agent.id
        
        logger.info(f"Agent connected: {agent.hostname} (has_gpu={connected.has_gpu})")
        
        # Auto-create/update resources for this agent
        await self._sync_agent_resources(db, agent, capabilities)
        
        return connected
    
    async def agent_disconnected(
        self,
        db: AsyncSession,
        agent_id: UUID
    ):
        """Handle agent disconnection."""
        connected = self._connected_agents.pop(agent_id, None)
        
        if connected:
            # Remove from hostname lookup
            self._hostname_to_agent.pop(connected.hostname, None)
            
            logger.info(f"Agent disconnected: {connected.hostname}")
            
            # Clean up any pending tool dispatches involving this agent
            await self.cleanup_dispatches_for_agent(agent_id, role="target")
            await self.cleanup_dispatches_for_agent(agent_id, role="requesting")
            
            # Handle campaign worker cleanup
            if connected.is_campaign_worker and connected.campaign_worker_id:
                worker_id = connected.campaign_worker_id
                
                # Remove from worker lookup
                self._worker_id_to_agent.pop(worker_id, None)
                
                # Remove campaign -> worker mappings
                for campaign_id in list(connected.held_campaigns):
                    self._campaign_to_worker.pop(campaign_id, None)
                
                # Disconnect worker in database (releases campaigns with failover)
                try:
                    from app.services import campaign_worker_service
                    await campaign_worker_service.disconnect_worker(
                        db=db,
                        worker_id=worker_id,
                        release_campaigns=True,  # Will set status to PAUSED_FAILOVER
                    )
                    logger.info(f"Campaign worker {worker_id} disconnected, campaigns released")
                except Exception as e:
                    logger.error(f"Error disconnecting campaign worker: {e}")
            
            # Update database - mark agent offline
            await db.execute(
                update(RemoteAgent)
                .where(RemoteAgent.id == agent_id)
                .values(
                    status=RemoteAgentStatus.OFFLINE.value,
                    disconnected_at=utc_now()
                )
            )
            
            # Disable all resources belonging to this agent
            # (they will be re-enabled when the agent reconnects)
            await db.execute(
                update(Resource)
                .where(Resource.remote_agent_id == agent_id)
                .values(status="disabled")
            )
            logger.info(f"Disabled resources for disconnected agent: {connected.hostname}")
            
            await db.commit()
            
            # Mark any running jobs as failed
            for job_id in connected.running_jobs:
                await self._fail_job(
                    db,
                    job_id,
                    "Agent disconnected"
                )
    
    async def agent_heartbeat(
        self,
        db: AsyncSession,
        agent_id: UUID,
        stats: dict
    ):
        """Process heartbeat from agent."""
        connected = self._connected_agents.get(agent_id)
        if connected:
            connected.last_heartbeat = utc_now()
        
        # Update database
        await db.execute(
            update(RemoteAgent)
            .where(RemoteAgent.id == agent_id)
            .values(
                last_heartbeat_at=utc_now(),
                last_seen_at=utc_now(),
                live_stats=stats
            )
        )
        await db.commit()
    
    # =========================================================================
    # Job Routing
    # =========================================================================
    
    async def assign_job(
        self,
        db: AsyncSession,
        job: JobQueue,
        tool_config: dict
    ) -> Optional[UUID]:
        """
        Assign a job to a suitable remote agent.
        
        Returns:
            Agent ID if assigned, None if no suitable agent available.
        """
        # Check if resource is associated with a specific remote agent
        resource = await db.get(Resource, job.resource_id)
        if resource and resource.remote_agent_id:
            # Job must go to specific agent
            agent_id = resource.remote_agent_id
            connected = self._connected_agents.get(agent_id)
            
            if connected and connected.is_available:
                return await self._send_job_to_agent(db, job, connected, tool_config)
            else:
                return None  # Required agent not available
        
        # Find any suitable agent
        for agent_id, connected in self._connected_agents.items():
            if connected.is_available:
                # Check if job requires GPU
                requires_gpu = resource and resource.resource_type == "gpu"
                if requires_gpu and not connected.has_gpu:
                    continue
                
                return await self._send_job_to_agent(db, job, connected, tool_config)
        
        return None  # No suitable agent available
    
    async def _send_job_to_agent(
        self,
        db: AsyncSession,
        job: JobQueue,
        agent: ConnectedAgent,
        tool_config: dict
    ) -> UUID:
        """Send job to a specific agent."""
        import json
        
        # Build job data
        job_data = {
            "type": "job_assigned",
            "data": {
                "job_id": str(job.id),
                "tool_id": str(job.tool_id),
                "tool_slug": tool_config.get("slug", "unknown"),
                "execution_type": tool_config.get("interface_type", "rest_api"),
                "config": tool_config.get("interface_config", {}),
                "parameters": job.parameters or {},
                "timeout": tool_config.get("timeout_seconds", 300)
            }
        }
        
        # Send to agent
        await agent.websocket.send(json.dumps(job_data))
        
        # Track job
        agent.running_jobs.add(job.id)
        
        # Update job record
        job.remote_agent_id = agent.agent_id
        job.status = JobStatus.RUNNING.value
        job.started_at = utc_now()
        await db.commit()
        
        logger.info(f"Job {job.id} assigned to agent {agent.name}")
        return agent.agent_id
    
    # =========================================================================
    # Job Results
    # =========================================================================
    
    async def job_accepted(
        self,
        db: AsyncSession,
        agent_id: UUID,
        job_id: UUID
    ):
        """Handle job acceptance from agent."""
        logger.debug(f"Job {job_id} accepted by agent")
    
    async def job_progress(
        self,
        db: AsyncSession,
        agent_id: UUID,
        job_id: UUID,
        progress: float,
        message: str = ""
    ):
        """Handle job progress update."""
        # Could emit WebSocket event to frontend here
        logger.debug(f"Job {job_id} progress: {progress}% - {message}")
    
    async def job_completed(
        self,
        db: AsyncSession,
        agent_id: UUID,
        job_id: UUID,
        result: Any
    ):
        """Handle job completion from agent."""
        connected = self._connected_agents.get(agent_id)
        if connected:
            connected.running_jobs.discard(job_id)
        
        job_id_str = str(job_id)
        
        # Check if this is a dispatched tool result (from cross-worker dispatch)
        if job_id_str in self._pending_tool_dispatches:
            await self.handle_dispatched_tool_result(
                db, agent_id, job_id_str, 
                {"success": True, "output": result}
            )
            return
        
        # Regular job queue handling
        job_id_uuid = UUID(job_id) if isinstance(job_id, str) else job_id
        result_any = await db.execute(
            select(JobQueue).where(JobQueue.id == job_id_uuid)
        )
        job = result_any.scalar_one_or_none()
        
        if job:
            job.status = JobStatus.COMPLETED.value
            job.result = result
            job.completed_at = utc_now()
            await db.commit()
        
        # Resolve any pending future
        future = self._pending_jobs.pop(job_id_uuid, None)
        if future and not future.done():
            future.set_result(result)
        
        logger.info(f"Job {job_id} completed")
    
    async def job_failed(
        self,
        db: AsyncSession,
        agent_id: UUID,
        job_id: UUID,
        error: str
    ):
        """Handle job failure from agent."""
        connected = self._connected_agents.get(agent_id)
        if connected:
            connected.running_jobs.discard(job_id)
        
        job_id_str = str(job_id)
        
        # Check if this is a dispatched tool failure (from cross-worker dispatch)
        if job_id_str in self._pending_tool_dispatches:
            await self.handle_dispatched_tool_result(
                db, agent_id, job_id_str,
                {"success": False, "error": error}
            )
            return
        
        await self._fail_job(db, job_id, error)
    
    async def _fail_job(
        self,
        db: AsyncSession,
        job_id: UUID,
        error: str
    ):
        """Mark job as failed."""
        job_id_uuid = UUID(job_id) if isinstance(job_id, str) else job_id
        
        result = await db.execute(
            select(JobQueue).where(JobQueue.id == job_id_uuid)
        )
        job = result.scalar_one_or_none()
        
        if job:
            job.status = JobStatus.FAILED.value
            job.error = error
            job.completed_at = utc_now()
            await db.commit()
        
        # Reject any pending future
        future = self._pending_jobs.pop(job_id_uuid, None)
        if future and not future.done():
            future.set_exception(Exception(error))
        
        logger.error(f"Job {job_id} failed: {error}")
    
    # =========================================================================
    # Resource Sync
    # =========================================================================
    
    async def _sync_agent_resources(
        self,
        db: AsyncSession,
        agent: RemoteAgent,
        capabilities: dict
    ):
        """
        Create or update resources based on agent capabilities.
        
        Auto-creates GPU, CPU, and storage resources for agents.
        Resources are scoped to the agent via (agent_hostname, local_name).
        Names include hostname prefix for uniqueness, followed by descriptive info:
        - GPU: "MyPC: GPU-0 (NVIDIA GeForce RTX 3090)"
        - CPU: "MyPC: CPU (Intel Core i9)"
        - Storage: "MyPC: Storage (500 GB) /home"
        """
        caps = capabilities.get("capabilities", {})
        hostname = agent.hostname
        
        # Sync GPU resources
        gpus = caps.get("gpus", [])
        for i, gpu in enumerate(gpus):
            gpu_index = gpu.get('index', i)
            gpu_name = gpu.get("name", "Unknown GPU")
            local_name = f"gpu-{gpu_index}"
            # Name like: "MyPC: GPU-0 (NVIDIA GeForce RTX 3090)"
            display_name = f"{hostname}: GPU-{gpu_index} ({gpu_name})"
            
            resource = await self._get_or_create_agent_resource(
                db, agent, local_name, display_name, "gpu", "compute"
            )
            resource.resource_metadata = {
                "gpu_name": gpu_name,
                "memory_mb": gpu.get("memory_total_mb"),
                "driver": gpu.get("driver_version"),
                "cuda": gpu.get("cuda_version")
            }
            resource.status = "available"
        
        # Sync CPU resource (one per agent)
        cpu_info = caps.get("cpu", {})
        if cpu_info:
            local_name = "cpu"
            cpu_model = cpu_info.get("model", "Unknown CPU")
            # Name like: "MyPC: CPU (Intel(R) Core(TM) i9 CPU @ 3.60GHz)"
            display_name = f"{hostname}: CPU ({cpu_model})"
            
            resource = await self._get_or_create_agent_resource(
                db, agent, local_name, display_name, "cpu", "compute"
            )
            resource.resource_metadata = {
                "model": cpu_model,
                "cores_physical": cpu_info.get("cores_physical"),
                "cores_logical": cpu_info.get("cores_logical"),
                "architecture": cpu_info.get("architecture"),
                "frequency_mhz": cpu_info.get("frequency_mhz")
            }
            resource.status = "available"
        
        # Sync storage resources
        storage_list = caps.get("storage", [])
        for i, storage in enumerate(storage_list):
            path = storage.get("path", f"storage-{i}")
            total_bytes = storage.get("total_bytes", 0)
            
            # Create a safe local name from path
            safe_path = path.strip("/").replace("/", "-").replace("\\", "-") or f"storage-{i}"
            local_name = f"storage-{safe_path}"
            
            # Format size (GB or TB)
            total_gb = total_bytes / (1024**3) if total_bytes else 0
            if total_gb >= 1000:
                size_str = f"{total_gb / 1024:.1f} TB"
            else:
                size_str = f"{int(total_gb)} GB"
            
            # Name like: "MyPC: Storage (500 GB) /home"
            display_name = f"{hostname}: Storage ({size_str}) {path}"
            
            resource = await self._get_or_create_agent_resource(
                db, agent, local_name, display_name, "storage", "capacity"
            )
            resource.resource_metadata = {
                "path": path,
                "total_bytes": total_bytes,
                "free_bytes": storage.get("free_bytes"),
                "filesystem": storage.get("filesystem"),
                "last_synced": utc_now().isoformat()
            }
            resource.status = "available"
        
        await db.commit()
    
    async def _get_or_create_agent_resource(
        self,
        db: AsyncSession,
        agent: RemoteAgent,
        local_name: str,
        display_name: str,
        resource_type: str,
        category: str
    ) -> Resource:
        """
        Get existing agent resource or create new one.
        
        Args:
            local_name: Stable identifier like "gpu-0", "cpu", "storage-C:-"
            display_name: Human-readable name like "GPU-0 (NVIDIA GeForce RTX 4090)"
        
        Returns the resource (either existing or newly created).
        """
        result = await db.execute(
            select(Resource).where(
                and_(
                    Resource.agent_hostname == agent.hostname,
                    Resource.local_name == local_name
                )
            )
        )
        resource = result.scalar_one_or_none()
        
        if not resource:
            resource = Resource(
                id=uuid4(),
                name=display_name,
                local_name=local_name,
                resource_type=resource_type,
                category=category,
                status="available",
                is_system_resource=True,
                remote_agent_id=agent.id,
                agent_hostname=agent.hostname,
                resource_metadata={}
            )
            db.add(resource)
            logger.info(f"Created {resource_type} resource: {display_name} ({agent.hostname})")
        else:
            # Update display name in case hardware changed or for consistency
            resource.name = display_name
        
        return resource
    
    # =========================================================================
    # Hostname-based Lookups
    # =========================================================================
    
    def get_agent_by_hostname(self, hostname: str) -> Optional[ConnectedAgent]:
        """Get a connected agent by hostname."""
        agent_id = self._hostname_to_agent.get(hostname)
        if agent_id:
            return self._connected_agents.get(agent_id)
        return None
    
    def is_agent_online(self, hostname: str) -> bool:
        """Check if an agent is currently connected."""
        return hostname in self._hostname_to_agent
    
    def get_online_agent_hostnames(self) -> list[str]:
        """Get list of currently connected agent hostnames."""
        return list(self._hostname_to_agent.keys())
    
    async def get_online_agents_for_tool(
        self,
        db: AsyncSession,
        tool
    ) -> list[ConnectedAgent]:
        """
        Get list of online agents that can run the given tool.
        
        Args:
            tool: Tool model instance with available_on_agents field
        
        Returns:
            List of ConnectedAgent instances
        """
        if not tool.is_distributed():
            return []  # Local-only tool
        
        available = tool.get_available_agent_hostnames()
        if not available:
            return []  # Explicitly disabled
        
        online_agents = []
        for hostname, agent_id in self._hostname_to_agent.items():
            connected = self._connected_agents.get(agent_id)
            if not connected:
                continue
            
            if "*" in available or hostname in available:
                online_agents.append(connected)
        
        return online_agents
    
    async def check_resources_available_for_tool(
        self,
        db: AsyncSession,
        tool,
        hostname: str
    ) -> bool:
        """
        Check if all required resources for a tool are available on an agent.
        
        Args:
            tool: Tool model instance
            hostname: Agent hostname
        
        Returns:
            True if all required resources are available (not in_use)
        """
        required_resources = tool.get_required_resources_for_agent(hostname)
        if not required_resources:
            return True  # No resource requirements
        
        for local_name in required_resources:
            result = await db.execute(
                select(Resource).where(
                    and_(
                        Resource.agent_hostname == hostname,
                        Resource.local_name == local_name
                    )
                )
            )
            resource = result.scalar_one_or_none()
            
            if not resource:
                logger.warning(f"Required resource {hostname}/{local_name} not found")
                return False
            
            if resource.status != "available":
                logger.debug(f"Resource {hostname}/{local_name} is {resource.status}")
                return False
        
        return True
    
    async def lock_resources_for_tool(
        self,
        db: AsyncSession,
        tool,
        hostname: str
    ) -> list[Resource]:
        """
        Lock all required resources for a tool on an agent.
        
        Args:
            tool: Tool model instance
            hostname: Agent hostname
        
        Returns:
            List of locked Resource instances
        """
        required_resources = tool.get_required_resources_for_agent(hostname)
        locked = []
        
        for local_name in required_resources:
            result = await db.execute(
                select(Resource).where(
                    and_(
                        Resource.agent_hostname == hostname,
                        Resource.local_name == local_name,
                        Resource.status == "available"
                    )
                )
            )
            resource = result.scalar_one_or_none()
            
            if resource:
                resource.status = "in_use"
                locked.append(resource)
            else:
                # Rollback locks
                for r in locked:
                    r.status = "available"
                return []
        
        await db.commit()
        return locked
    
    async def release_resources(
        self,
        db: AsyncSession,
        resources: list[Resource]
    ):
        """Release locked resources."""
        for resource in resources:
            resource.status = "available"
        await db.commit()
    
    # =========================================================================
    # Health & Status
    # =========================================================================
    
    def get_connected_agents(self) -> list[dict]:
        """Get list of currently connected agents."""
        return [
            {
                "agent_id": str(agent.agent_id),
                "hostname": agent.hostname,
                "display_name": agent.display_name,
                "name": agent.name,
                "connected_at": agent.connected_at.isoformat(),
                "last_heartbeat": agent.last_heartbeat.isoformat(),
                "running_jobs": len(agent.running_jobs),
                "max_concurrent_jobs": agent.max_concurrent_jobs,
                "is_available": agent.is_available,
                "has_gpu": agent.has_gpu,
                "gpu_names": agent.get_gpu_names(),
                # Campaign worker info
                "is_campaign_worker": agent.is_campaign_worker,
                "campaign_worker_id": agent.campaign_worker_id,
                "campaign_capacity": agent.campaign_capacity,
                "held_campaigns": len(agent.held_campaigns),
                "is_campaign_available": agent.is_campaign_available,
            }
            for agent in self._connected_agents.values()
        ]
    
    async def check_stale_connections(self, timeout_seconds: int = 60):
        """Check for agents that haven't sent heartbeat recently."""
        cutoff = utc_now() - timedelta(seconds=timeout_seconds)
        stale_agents = []
        
        for agent_id, connected in list(self._connected_agents.items()):
            if connected.last_heartbeat < cutoff:
                stale_agents.append(agent_id)
        
        return stale_agents
    
    # =========================================================================
    # Campaign Worker Management
    # =========================================================================
    
    async def register_campaign_worker(
        self,
        db: AsyncSession,
        agent_id: UUID,
        worker_data: dict
    ):
        """
        Register a remote agent as a campaign worker.
        
        This is called when a remote agent sends a worker_register message,
        indicating it wants to participate in distributed campaign processing.
        """
        from app.services import campaign_worker_service
        
        connected = self._connected_agents.get(agent_id)
        if not connected:
            logger.error(f"Worker registration for unknown agent: {agent_id}")
            return
        
        worker_id = worker_data.get("worker_id")
        max_campaigns = worker_data.get("max_campaigns", 3)
        hostname = worker_data.get("hostname", connected.hostname)
        
        # Update connected agent state
        connected.is_campaign_worker = True
        connected.campaign_worker_id = worker_id
        connected.campaign_capacity = max_campaigns
        
        # Register in worker_id -> agent_id lookup
        self._worker_id_to_agent[worker_id] = agent_id
        
        # Register in campaign_workers table
        try:
            worker = await campaign_worker_service.register_worker(
                db=db,
                hostname=hostname,
                worker_type="remote",
                remote_agent_id=agent_id,
                campaign_capacity=max_campaigns,
            )
            
            logger.info(
                f"Campaign worker registered: {worker_id} "
                f"(capacity={max_campaigns}, agent={connected.hostname})"
            )
            
            # Send confirmation
            await connected.websocket.send_json({
                "type": "worker_registered",
                "data": {
                    "worker_id": worker_id,
                    "campaign_capacity": max_campaigns,
                    "message": f"Campaign worker {worker_id} registered"
                }
            })
            
        except Exception as e:
            logger.error(f"Failed to register campaign worker: {e}")
    
    async def campaign_worker_heartbeat(
        self,
        db: AsyncSession,
        agent_id: UUID,
        heartbeat_data: dict
    ):
        """Process heartbeat from a campaign worker."""
        from app.services import campaign_worker_service
        
        worker_id = heartbeat_data.get("worker_id")
        campaign_ids = heartbeat_data.get("campaign_ids", [])
        available_slots = heartbeat_data.get("available_slots", 0)
        
        connected = self._connected_agents.get(agent_id)
        if not connected:
            logger.error(f"Heartbeat from unknown agent: {agent_id}")
            return
            
        # Auto-register as campaign worker if not already registered
        # This handles reconnects after backend restart
        if not connected.is_campaign_worker and worker_id:
            logger.info(f"Auto-registering campaign worker {worker_id} from heartbeat")
            connected.is_campaign_worker = True
            connected.campaign_worker_id = worker_id
            connected.campaign_capacity = heartbeat_data.get("max_campaigns", len(campaign_ids) + available_slots)
            self._worker_id_to_agent[worker_id] = agent_id
            
        connected.last_heartbeat = utc_now()
        # Update held campaigns
        connected.held_campaigns = {UUID(c) if isinstance(c, str) else c for c in campaign_ids}
        
        # Update worker service
        try:
            await campaign_worker_service.update_worker_heartbeat(
                db=db,
                worker_id=worker_id,
                campaign_ids=[UUID(c) if isinstance(c, str) else c for c in campaign_ids],
            )
        except Exception as e:
            logger.error(f"Worker heartbeat error: {e}")
    
    async def campaign_worker_disconnect(
        self,
        db: AsyncSession,
        agent_id: UUID,
        disconnect_data: dict
    ):
        """Handle graceful disconnect from campaign worker."""
        from app.services import campaign_worker_service
        
        worker_id = disconnect_data.get("worker_id")
        graceful = disconnect_data.get("graceful", True)
        
        connected = self._connected_agents.get(agent_id)
        if connected:
            connected.is_campaign_worker = False
            connected.campaign_worker_id = None
            connected.held_campaigns.clear()
        
        # Remove from lookup
        self._worker_id_to_agent.pop(worker_id, None)
        
        # Update database
        try:
            await campaign_worker_service.disconnect_worker(
                db=db,
                worker_id=worker_id,
                release_campaigns=not graceful,
            )
            logger.info(f"Campaign worker disconnected: {worker_id} (graceful={graceful})")
        except Exception as e:
            logger.error(f"Worker disconnect error: {e}")
    
    async def campaign_accepted(
        self,
        db: AsyncSession,
        agent_id: UUID,
        accept_data: dict
    ):
        """Handle campaign acceptance from worker."""
        from app.services import campaign_lease_service
        
        campaign_id = accept_data.get("campaign_id")
        worker_id = accept_data.get("worker_id")
        
        logger.debug(f"Campaign {campaign_id} accepted by {worker_id}")
        
        cid = UUID(campaign_id) if isinstance(campaign_id, str) else campaign_id
        
        # Acquire lease in database
        try:
            await campaign_lease_service.acquire_lease(
                db=db,
                worker_id=worker_id,
                campaign_id=cid,
                ttl_seconds=300,  # 5 minute lease
            )
            logger.info(f"Lease acquired for campaign {campaign_id} by {worker_id}")
        except Exception as e:
            logger.error(f"Failed to acquire lease for campaign {campaign_id}: {e}")
            # Still track in memory even if DB fails
        
        # Update in-memory state
        connected = self._connected_agents.get(agent_id)
        if connected:
            connected.held_campaigns.add(cid)
            self._campaign_to_worker[cid] = worker_id
    
    async def campaign_release(
        self,
        db: AsyncSession,
        agent_id: UUID,
        release_data: dict
    ):
        """Handle campaign release from worker."""
        from app.services import campaign_lease_service
        
        campaign_id = release_data.get("campaign_id")
        worker_id = release_data.get("worker_id")
        new_status = release_data.get("new_status")
        reason = release_data.get("reason", "release")
        
        cid = UUID(campaign_id) if isinstance(campaign_id, str) else campaign_id
        
        # Update connected state
        connected = self._connected_agents.get(agent_id)
        if connected:
            connected.held_campaigns.discard(cid)
        
        # Update campaign lookup
        self._campaign_to_worker.pop(cid, None)
        
        # Release lease in database
        try:
            await campaign_lease_service.release_lease(
                db=db,
                worker_id=worker_id,
                campaign_id=cid,
                reason=reason,
                new_status=new_status,
            )
            logger.info(f"Campaign {campaign_id} released by {worker_id} ({reason})")
        except Exception as e:
            logger.error(f"Campaign release error: {e}")
    
    async def campaign_progress(
        self,
        db: AsyncSession,
        agent_id: UUID,
        progress_data: dict
    ):
        """Handle campaign progress update from worker."""
        campaign_id = progress_data.get("campaign_id")
        phase = progress_data.get("phase")
        message = progress_data.get("message", "")
        
        # Could emit WebSocket event to frontend here
        logger.debug(f"Campaign {campaign_id} progress: {phase} - {message}")
        
        # TODO: Store progress event in database if needed
    
    async def campaign_response(
        self,
        db: AsyncSession,
        agent_id: UUID,
        response_data: dict
    ):
        """Handle LLM response from campaign worker."""
        from app.models import Message, SenderType, Conversation, ConversationType
        from app.services.usage_service import calculate_cost
        from sqlalchemy import select
        
        campaign_id = response_data.get("campaign_id")
        content = response_data.get("content", "")
        message_type = response_data.get("message_type", "agent")
        tokens_used = response_data.get("tokens_used", 0)
        model_used = response_data.get("model_used", "")
        provider_used = response_data.get("provider_used", "")
        
        # Enhanced token tracking
        prompt_tokens = response_data.get("prompt_tokens", 0)
        completion_tokens = response_data.get("completion_tokens", 0)
        
        # Calculate cost if we have detailed token info
        cost_usd = None
        if model_used and prompt_tokens > 0:
            cost_usd = calculate_cost(model_used, prompt_tokens, completion_tokens)
        
        cid = UUID(campaign_id) if isinstance(campaign_id, str) else campaign_id
        
        # Find or create the campaign's conversation
        result = await db.execute(
            select(Conversation).where(
                Conversation.conversation_type == ConversationType.CAMPAIGN,
                Conversation.related_id == cid
            )
        )
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            # Get campaign to find user
            from app.models import Campaign
            camp_result = await db.execute(
                select(Campaign).where(Campaign.id == cid)
            )
            campaign = camp_result.scalar_one_or_none()
            
            if not campaign:
                logger.error(f"Campaign {campaign_id} not found for response")
                return
            
            # Create conversation for this campaign
            conversation = Conversation(
                created_by_user_id=campaign.user_id,
                conversation_type=ConversationType.CAMPAIGN,
                related_id=cid,
                title=f"Campaign {cid}",
            )
            db.add(conversation)
            await db.flush()  # Get the ID
            logger.info(f"Created conversation for campaign {campaign_id}")
        
        # Store message in conversation with enhanced token tracking
        message = Message(
            conversation_id=conversation.id,
            content=content,
            sender_type=SenderType.AGENT,
            tokens_used=tokens_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            model_used=model_used,
            meta_data={
                "provider": provider_used,
                "source": "remote_worker",
                "latency_ms": response_data.get("latency_ms"),
            } if provider_used else {"source": "remote_worker"},
        )
        db.add(message)
        await db.commit()
        
        logger.info(
            f"Stored campaign response: campaign={campaign_id}, "
            f"tokens={tokens_used} (prompt={prompt_tokens}, completion={completion_tokens}), "
            f"model={model_used}, provider={provider_used}, cost=${cost_usd or 0:.6f}"
        )
    
    async def campaign_user_input_request(
        self,
        db: AsyncSession,
        agent_id: UUID,
        request_data: dict
    ):
        """
        Handle user input request from campaign worker.
        
        The worker is asking the user a question as part of campaign processing.
        We store this as a message and the UI will display it for user response.
        """
        from app.models import Message, SenderType, Conversation, ConversationType
        from sqlalchemy import select
        
        campaign_id = request_data.get("campaign_id")
        # Accept both "message" (what agent sends) and "prompt" (old field name)
        message_content = request_data.get("message") or request_data.get("prompt", "")
        context = request_data.get("context", "")
        priority = request_data.get("priority", "medium")
        
        cid = UUID(campaign_id) if isinstance(campaign_id, str) else campaign_id
        
        # Find or create the campaign's conversation
        result = await db.execute(
            select(Conversation).where(
                Conversation.conversation_type == ConversationType.CAMPAIGN,
                Conversation.related_id == cid
            )
        )
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            # Get campaign to find user
            from app.models import Campaign
            camp_result = await db.execute(
                select(Campaign).where(Campaign.id == cid)
            )
            campaign = camp_result.scalar_one_or_none()
            
            if not campaign:
                logger.error(f"Campaign {campaign_id} not found for user input request")
                return
            
            # Create conversation for this campaign
            conversation = Conversation(
                created_by_user_id=campaign.user_id,
                conversation_type=ConversationType.CAMPAIGN,
                related_id=cid,
                title=f"Campaign {cid}",
            )
            db.add(conversation)
            await db.flush()
            logger.info(f"Created conversation for campaign {campaign_id}")
        
        # Store the agent's question as a message
        message = Message(
            conversation_id=conversation.id,
            content=message_content,
            sender_type=SenderType.AGENT,
            meta_data={
                "context": context,
                "priority": priority,
                "source": "remote_worker",
                "awaiting_input": True,
            },
        )
        db.add(message)
        await db.commit()
        
        logger.info(f"User input requested for campaign {campaign_id}: {message_content[:100]}...")
    
    async def campaign_error(
        self,
        db: AsyncSession,
        agent_id: UUID,
        error_data: dict
    ):
        """Handle campaign error from worker."""
        campaign_id = error_data.get("campaign_id")
        error_msg = error_data.get("error", "Unknown error")
        
        logger.error(f"Campaign {campaign_id} error: {error_msg}")
        
        # TODO: Update campaign status or create error event
    
    async def campaign_tool_dispatch(
        self,
        db: AsyncSession,
        agent_id: UUID,
        dispatch_data: dict
    ):
        """
        Handle tool dispatch request from campaign worker.
        
        The worker needs to execute a tool as part of campaign processing.
        We find the appropriate agent to run the tool and dispatch the job.
        
        Flow:
        1. Look up tool by slug
        2. Find available agents with capability
        3. Check resource availability
        4. Route job to best agent OR execute locally
        5. Track pending execution for result routing
        """
        from app.models import Tool
        
        exec_id = dispatch_data.get("execution_id")
        worker_id = dispatch_data.get("worker_id")
        campaign_id = dispatch_data.get("campaign_id")
        tool_slug = dispatch_data.get("tool_slug")
        params = dispatch_data.get("params", {})
        
        logger.info(f"Tool dispatch: {tool_slug} for campaign {campaign_id} (exec_id: {exec_id})")
        
        requesting_agent = self._connected_agents.get(agent_id)
        if not requesting_agent:
            logger.error(f"Requesting agent {agent_id} not found")
            return
        
        # Look up the tool
        result = await db.execute(
            select(Tool).where(Tool.slug == tool_slug)
        )
        tool = result.scalar_one_or_none()
        
        if not tool:
            await self._send_tool_result(requesting_agent, exec_id, {
                "success": False,
                "error": f"Tool not found: {tool_slug}",
            })
            return
        
        # Check if tool is distributed or local-only
        if not tool.is_distributed():
            # Execute locally via ToolExecutor
            await self._execute_tool_locally(
                db, requesting_agent, exec_id, tool, params
            )
            return
        
        # Find available agents for this tool
        available_agents = await self.get_online_agents_for_tool(db, tool)
        
        if not available_agents:
            # No remote agents - try local execution
            logger.info(f"No remote agents for {tool_slug}, trying local execution")
            await self._execute_tool_locally(
                db, requesting_agent, exec_id, tool, params
            )
            return
        
        # Find best agent (prefer: available resources, lower load)
        # Build list of candidates with their load scores
        candidates = []
        for candidate in available_agents:
            # Skip the requesting agent to avoid self-dispatch loops
            if candidate.agent_id == agent_id:
                continue
            
            # Check if agent is available
            if not candidate.is_available:
                continue
            
            # Check resource availability
            resources_ok = await self.check_resources_available_for_tool(
                db, tool, candidate.hostname
            )
            if resources_ok:
                # Calculate load score (lower is better)
                # running_jobs: current jobs
                # max_concurrent_jobs: capacity
                running = len(candidate.running_jobs)
                max_jobs = candidate.capabilities.get("max_concurrent_jobs", 1)
                load_ratio = running / max(max_jobs, 1)
                candidates.append((candidate, load_ratio))
        
        # Sort by load ratio (least loaded first)
        candidates.sort(key=lambda x: x[1])
        
        best_agent = candidates[0][0] if candidates else None
        
        if not best_agent:
            # No suitable remote agent - try local execution
            logger.info(f"No suitable agent for {tool_slug}, trying local execution")
            await self._execute_tool_locally(
                db, requesting_agent, exec_id, tool, params
            )
            return
        
        logger.info(f"Selected agent {best_agent.hostname} for {tool_slug} (load: {candidates[0][1]:.2f})")
        
        # Dispatch to remote agent
        await self._dispatch_tool_to_agent(
            db, requesting_agent, exec_id, tool, params, best_agent
        )
    
    async def _send_tool_result(
        self,
        requesting_agent: ConnectedAgent,
        exec_id: str,
        result: dict
    ):
        """Send tool execution result back to requesting agent."""
        await requesting_agent.websocket.send_json({
            "type": "tool_result",
            "data": {
                "execution_id": exec_id,
                "result": result,
            }
        })
    
    async def _execute_tool_locally(
        self,
        db: AsyncSession,
        requesting_agent: ConnectedAgent,
        exec_id: str,
        tool,
        params: dict
    ):
        """Execute tool locally and return result to requesting agent."""
        from app.services.tool_execution_service import tool_executor
        
        logger.info(f"Executing tool {tool.slug} locally for exec_id {exec_id}")
        
        try:
            # GPU VRAM eviction — if this tool uses GPU resources, clear other tenants
            resource_ids = tool.resource_ids or []
            if resource_ids:
                try:
                    from app.services.gpu_lifecycle_service import get_gpu_lifecycle_service
                    gpu_service = get_gpu_lifecycle_service()
                    eviction_result = await gpu_service.prepare_gpu_for_tool(tool.slug)
                    logger.info(f"GPU eviction for {tool.slug}: {eviction_result}")
                    service_ready = await gpu_service.ensure_service_running(tool.slug)
                    if not service_ready:
                        logger.warning(f"Target service for {tool.slug} may not be ready")
                except Exception as e:
                    logger.warning(f"GPU lifecycle preparation failed for {tool.slug}: {e}")

            result = await tool_executor.execute(tool, params)
            await self._send_tool_result(requesting_agent, exec_id, {
                "success": result.success,
                "output": result.output,
                "error": result.error,
                "duration_ms": result.duration_ms,
                "executed_by": "local",
            })
        except Exception as e:
            logger.exception(f"Local tool execution failed: {e}")
            await self._send_tool_result(requesting_agent, exec_id, {
                "success": False,
                "error": str(e),
                "executed_by": "local",
            })
    
    async def _dispatch_tool_to_agent(
        self,
        db: AsyncSession,
        requesting_agent: ConnectedAgent,
        exec_id: str,
        tool,
        params: dict,
        target_agent: ConnectedAgent
    ):
        """Dispatch tool execution to a remote agent."""
        logger.info(f"Dispatching tool {tool.slug} to agent {target_agent.hostname} (exec_id: {exec_id})")
        
        # Track pending execution for result routing
        self._pending_tool_dispatches[exec_id] = {
            "requesting_agent_id": requesting_agent.agent_id,
            "target_agent_id": target_agent.agent_id,
            "tool_slug": tool.slug,
            "dispatched_at": utc_now(),
        }
        
        # Build job data for target agent
        job_data = {
            "type": "job_assigned",
            "data": {
                "job_id": exec_id,  # Use exec_id as job_id for tracking
                "tool_id": str(tool.id),
                "tool_slug": tool.slug,
                "execution_type": tool.interface_type or "rest_api",
                "config": tool.interface_config or {},
                "parameters": params,
                "timeout": tool.timeout_seconds or 300,
                "is_campaign_dispatch": True,  # Flag for special handling
            }
        }
        
        # Send to target agent
        import json
        await target_agent.websocket.send(json.dumps(job_data))
        target_agent.running_jobs.add(UUID(exec_id) if isinstance(exec_id, str) else exec_id)
        
        logger.info(f"Tool {tool.slug} dispatched to {target_agent.hostname}")
    
    async def handle_dispatched_tool_result(
        self,
        db: AsyncSession,
        target_agent_id: UUID,
        job_id: str,
        result: dict
    ):
        """
        Handle result from a dispatched tool execution.
        
        Routes the result back to the original requesting agent.
        """
        dispatch_info = self._pending_tool_dispatches.pop(job_id, None)
        
        if not dispatch_info:
            logger.warning(f"No dispatch info for job {job_id}")
            return
        
        requesting_agent_id = dispatch_info["requesting_agent_id"]
        requesting_agent = self._connected_agents.get(requesting_agent_id)
        
        if not requesting_agent:
            logger.warning(f"Requesting agent {requesting_agent_id} no longer connected")
            return
        
        # Add execution metadata
        result["executed_by"] = dispatch_info.get("target_agent_id")
        result["tool_slug"] = dispatch_info.get("tool_slug")
        
        await self._send_tool_result(requesting_agent, job_id, result)
    
    async def cleanup_stale_tool_dispatches(
        self,
        timeout_seconds: int = 300
    ):
        """
        Clean up tool dispatches that have been pending too long.
        
        Called periodically to handle cases where:
        - Target agent disconnected without completing
        - Tool execution hung without response
        - Network issues prevented result delivery
        
        Args:
            timeout_seconds: How long before a dispatch is considered stale (default 5 min)
        """
        now = utc_now()
        stale_dispatches = []
        
        for exec_id, info in self._pending_tool_dispatches.items():
            dispatched_at = info.get("dispatched_at")
            if dispatched_at and (now - ensure_utc(dispatched_at)).total_seconds() > timeout_seconds:
                stale_dispatches.append(exec_id)
        
        for exec_id in stale_dispatches:
            dispatch_info = self._pending_tool_dispatches.pop(exec_id, None)
            if not dispatch_info:
                continue
            
            logger.warning(
                f"Tool dispatch {exec_id} timed out after {timeout_seconds}s "
                f"(tool: {dispatch_info.get('tool_slug')})"
            )
            
            # Notify requesting agent of timeout
            requesting_agent_id = dispatch_info.get("requesting_agent_id")
            requesting_agent = self._connected_agents.get(requesting_agent_id)
            
            if requesting_agent:
                await self._send_tool_result(requesting_agent, exec_id, {
                    "success": False,
                    "error": f"Tool execution timed out after {timeout_seconds}s",
                    "tool_slug": dispatch_info.get("tool_slug"),
                    "timed_out": True,
                })
        
        if stale_dispatches:
            logger.info(f"Cleaned up {len(stale_dispatches)} stale tool dispatches")
    
    async def cleanup_dispatches_for_agent(
        self,
        agent_id: UUID,
        role: str = "target"
    ):
        """
        Clean up pending dispatches when an agent disconnects.
        
        Args:
            agent_id: The disconnecting agent's ID
            role: "target" (executing agent) or "requesting" (waiting for result)
        """
        to_cleanup = []
        
        for exec_id, info in self._pending_tool_dispatches.items():
            if role == "target" and info.get("target_agent_id") == agent_id:
                to_cleanup.append(exec_id)
            elif role == "requesting" and info.get("requesting_agent_id") == agent_id:
                to_cleanup.append(exec_id)
        
        for exec_id in to_cleanup:
            dispatch_info = self._pending_tool_dispatches.pop(exec_id, None)
            if not dispatch_info:
                continue
            
            if role == "target":
                # Target agent disconnected - notify requesting agent
                requesting_agent_id = dispatch_info.get("requesting_agent_id")
                requesting_agent = self._connected_agents.get(requesting_agent_id)
                
                if requesting_agent:
                    await self._send_tool_result(requesting_agent, exec_id, {
                        "success": False,
                        "error": "Executing agent disconnected",
                        "tool_slug": dispatch_info.get("tool_slug"),
                        "agent_disconnected": True,
                    })
            # If requesting agent disconnected, just clean up (no one to notify)
        
        if to_cleanup:
            logger.info(f"Cleaned up {len(to_cleanup)} dispatches for disconnected agent {agent_id}")
    
    async def route_user_input_to_campaign(
        self,
        db: AsyncSession,
        campaign_id: UUID,
        message: str,
    ) -> bool:
        """
        Route a user message to the worker managing a campaign.
        
        Args:
            campaign_id: Campaign UUID
            message: User's message content
            
        Returns:
            True if message was routed, False if no worker found
        """
        worker_id = self._campaign_to_worker.get(campaign_id)
        if not worker_id:
            logger.debug(f"No remote worker for campaign {campaign_id}")
            return False
        
        agent_id = self._worker_id_to_agent.get(worker_id)
        if not agent_id:
            logger.warning(f"Worker {worker_id} not connected")
            return False
        
        connected = self._connected_agents.get(agent_id)
        if not connected:
            logger.warning(f"Agent {agent_id} not connected")
            return False
        
        # Send user input to the worker
        await connected.websocket.send_json({
            "type": "campaign_user_input",
            "data": {
                "campaign_id": str(campaign_id),
                "message": message,
            }
        })
        
        logger.debug(f"Routed user input to worker {worker_id} for campaign {campaign_id}")
        return True
    
    async def assign_campaign_to_worker(
        self,
        db: AsyncSession,
        campaign_id: UUID,
        campaign_data: dict,
    ) -> Optional[str]:
        """
        Find an available remote worker and assign a campaign.
        
        Args:
            campaign_id: Campaign UUID
            campaign_data: Campaign state data to send to worker.
                Should include:
                - status: Campaign status
                - current_phase: Current execution phase
                - proposal_title, proposal_summary: Campaign description
                - budget_allocated, budget_spent, revenue_generated: Financial data
                - tasks_total, tasks_completed: Progress tracking
                - requirements_checklist: List of requirements
                - conversation_history: List of messages
                - available_tools: List of available tools
                - model_tier: LLM model tier ("fast", "reasoning", "quality")
                - max_tokens: Max tokens per LLM call (default: 6000)
            
        Returns:
            worker_id if assigned, None if no workers available
        """
        logger.info(f"assign_campaign_to_worker called for {campaign_id}")
        logger.info(f"Connected agents: {list(self._connected_agents.keys())}")
        
        # Ensure model_tier and max_tokens have defaults
        # These match the Campaign Manager agent's configuration
        if "model_tier" not in campaign_data:
            campaign_data["model_tier"] = "reasoning"
        if "max_tokens" not in campaign_data:
            campaign_data["max_tokens"] = 6000
        
        # Find available campaign worker
        for agent_id, connected in self._connected_agents.items():
            logger.info(f"Checking agent {agent_id}: is_campaign_worker={connected.is_campaign_worker}, campaign_capacity={connected.campaign_capacity}, held_campaigns={len(connected.held_campaigns)}")
            if connected.is_campaign_available:
                logger.info(f"Agent {agent_id} is available, sending campaign_assigned message")
                # Assign to this worker
                await connected.websocket.send_json({
                    "type": "campaign_assigned",
                    "data": {
                        "campaign_id": str(campaign_id),
                        "campaign": campaign_data,
                    }
                })
                
                logger.info(
                    f"Campaign {campaign_id} assigned to worker "
                    f"{connected.campaign_worker_id}"
                )
                return connected.campaign_worker_id
        
        logger.warning(f"No available campaign workers found!")
        return None
    
    def get_campaign_worker(self, campaign_id: UUID) -> Optional[ConnectedAgent]:
        """Get the connected agent managing a campaign."""
        worker_id = self._campaign_to_worker.get(campaign_id)
        if not worker_id:
            return None
        agent_id = self._worker_id_to_agent.get(worker_id)
        if not agent_id:
            return None
        return self._connected_agents.get(agent_id)
    
    def get_available_campaign_workers(self) -> list[ConnectedAgent]:
        """Get list of connected agents that can accept campaigns."""
        return [
            agent for agent in self._connected_agents.values()
            if agent.is_campaign_available
        ]


# Global broker instance
broker_service = BrokerService()
