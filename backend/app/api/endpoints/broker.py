"""
Broker API endpoints for remote agent communication.

Provides:
- WebSocket endpoint for agent connections
- REST endpoints for agent management
- Admin endpoints for viewing connected agents
"""
import json
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.api.deps import get_current_user, get_current_admin
from app.models import User, RemoteAgent, RemoteAgentStatus
from app.services.broker_service import broker_service
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/broker", tags=["broker"])


# =============================================================================
# Schemas
# =============================================================================

class RemoteAgentCreate(BaseModel):
    """Create a new remote agent registration.
    
    The hostname will be used as the primary identifier for this agent.
    It should match the actual hostname of the machine where the agent runs.
    """
    hostname: str  # Primary identifier - should match machine hostname
    display_name: Optional[str] = None  # Optional friendly name
    description: str = ""
    tags: list[str] = []


class RemoteAgentResponse(BaseModel):
    """Remote agent response."""
    id: UUID
    hostname: str  # Primary identifier
    display_name: Optional[str]  # Friendly name (may be None)
    name: str  # Computed: display_name or hostname
    description: Optional[str]
    tags: list[str]
    status: str
    max_concurrent_jobs: int
    capabilities: Optional[dict]
    live_stats: Optional[dict]
    last_seen_at: Optional[str]
    connected_at: Optional[str]
    ip_address: Optional[str]
    is_enabled: bool
    created_at: str
    
    class Config:
        from_attributes = True


class RemoteAgentCreateResponse(BaseModel):
    """Response after creating agent - includes API key (shown once!)."""
    agent: RemoteAgentResponse
    api_key: str  # Only shown on creation!


# =============================================================================
# Admin Endpoints
# =============================================================================

@router.post("/agents", response_model=RemoteAgentCreateResponse)
async def create_remote_agent(
    data: RemoteAgentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Create a new remote agent registration.
    
    The hostname is the primary identifier and should match the actual
    hostname of the machine where the agent will run.
    
    Returns the agent and its API key. The API key is only shown once!
    Copy it and configure it on the remote machine.
    """
    # Check hostname uniqueness
    existing = await db.execute(
        select(RemoteAgent).where(RemoteAgent.hostname == data.hostname)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"Agent with hostname '{data.hostname}' already exists")
    
    agent, api_key = await broker_service.create_agent(
        db,
        hostname=data.hostname,
        display_name=data.display_name,
        description=data.description,
        tags=data.tags
    )
    
    return RemoteAgentCreateResponse(
        agent=RemoteAgentResponse(
            id=agent.id,
            hostname=agent.hostname,
            display_name=agent.display_name,
            name=agent.name,  # Uses property: display_name or hostname
            description=agent.description,
            tags=agent.tags or [],
            status=agent.status,
            max_concurrent_jobs=agent.max_concurrent_jobs,
            capabilities=agent.capabilities,
            live_stats=agent.live_stats,
            last_seen_at=agent.last_seen_at.isoformat() if agent.last_seen_at else None,
            connected_at=agent.connected_at.isoformat() if agent.connected_at else None,
            ip_address=agent.ip_address,
            is_enabled=agent.is_enabled,
            created_at=agent.created_at.isoformat()
        ),
        api_key=api_key
    )


@router.get("/agents", response_model=list[RemoteAgentResponse])
async def list_remote_agents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """List all registered remote agents."""
    result = await db.execute(
        select(RemoteAgent).order_by(RemoteAgent.hostname)
    )
    agents = result.scalars().all()
    
    return [
        RemoteAgentResponse(
            id=a.id,
            hostname=a.hostname,
            display_name=a.display_name,
            name=a.name,  # Uses property
            description=a.description,
            tags=a.tags or [],
            status=a.status,
            max_concurrent_jobs=a.max_concurrent_jobs,
            capabilities=a.capabilities,
            live_stats=a.live_stats,
            last_seen_at=a.last_seen_at.isoformat() if a.last_seen_at else None,
            connected_at=a.connected_at.isoformat() if a.connected_at else None,
            ip_address=a.ip_address,
            is_enabled=a.is_enabled,
            created_at=a.created_at.isoformat()
        )
        for a in agents
    ]


@router.get("/agents/connected")
async def get_connected_agents(
    current_user: User = Depends(get_current_admin)
):
    """Get list of currently connected agents (live from memory)."""
    return broker_service.get_connected_agents()


@router.get("/agents/{hostname}", response_model=RemoteAgentResponse)
async def get_remote_agent(
    hostname: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Get a specific remote agent by hostname."""
    result = await db.execute(
        select(RemoteAgent).where(RemoteAgent.hostname == hostname)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, f"Agent with hostname '{hostname}' not found")
    
    return RemoteAgentResponse(
        id=agent.id,
        hostname=agent.hostname,
        display_name=agent.display_name,
        name=agent.name,
        description=agent.description,
        tags=agent.tags or [],
        status=agent.status,
        max_concurrent_jobs=agent.max_concurrent_jobs,
        capabilities=agent.capabilities,
        live_stats=agent.live_stats,
        last_seen_at=agent.last_seen_at.isoformat() if agent.last_seen_at else None,
        connected_at=agent.connected_at.isoformat() if agent.connected_at else None,
        ip_address=agent.ip_address,
        is_enabled=agent.is_enabled,
        created_at=agent.created_at.isoformat()
    )


@router.delete("/agents/{hostname}")
async def delete_remote_agent(
    hostname: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Delete a remote agent registration by hostname."""
    result = await db.execute(
        select(RemoteAgent).where(RemoteAgent.hostname == hostname)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, f"Agent with hostname '{hostname}' not found")
    
    await db.delete(agent)
    await db.commit()
    
    return {"message": f"Agent '{agent.hostname}' deleted"}


@router.post("/agents/{hostname}/enable")
async def enable_agent(
    hostname: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Enable a remote agent by hostname."""
    result = await db.execute(
        select(RemoteAgent).where(RemoteAgent.hostname == hostname)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, f"Agent with hostname '{hostname}' not found")
    
    agent.is_enabled = True
    await db.commit()
    
    return {"message": f"Agent '{agent.hostname}' enabled"}


@router.post("/agents/{hostname}/disable")
async def disable_agent(
    hostname: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Disable a remote agent by hostname (prevents new connections)."""
    result = await db.execute(
        select(RemoteAgent).where(RemoteAgent.hostname == hostname)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, f"Agent with hostname '{hostname}' not found")
    
    agent.is_enabled = False
    await db.commit()
    
    return {"message": f"Agent '{agent.hostname}' disabled"}


@router.post("/agents/{hostname}/regenerate-key")
async def regenerate_api_key(
    hostname: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Regenerate API key for an agent.
    
    The old key will stop working immediately.
    Returns the new API key (shown once!).
    """
    result = await db.execute(
        select(RemoteAgent).where(RemoteAgent.hostname == hostname)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, f"Agent with hostname '{hostname}' not found")
    
    # Generate new key
    new_api_key = broker_service.generate_api_key()
    agent.api_key_hash = broker_service.hash_api_key(new_api_key)
    await db.commit()
    
    return {
        "message": f"API key regenerated for '{agent.name}'",
        "api_key": new_api_key  # Only shown once!
    }


# =============================================================================
# WebSocket Endpoint
# =============================================================================

@router.websocket("/agent")
async def agent_websocket(
    websocket: WebSocket,
):
    """
    WebSocket endpoint for remote agent connections.
    
    Protocol:
    1. Agent connects
    2. Agent must send an 'auth' message as its first message:
       {"type": "auth", "data": {"api_key": "..."}}
    3. Agent sends 'register' message with capabilities
    4. Broker responds with 'registered' message
    5. Agent sends periodic 'heartbeat' messages
    6. Broker sends 'job_assigned' messages when jobs are available
    7. Agent sends 'job_completed' or 'job_failed' messages
    """
    from app.core.database import get_session_maker
    
    # Create session maker for this connection
    session_maker = get_session_maker()

    # Accept the connection first (needed for first-message auth)
    await websocket.accept()

    # GAP-2: First-message auth only — no query parameter (prevents key leakage in logs)
    effective_key = None
    try:
        import asyncio
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        first_msg = json.loads(raw)
        if first_msg.get("type") == "auth":
            effective_key = first_msg.get("data", {}).get("api_key")
        if not effective_key:
            await websocket.close(code=4001, reason="First message must be auth with api_key")
            return
    except Exception:
        await websocket.close(code=4001, reason="Authentication timeout or invalid message")
        return
    
    # Authenticate - use a fresh session
    async with session_maker() as db:
        agent = await broker_service.authenticate_agent(db, effective_key)
        if not agent:
            await websocket.close(code=4001, reason="Invalid API key")
            return
        agent_id = agent.id
        agent_name = agent.name
    
    logger.info(f"Agent '{agent_name}' WebSocket connected")
    
    connected = None
    
    try:
        async for message in websocket.iter_text():
            try:
                data = json.loads(message)
                msg_type = data.get("type")
                msg_data = data.get("data", {})
                
                # Debug: log all incoming messages
                if msg_type not in ("heartbeat", "worker_heartbeat"):
                    logger.info(f"Agent {agent_name} message: {msg_type}")
                
                # Get fresh db session for each message
                async with session_maker() as db:
                    # Re-fetch agent to avoid detached instance errors
                    agent = await db.get(RemoteAgent, agent_id)
                    if not agent:
                        logger.error(f"Agent {agent_id} not found in database")
                        await websocket.close(code=4002, reason="Agent not found")
                        return
                    
                    if msg_type == "register":
                        # Agent registration with capabilities
                        connected = await broker_service.agent_connected(
                            db, agent, websocket, msg_data
                        )
                        
                        # Send confirmation
                        await websocket.send_json({
                            "type": "registered",
                            "data": {
                                "agent_id": str(agent.id),
                                "message": f"Welcome, {agent.name}!"
                            }
                        })
                    
                    elif msg_type == "heartbeat":
                        await broker_service.agent_heartbeat(
                            db, agent_id, msg_data.get("stats", {})
                        )
                        
                    elif msg_type == "job_accepted":
                        job_id = msg_data.get("job_id")
                        if job_id:
                            await broker_service.job_accepted(db, agent_id, UUID(job_id))
                            
                    elif msg_type == "job_progress":
                        job_id = msg_data.get("job_id")
                        if job_id:
                            await broker_service.job_progress(
                                db, agent_id, UUID(job_id),
                                msg_data.get("progress", 0),
                                msg_data.get("message", "")
                            )
                            
                    elif msg_type == "job_completed":
                        job_id = msg_data.get("job_id")
                        if job_id:
                            await broker_service.job_completed(
                                db, agent_id, job_id,
                                msg_data.get("result")
                            )
                            
                    elif msg_type == "job_failed":
                        job_id = msg_data.get("job_id")
                        if job_id:
                            await broker_service.job_failed(
                                db, agent_id, job_id,
                                msg_data.get("error", "Unknown error")
                            )
                    
                    # Campaign Worker Messages
                    elif msg_type == "worker_register":
                        await broker_service.register_campaign_worker(
                            db, agent_id, msg_data
                        )
                        
                    elif msg_type == "worker_heartbeat":
                        await broker_service.campaign_worker_heartbeat(
                            db, agent_id, msg_data
                        )
                        
                    elif msg_type == "worker_disconnect":
                        await broker_service.campaign_worker_disconnect(
                            db, agent_id, msg_data
                        )
                        
                    elif msg_type == "campaign_accepted":
                        await broker_service.campaign_accepted(
                            db, agent_id, msg_data
                        )
                        
                    elif msg_type == "campaign_release":
                        await broker_service.campaign_release(
                            db, agent_id, msg_data
                        )
                        
                    elif msg_type == "campaign_progress":
                        await broker_service.campaign_progress(
                            db, agent_id, msg_data
                        )
                        
                    elif msg_type == "campaign_response":
                        await broker_service.campaign_response(
                            db, agent_id, msg_data
                        )
                        
                    elif msg_type == "campaign_user_input_request":
                        # Worker is requesting user input for the campaign
                        await broker_service.campaign_user_input_request(
                            db, agent_id, msg_data
                        )
                        
                    elif msg_type == "campaign_error":
                        await broker_service.campaign_error(
                            db, agent_id, msg_data
                        )
                        
                    elif msg_type == "tool_dispatch":
                        await broker_service.campaign_tool_dispatch(
                            db, agent_id, msg_data
                        )
                            
                    else:
                        logger.warning(f"Unknown message type from {agent_name}: {msg_type}")
                    
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON from {agent_name}: {message[:100]}")
            except Exception as e:
                logger.error(f"Error processing message from {agent_name}: {e}")
                
    except WebSocketDisconnect:
        logger.info(f"Agent '{agent_name}' disconnected")
    except Exception as e:
        logger.error(f"WebSocket error for {agent_name}: {e}")
    finally:
        if connected:
            async with session_maker() as db:
                await broker_service.agent_disconnected(db, agent_id)
