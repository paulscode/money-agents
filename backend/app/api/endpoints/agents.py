"""Agent API endpoints."""
import asyncio
import json
import logging
import time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_admin, get_db, get_db_context
from app.models import User
from app.agents import AgentContext, proposal_writer_agent, tool_scout_agent, campaign_manager_agent, campaign_discussion_agent
from app.core.security import decode_access_token
from app.core.rate_limit import limiter
from app.services.llm_usage_service import llm_usage_service
from app.models.llm_usage import LLMUsageSource
from app.api.websocket_security import (
    WSConnectionGuard,
    authenticate_websocket,
    ws_receive_validated,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class AgentChatRequest(BaseModel):
    """Request to chat with an agent."""
    
    message: str = Field(..., min_length=1, max_length=10000)
    conversation_id: Optional[UUID] = None
    proposal_id: Optional[UUID] = None


class AgentChatResponse(BaseModel):
    """Response from agent chat."""
    
    success: bool
    message: str
    content: Optional[str] = None
    message_id: Optional[str] = None
    tokens_used: int = 0
    model_used: Optional[str] = None
    latency_ms: int = 0


class ProposalAnalysisRequest(BaseModel):
    """Request to analyze a proposal."""
    
    title: Optional[str] = None
    summary: Optional[str] = None
    detailed_description: Optional[str] = None
    initial_budget: Optional[float] = None
    expected_returns: Optional[dict] = None
    risk_level: Optional[str] = None
    risk_description: Optional[str] = None
    success_criteria: Optional[dict] = None
    required_tools: Optional[dict] = None
    required_inputs: Optional[dict] = None
    stop_loss_threshold: Optional[dict] = None
    implementation_timeline: Optional[dict] = None


class ProposalAnalysisResponse(BaseModel):
    """Response from proposal analysis."""
    
    success: bool
    message: str
    analysis: Optional[str] = None
    tokens_used: int = 0
    model_used: Optional[str] = None
    latency_ms: int = 0


@router.post("/proposal-writer/chat", response_model=AgentChatResponse)
@limiter.limit("10/minute")
async def chat_with_proposal_writer(
    request: Request,
    body: AgentChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Chat with the Proposal Writer agent.
    
    The agent helps refine and improve campaign proposals through conversation.
    """
    from app.models import Conversation, ConversationType
    from sqlalchemy import select
    
    # Determine conversation
    conversation_id = body.conversation_id
    
    # If proposal_id provided but no conversation_id, find or create conversation
    if body.proposal_id and not conversation_id:
        query = select(Conversation).where(
            Conversation.conversation_type == ConversationType.PROPOSAL,
            Conversation.related_id == body.proposal_id,
        )
        result = await db.execute(query)
        existing = result.scalar_one_or_none()
        
        if existing:
            conversation_id = existing.id
    
    # Build context
    context = AgentContext(
        db=db,
        conversation_id=conversation_id,
        related_id=body.proposal_id,
        user_id=current_user.id,
    )
    
    # If we need a conversation but don't have one, create it
    if body.proposal_id and not conversation_id:
        conversation = await proposal_writer_agent.get_or_create_conversation(
            context=context,
            conversation_type=ConversationType.PROPOSAL,
            title="Proposal Discussion",
        )
        conversation_id = conversation.id
        context.conversation_id = conversation_id
    
    # Execute agent
    result = await proposal_writer_agent.execute(
        context=context,
        action="respond",
        user_message=body.message,
    )
    
    await db.commit()
    
    return AgentChatResponse(
        success=result.success,
        message=result.message,
        content=result.data.get("content") if result.data else None,
        message_id=result.data.get("message_id") if result.data else None,
        tokens_used=result.tokens_used,
        model_used=result.model_used,
        latency_ms=result.latency_ms,
    )


@router.post("/proposal-writer/analyze", response_model=ProposalAnalysisResponse)
@limiter.limit("10/minute")
async def analyze_proposal(
    request: Request,
    body: ProposalAnalysisRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Analyze a proposal and get feedback.
    
    Send proposal details to get a detailed analysis with suggestions.
    """
    context = AgentContext(
        db=db,
        user_id=current_user.id,
    )
    
    # Convert request to dict
    proposal_data = body.model_dump(exclude_none=True)
    
    result = await proposal_writer_agent.execute(
        context=context,
        action="analyze",
        proposal_data=proposal_data,
    )
    
    return ProposalAnalysisResponse(
        success=result.success,
        message=result.message,
        analysis=result.data.get("analysis") if result.data else None,
        tokens_used=result.tokens_used,
        model_used=result.model_used,
        latency_ms=result.latency_ms,
    )


@router.get("/status")
async def get_agent_status(
    current_user: User = Depends(get_current_user),
):
    """Get status of available agents."""
    return {
        "agents": [
            {
                "name": proposal_writer_agent.name,
                "description": proposal_writer_agent.description,
                "default_model": proposal_writer_agent.default_model,
                "status": "active",
            }
        ]
    }




def build_model_override(data: dict) -> Optional[str]:
    """
    Build a model override string from provider and tier in WebSocket message data.
    
    The LLM service accepts formats like:
    - "fast", "reasoning", "quality" (tier only, uses default provider)
    - "claude:fast", "openai:reasoning" (provider:tier)
    
    Args:
        data: WebSocket message data dict
        
    Returns:
        Model override string or None if no override specified
    """
    provider = data.get("provider")
    tier = data.get("tier")
    
    if not tier:
        return None
    
    if provider:
        return f"{provider}:{tier}"
    return tier


@router.websocket("/proposal-writer/stream")
async def websocket_proposal_writer_stream(websocket: WebSocket):
    """
    WebSocket endpoint for streaming chat with Proposal Writer agent.
    
    Protocol:
    1. Connect to WebSocket
    2. Send auth message: {"type": "auth", "token": "<access_token>"}
    3. Receive auth response: {"type": "auth_result", "success": true/false}
    4. Send chat messages: {"type": "message", "content": "<message>", "conversation_id": "<optional>"}
    5. Receive streamed response:
       - {"type": "chunk", "content": "<text>"}
       - {"type": "done", "model": "<model>", "provider": "<provider>", "tokens": <n>, "latency_ms": <n>}
    """
    await websocket.accept()
    
    try:
        # Authenticate
        user = await authenticate_websocket(websocket)
        if not user:
            await websocket.send_json({
                "type": "auth_result",
                "success": False,
                "error": "Authentication failed",
            })
            await websocket.close(code=4001, reason="Authentication failed")
            return
        
        await websocket.send_json({
            "type": "auth_result",
            "success": True,
            "user_id": str(user.id),
        })
        
        logger.info(f"WebSocket authenticated for user {user.id}")
        
        # SGA-L3: Use WSConnectionGuard for safe per-user tracking
        async with WSConnectionGuard(str(user.id)) as guard:
            if guard.rejected:
                await websocket.send_json({"type": "error", "error": "Too many connections"})
                await websocket.close(code=4008, reason="Too many connections")
                return
            
            rate_state: dict = {}  # SGA-M1: per-connection rate tracking
            # Handle messages
            while True:
                try:
                    data = await ws_receive_validated(websocket, rate_state=rate_state)
                    if data.get("type") == "_oversized":
                        await websocket.send_json({
                            "type": "error",
                            "error": "Message too large",
                        })
                        continue
                    if data.get("type") == "_rate_limited":
                        continue  # silently drop rapid-fire messages
                except WebSocketDisconnect:
                    logger.info(f"WebSocket disconnected for user {user.id}")
                    break
                
                msg_type = data.get("type")
                
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                
                if msg_type != "message":
                    await websocket.send_json({
                        "type": "error",
                        "error": f"Unknown message type: {msg_type}",
                    })
                    continue
                
                content = data.get("content", "").strip()
                if not content:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Empty message content",
                    })
                    continue
                
                conversation_id = data.get("conversation_id")
                proposal_context = data.get("proposal_context")
                model_override = build_model_override(data)
                
                # Stream response
                try:
                    async with get_db_context() as db:
                        context = AgentContext(
                            db=db,
                            conversation_id=UUID(conversation_id) if conversation_id else None,
                            user_id=user.id,
                            extra={"proposal_context": proposal_context} if proposal_context else {},
                        )
                        
                        # Load conversation history with guardrails (max 30 messages)
                        conversation_history = []
                        if context.conversation_id:
                            conversation_history = await proposal_writer_agent.get_conversation_history(
                                context, limit=30
                            )
                        
                        full_content = []
                        
                        async for chunk in proposal_writer_agent.respond_to_message_stream(
                            context=context,
                            user_message=content,
                            model_override=model_override,
                        ):
                            if chunk.is_final:
                                # Track LLM usage
                                await llm_usage_service.track(
                                    db=db,
                                    source=LLMUsageSource.AGENT_CHAT,
                                    provider=chunk.provider or "",
                                    model=chunk.model or "",
                                    prompt_tokens=chunk.prompt_tokens,
                                    completion_tokens=chunk.completion_tokens,
                                    user_id=user.id,
                                    conversation_id=context.conversation_id,
                                    latency_ms=chunk.latency_ms,
                                    meta_data={"agent": "proposal_writer"},
                                )
                                
                                # Send completion message
                                await websocket.send_json({
                                    "type": "done",
                                    "model": chunk.model,
                                    "provider": chunk.provider,
                                    "prompt_tokens": chunk.prompt_tokens,
                                    "completion_tokens": chunk.completion_tokens,
                                    "total_tokens": chunk.total_tokens,
                                    "latency_ms": chunk.latency_ms,
                                })
                                
                                # Save message to conversation if we have one
                                if context.conversation_id:
                                    full_text = "".join(full_content)
                                    await proposal_writer_agent.send_message(
                                        context=context,
                                        content=full_text,
                                        tokens_used=chunk.total_tokens,
                                        model_used=chunk.model,
                                        prompt_tokens=chunk.prompt_tokens,
                                        completion_tokens=chunk.completion_tokens,
                                    )
                                    await db.commit()
                            else:
                                # Stream chunk
                                full_content.append(chunk.content)
                                await websocket.send_json({
                                    "type": "chunk",
                                    "content": chunk.content,
                                })
                                
                except Exception as e:
                    logger.exception(f"Error streaming response for user {user.id}")
                    await websocket.send_json({
                        "type": "error",
                        "error": "An unexpected error occurred while processing your request.",
                    })
                
    except WebSocketDisconnect:
        logger.info("WebSocket connection closed")
    except Exception as e:
        logger.exception("WebSocket error")
        try:
            await websocket.send_json({
                "type": "error",
                "error": "An unexpected error occurred.",
            })
        except Exception:
            pass


@router.websocket("/tool-scout/stream")
async def websocket_tool_scout_stream(websocket: WebSocket):
    """
    WebSocket endpoint for streaming chat with Tool Scout agent.
    
    Protocol:
    1. Connect to WebSocket
    2. Send auth message: {"type": "auth", "token": "<access_token>"}
    3. Receive auth response: {"type": "auth_result", "success": true/false}
    4. Send chat messages: {"type": "message", "content": "<message>", "conversation_id": "<optional>", "tool_context": {...}}
    5. Receive streamed response:
       - {"type": "chunk", "content": "<text>"}
       - {"type": "done", "model": "<model>", "provider": "<provider>", "tokens": <n>, "latency_ms": <n>}
    """
    await websocket.accept()
    
    try:
        # Authenticate
        user = await authenticate_websocket(websocket)
        if not user:
            await websocket.send_json({
                "type": "auth_result",
                "success": False,
                "error": "Authentication failed",
            })
            await websocket.close(code=4001, reason="Authentication failed")
            return
        
        await websocket.send_json({
            "type": "auth_result",
            "success": True,
            "user_id": str(user.id),
        })
        
        logger.info(f"Tool Scout WebSocket authenticated for user {user.id}")
        
        # SGA-L3: Use WSConnectionGuard for safe per-user tracking
        async with WSConnectionGuard(str(user.id)) as guard:
            if guard.rejected:
                await websocket.send_json({"type": "error", "error": "Too many connections"})
                await websocket.close(code=4008, reason="Too many connections")
                return
            
            rate_state: dict = {}  # SGA-M1: per-connection rate tracking
            # Handle messages
            while True:
                try:
                    data = await ws_receive_validated(websocket, rate_state=rate_state)
                    if data.get("type") == "_oversized":
                        await websocket.send_json({
                            "type": "error",
                            "error": "Message too large",
                        })
                        continue
                    if data.get("type") == "_rate_limited":
                        continue  # silently drop rapid-fire messages
                except WebSocketDisconnect:
                    logger.info(f"Tool Scout WebSocket disconnected for user {user.id}")
                    break
                
                msg_type = data.get("type")
                
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                
                if msg_type != "message":
                    await websocket.send_json({
                        "type": "error",
                        "error": f"Unknown message type: {msg_type}",
                    })
                    continue
                
                content = data.get("content", "").strip()
                if not content:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Empty message content",
                    })
                    continue
                
                conversation_id = data.get("conversation_id")
                tool_context = data.get("tool_context")
                model_override = build_model_override(data)
                
                # Stream response
                try:
                    async with get_db_context() as db:
                        context = AgentContext(
                            db=db,
                            conversation_id=UUID(conversation_id) if conversation_id else None,
                            user_id=user.id,
                            extra={"tool_context": tool_context} if tool_context else {},
                        )
                        
                        # Load conversation history with guardrails (max 30 messages)
                        conversation_history = []
                        if context.conversation_id:
                            conversation_history = await tool_scout_agent.get_conversation_history(
                                context, limit=30
                            )
                        
                        full_content = []
                        
                        async for chunk in tool_scout_agent.chat(
                            context=context,
                            user_message=content,
                            conversation_history=conversation_history,
                            tool_context=tool_context,
                            model_override=model_override,
                        ):
                            if chunk.is_final:
                                # Track LLM usage
                                await llm_usage_service.track(
                                    db=db,
                                    source=LLMUsageSource.AGENT_CHAT,
                                    provider=chunk.provider or "",
                                    model=chunk.model or "",
                                    prompt_tokens=chunk.prompt_tokens,
                                    completion_tokens=chunk.completion_tokens,
                                    user_id=user.id,
                                    conversation_id=context.conversation_id,
                                    latency_ms=chunk.latency_ms,
                                    meta_data={"agent": "tool_scout"},
                                )
                                
                                # Send completion message
                                await websocket.send_json({
                                    "type": "done",
                                    "model": chunk.model,
                                    "provider": chunk.provider,
                                    "prompt_tokens": chunk.prompt_tokens,
                                    "completion_tokens": chunk.completion_tokens,
                                    "total_tokens": chunk.total_tokens,
                                    "latency_ms": chunk.latency_ms,
                                })
                                
                                # Save message to conversation if we have one
                                if context.conversation_id:
                                    full_text = "".join(full_content)
                                    await tool_scout_agent.send_message(
                                        context=context,
                                        content=full_text,
                                        tokens_used=chunk.total_tokens,
                                        model_used=chunk.model,
                                        prompt_tokens=chunk.prompt_tokens,
                                        completion_tokens=chunk.completion_tokens,
                                    )
                                    await db.commit()
                            else:
                                # Stream chunk
                                full_content.append(chunk.content)
                                await websocket.send_json({
                                    "type": "chunk",
                                    "content": chunk.content,
                                })
                                
                except Exception as e:
                    logger.exception(f"Error streaming Tool Scout response for user {user.id}")
                    await websocket.send_json({
                        "type": "error",
                        "error": "An unexpected error occurred while processing your request.",
                    })
                
    except WebSocketDisconnect:
        logger.info("Tool Scout WebSocket connection closed")
    except Exception as e:
        logger.exception("Tool Scout WebSocket error")
        try:
            await websocket.send_json({
                "type": "error",
                "error": "An unexpected error occurred.",
            })
        except Exception:
            pass


@router.websocket("/campaign-manager/stream")
async def websocket_campaign_manager_stream(websocket: WebSocket):
    """
    WebSocket endpoint for streaming chat with Campaign Manager agent.
    
    Protocol:
    1. Connect to WebSocket
    2. Send auth message: {"type": "auth", "token": "<access_token>"}
    3. Receive auth response: {"type": "auth_result", "success": true/false}
    4. Send chat messages: {"type": "message", "content": "<message>", "conversation_id": "<optional>", "campaign_context": {...}}
    5. Receive streamed response:
       - {"type": "chunk", "content": "<text>"}
       - {"type": "done", "model": "<model>", "provider": "<provider>", "tokens": <n>, "latency_ms": <n>}
    """
    await websocket.accept()
    
    try:
        # Authenticate
        user = await authenticate_websocket(websocket)
        if not user:
            await websocket.send_json({
                "type": "auth_result",
                "success": False,
                "error": "Authentication failed",
            })
            await websocket.close(code=4001, reason="Authentication failed")
            return
        
        await websocket.send_json({
            "type": "auth_result",
            "success": True,
            "user_id": str(user.id),
        })
        
        logger.info(f"Campaign Manager WebSocket authenticated for user {user.id}")
        
        # SGA-L3: Use WSConnectionGuard for safe per-user tracking
        async with WSConnectionGuard(str(user.id)) as guard:
            if guard.rejected:
                await websocket.send_json({"type": "error", "error": "Too many connections"})
                await websocket.close(code=4008, reason="Too many connections")
                return
            
            rate_state: dict = {}  # SGA-M1: per-connection rate tracking
            # Handle messages
            while True:
                try:
                    data = await ws_receive_validated(websocket, rate_state=rate_state)
                    if data.get("type") == "_oversized":
                        await websocket.send_json({
                            "type": "error",
                            "error": "Message too large",
                        })
                        continue
                    if data.get("type") == "_rate_limited":
                        continue  # silently drop rapid-fire messages
                except WebSocketDisconnect:
                    logger.info(f"Campaign Manager WebSocket disconnected for user {user.id}")
                    break
                
                msg_type = data.get("type")
                
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                
                if msg_type != "message":
                    await websocket.send_json({
                        "type": "error",
                        "error": f"Unknown message type: {msg_type}",
                    })
                    continue
                
                content = data.get("content", "").strip()
                if not content:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Empty message content",
                    })
                    continue
                
                conversation_id = data.get("conversation_id")
                campaign_context = data.get("campaign_context")
                
                # Stream response
                try:
                    async with get_db_context() as db:
                        context = AgentContext(
                            db=db,
                            conversation_id=UUID(conversation_id) if conversation_id else None,
                            user_id=user.id,
                            extra={"campaign_context": campaign_context} if campaign_context else {},
                        )
                        
                        full_content = []
                        
                        async for chunk in campaign_manager_agent.chat_stream(
                            context=context,
                            user_message=content,
                            campaign_context=campaign_context,
                        ):
                            if chunk.is_final:
                                # Track LLM usage
                                await llm_usage_service.track(
                                    db=db,
                                    source=LLMUsageSource.AGENT_CHAT,
                                    provider=chunk.provider or "",
                                    model=chunk.model or "",
                                    prompt_tokens=chunk.prompt_tokens,
                                    completion_tokens=chunk.completion_tokens,
                                    user_id=user.id,
                                    conversation_id=context.conversation_id,
                                    latency_ms=chunk.latency_ms,
                                    meta_data={"agent": "campaign_manager"},
                                )
                                
                                # Send completion message
                                await websocket.send_json({
                                    "type": "done",
                                    "model": chunk.model,
                                    "provider": chunk.provider,
                                    "prompt_tokens": chunk.prompt_tokens,
                                    "completion_tokens": chunk.completion_tokens,
                                    "total_tokens": chunk.total_tokens,
                                    "latency_ms": chunk.latency_ms,
                                })
                                
                                # Save message to conversation if we have one
                                if context.conversation_id:
                                    full_text = "".join(full_content)
                                    await campaign_manager_agent.send_message(
                                        context=context,
                                        content=full_text,
                                        tokens_used=chunk.total_tokens,
                                        model_used=chunk.model,
                                        prompt_tokens=chunk.prompt_tokens,
                                        completion_tokens=chunk.completion_tokens,
                                    )
                                    await db.commit()
                            else:
                                # Stream chunk
                                full_content.append(chunk.content)
                                await websocket.send_json({
                                    "type": "chunk",
                                    "content": chunk.content,
                                })
                                
                except Exception as e:
                    logger.exception(f"Error streaming Campaign Manager response for user {user.id}")
                    await websocket.send_json({
                        "type": "error",
                        "error": "An unexpected error occurred while processing your request.",
                    })
                
    except WebSocketDisconnect:
        logger.info("Campaign Manager WebSocket connection closed")
    except Exception as e:
        logger.exception("Campaign Manager WebSocket error")
        try:
            await websocket.send_json({
                "type": "error",
                "error": "An unexpected error occurred.",
            })
        except Exception:
            pass


# =============================================================================
# Campaign Discussion WebSocket
# =============================================================================


@router.websocket("/campaign-discussion/stream")
async def websocket_campaign_discussion_stream(websocket: WebSocket):
    """
    WebSocket endpoint for streaming chat with Campaign Discussion agent.
    
    Protocol:
    1. Connect to WebSocket
    2. Send auth message: {"type": "auth", "token": "<access_token>"}
    3. Receive auth response: {"type": "auth_result", "success": true/false}
    4. Send chat messages: {"type": "message", "content": "<message>", "conversation_id": "<optional>", "campaign_id": "<required>"}
    5. Receive streamed response:
       - {"type": "chunk", "content": "<text>"}
       - {"type": "actions", "actions": [...]} - Actions parsed from response (Phase 2)
       - {"type": "done", "model": "<model>", "provider": "<provider>", "tokens": <n>, "latency_ms": <n>}
    6. Execute actions: {"type": "execute_actions", "action_ids": ["id1", "id2"]}
       - {"type": "action_results", "results": [...]}
    """
    await websocket.accept()
    
    # Track pending actions per session for execution
    pending_actions: dict = {}  # action_id -> CampaignAction
    current_campaign_id: Optional[UUID] = None
    
    try:
        # Authenticate
        user = await authenticate_websocket(websocket)
        if not user:
            await websocket.send_json({
                "type": "auth_result",
                "success": False,
                "error": "Authentication failed",
            })
            await websocket.close(code=4001, reason="Authentication failed")
            return
        
        await websocket.send_json({
            "type": "auth_result",
            "success": True,
            "user_id": str(user.id),
        })
        
        logger.info(f"Campaign Discussion WebSocket authenticated for user {user.id}")
        
        # SGA-L3: Use WSConnectionGuard for safe per-user tracking
        async with WSConnectionGuard(str(user.id)) as guard:
            if guard.rejected:
                await websocket.send_json({"type": "error", "error": "Too many connections"})
                await websocket.close(code=4008, reason="Too many connections")
                return
            
            rate_state: dict = {}  # SGA-M1: per-connection rate tracking
            # Handle messages
            while True:
                try:
                    data = await ws_receive_validated(websocket, rate_state=rate_state)
                    if data.get("type") == "_oversized":
                        await websocket.send_json({
                            "type": "error",
                            "error": "Message too large",
                        })
                        continue
                    if data.get("type") == "_rate_limited":
                        continue  # silently drop rapid-fire messages
                except WebSocketDisconnect:
                    logger.info(f"Campaign Discussion WebSocket disconnected for user {user.id}")
                    break
                
                msg_type = data.get("type")
                
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                
                # Handle action execution request
                if msg_type == "execute_actions":
                    action_ids = data.get("action_ids", [])
                    if not action_ids:
                        await websocket.send_json({
                            "type": "error",
                            "error": "No action_ids provided",
                        })
                        continue
                    
                    if not current_campaign_id:
                        await websocket.send_json({
                            "type": "error",
                            "error": "No campaign context - send a message first",
                        })
                        continue
                    
                    # Execute the requested actions
                    try:
                        from app.services.campaign_action_service import CampaignActionService
                        
                        async with get_db_context() as db:
                            action_service = CampaignActionService(db)
                            
                            # Get the actions to execute
                            actions_to_execute = [
                                pending_actions[aid] for aid in action_ids 
                                if aid in pending_actions
                            ]
                            
                            if not actions_to_execute:
                                await websocket.send_json({
                                    "type": "error",
                                    "error": "No matching pending actions found",
                                })
                                continue
                            
                            # Execute actions
                            results = await action_service.execute_actions(
                                campaign_id=current_campaign_id,
                                actions=actions_to_execute,
                                user_id=user.id,
                            )
                            
                            await db.commit()
                            
                            # Clear executed actions from pending
                            for aid in action_ids:
                                pending_actions.pop(aid, None)
                            
                            await websocket.send_json({
                                "type": "action_results",
                                "results": results,
                            })
                            
                    except Exception as e:
                        logger.exception(f"Error executing actions for user {user.id}")
                        await websocket.send_json({
                            "type": "error",
                            "error": "Failed to execute actions. Please try again.",
                        })
                    continue
                
                if msg_type != "message":
                    await websocket.send_json({
                        "type": "error",
                        "error": f"Unknown message type: {msg_type}",
                    })
                    continue
                
                content = data.get("content", "").strip()
                if not content:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Empty message content",
                    })
                    continue
                
                # Campaign ID is required for discussion context
                campaign_id = data.get("campaign_id")
                if not campaign_id:
                    await websocket.send_json({
                        "type": "error",
                        "error": "campaign_id is required for campaign discussions",
                    })
                    continue
                
                current_campaign_id = UUID(campaign_id)
                conversation_id = data.get("conversation_id")
                model_override = build_model_override(data)
                
                # Stream response
                try:
                    async with get_db_context() as db:
                        context = AgentContext(
                            db=db,
                            conversation_id=UUID(conversation_id) if conversation_id else None,
                            related_id=current_campaign_id,
                            user_id=user.id,
                        )
                        
                        full_content = []
                        
                        async for chunk in campaign_discussion_agent.respond_to_message_stream(
                            context=context,
                            user_message=content,
                            model_override=model_override,
                        ):
                            if chunk.is_final:
                                # Parse actions from the full response
                                from app.services.campaign_action_service import CampaignActionService
                                
                                full_text = "".join(full_content)
                                action_service = CampaignActionService(db)
                                parse_result = action_service.parse_response(full_text)
                                
                                # If there are actions, send them to the frontend
                                if parse_result.actions:
                                    # Store in pending actions
                                    for action in parse_result.actions:
                                        pending_actions[action.action_id] = action
                                    
                                    await websocket.send_json({
                                        "type": "actions",
                                        "actions": [a.to_dict() for a in parse_result.actions],
                                        "clean_content": parse_result.clean_content,
                                    })
                                
                                # Track LLM usage
                                await llm_usage_service.track(
                                    db=db,
                                    source=LLMUsageSource.AGENT_CHAT,
                                    provider=chunk.provider or "",
                                    model=chunk.model or "",
                                    prompt_tokens=chunk.prompt_tokens,
                                    completion_tokens=chunk.completion_tokens,
                                    user_id=user.id,
                                    conversation_id=context.conversation_id,
                                    campaign_id=current_campaign_id,
                                    latency_ms=chunk.latency_ms,
                                    meta_data={"agent": "campaign_discussion"},
                                )
                                
                                # Send completion message
                                await websocket.send_json({
                                    "type": "done",
                                    "model": chunk.model,
                                    "provider": chunk.provider,
                                    "prompt_tokens": chunk.prompt_tokens,
                                    "completion_tokens": chunk.completion_tokens,
                                    "total_tokens": chunk.total_tokens,
                                    "latency_ms": chunk.latency_ms,
                                })
                                
                                # Save message to conversation (use clean content if we have actions)
                                if context.conversation_id:
                                    save_content = parse_result.clean_content if parse_result.actions else full_text
                                    await campaign_discussion_agent.send_message(
                                        context=context,
                                        content=save_content,
                                        tokens_used=chunk.total_tokens,
                                        model_used=chunk.model,
                                        prompt_tokens=chunk.prompt_tokens,
                                        completion_tokens=chunk.completion_tokens,
                                    )
                                    await db.commit()
                            else:
                                # Stream chunk
                                full_content.append(chunk.content)
                                await websocket.send_json({
                                    "type": "chunk",
                                    "content": chunk.content,
                                })
                                
                except Exception as e:
                    logger.exception(f"Error streaming Campaign Discussion response for user {user.id}")
                    await websocket.send_json({
                        "type": "error",
                        "error": "An unexpected error occurred while processing your request.",
                    })
                
    except WebSocketDisconnect:
        logger.info("Campaign Discussion WebSocket connection closed")
    except Exception as e:
        logger.exception("Campaign Discussion WebSocket error")
        try:
            await websocket.send_json({
                "type": "error",
                "error": "An unexpected error occurred.",
            })
        except Exception:
            pass


# =============================================================================
# Campaign Manager REST Endpoints
# =============================================================================


class CampaignInitRequest(BaseModel):
    """Request to initialize a campaign from a proposal."""
    proposal_id: UUID


class CampaignUserInputRequest(BaseModel):
    """Request to process user input for a campaign."""
    message: str = Field(..., min_length=1, max_length=10000)


class CampaignActionResponse(BaseModel):
    """Response from campaign action."""
    success: bool
    message: str
    data: Optional[dict] = None
    tokens_used: int = 0
    model_used: Optional[str] = None


@router.post("/campaign-manager/initialize", response_model=CampaignActionResponse)
@limiter.limit("5/minute")
async def initialize_campaign(
    request: Request,
    body: CampaignInitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Initialize a new campaign from an approved proposal.
    
    Creates the campaign record, generates requirements checklist,
    and sets up the initial state.
    """
    context = AgentContext(
        db=db,
        user_id=current_user.id,
    )
    
    result = await campaign_manager_agent.initialize_campaign(
        context=context,
        proposal_id=body.proposal_id,
        user_id=current_user.id,
    )
    
    await db.commit()
    
    return CampaignActionResponse(
        success=result.success,
        message=result.message,
        data=result.data,
        tokens_used=result.tokens_used,
        model_used=result.model_used,
    )


@router.post("/campaign-manager/{campaign_id}/input", response_model=CampaignActionResponse)
async def process_campaign_input(
    campaign_id: UUID,
    request: CampaignUserInputRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Process user input for a campaign.
    
    Analyzes the input to determine which requirements it satisfies
    and updates the campaign state accordingly.
    
    If the campaign is being processed by a remote worker, the input
    is routed to that worker via the broker.
    """
    from app.services.broker_service import broker_service
    
    # First, check if this campaign is owned by a remote worker
    routed = await broker_service.route_user_input_to_campaign(
        db=db,
        campaign_id=campaign_id,
        message=request.message,
    )
    
    if routed:
        # Message was sent to remote worker
        # The worker will process it and send back a response
        return CampaignActionResponse(
            success=True,
            message="Input sent to remote worker for processing",
            data={"routed_to_remote_worker": True},
        )
    
    # Process locally
    context = AgentContext(
        db=db,
        user_id=current_user.id,
    )
    
    result = await campaign_manager_agent.process_user_input(
        context=context,
        campaign_id=campaign_id,
        user_message=request.message,
    )
    
    return CampaignActionResponse(
        success=result.success,
        message=result.message,
        data=result.data,
        tokens_used=result.tokens_used,
        model_used=result.model_used,
    )


@router.get("/campaign-manager/{campaign_id}/status", response_model=CampaignActionResponse)
async def get_campaign_status(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get current status of a campaign."""
    context = AgentContext(
        db=db,
        user_id=current_user.id,
    )
    
    result = await campaign_manager_agent.get_campaign_status(
        context=context,
        campaign_id=campaign_id,
    )
    
    return CampaignActionResponse(
        success=result.success,
        message=result.message,
        data=result.data,
    )


@router.post("/campaign-manager/{campaign_id}/pause", response_model=CampaignActionResponse)
async def pause_campaign(
    campaign_id: UUID,
    reason: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pause an active campaign."""
    context = AgentContext(
        db=db,
        user_id=current_user.id,
    )
    
    result = await campaign_manager_agent.pause_campaign(
        context=context,
        campaign_id=campaign_id,
        reason=reason,
    )
    
    return CampaignActionResponse(
        success=result.success,
        message=result.message,
        data=result.data,
    )


@router.post("/campaign-manager/{campaign_id}/resume", response_model=CampaignActionResponse)
async def resume_campaign(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resume a paused campaign."""
    context = AgentContext(
        db=db,
        user_id=current_user.id,
    )
    
    result = await campaign_manager_agent.resume_campaign(
        context=context,
        campaign_id=campaign_id,
    )
    
    return CampaignActionResponse(
        success=result.success,
        message=result.message,
        data=result.data,
    )


class CampaignTerminateRequest(BaseModel):
    """Request to terminate a campaign."""
    reason: str = Field(..., min_length=1, max_length=500)


@router.post("/campaign-manager/{campaign_id}/terminate", response_model=CampaignActionResponse)
async def terminate_campaign(
    campaign_id: UUID,
    request: CampaignTerminateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Terminate a campaign early."""
    context = AgentContext(
        db=db,
        user_id=current_user.id,
    )
    
    result = await campaign_manager_agent.terminate_campaign(
        context=context,
        campaign_id=campaign_id,
        reason=request.reason,
    )
    
    return CampaignActionResponse(
        success=result.success,
        message=result.message,
        data=result.data,
    )


@router.post("/campaign-manager/{campaign_id}/step", response_model=CampaignActionResponse)
async def execute_campaign_step(
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Execute the next step in a campaign.
    
    This is typically called by the scheduler but can be manually triggered.
    """
    context = AgentContext(
        db=db,
        user_id=current_user.id,
    )
    
    result = await campaign_manager_agent.execute_campaign_step(
        context=context,
        campaign_id=campaign_id,
    )
    
    return CampaignActionResponse(
        success=result.success,
        message=result.message,
        data=result.data,
        tokens_used=result.tokens_used,
        model_used=result.model_used,
    )


# =============================================================================
# Agent Scheduler Endpoints
# =============================================================================
# These endpoints manage agent scheduling, status, and budgets

from app.api.endpoints.agent_scheduler import (
    list_agents,
    get_agent,
    update_agent,
    pause_agent,
    resume_agent,
    trigger_agent,
    get_agent_budget,
    update_agent_budget,
    list_agent_runs,
    list_all_recent_runs,
    get_agent_statistics,
)

# Mount scheduler endpoints
router.add_api_route("/scheduler", list_agents, methods=["GET"], tags=["Agent Scheduler"])
router.add_api_route("/scheduler/{agent_slug}", get_agent, methods=["GET"], tags=["Agent Scheduler"])
router.add_api_route("/scheduler/{agent_slug}", update_agent, methods=["PATCH"], tags=["Agent Scheduler"])
router.add_api_route("/scheduler/{agent_slug}/pause", pause_agent, methods=["POST"], tags=["Agent Scheduler"])
router.add_api_route("/scheduler/{agent_slug}/resume", resume_agent, methods=["POST"], tags=["Agent Scheduler"])
router.add_api_route("/scheduler/{agent_slug}/trigger", trigger_agent, methods=["POST"], tags=["Agent Scheduler"])
router.add_api_route("/scheduler/{agent_slug}/budget", get_agent_budget, methods=["GET"], tags=["Agent Scheduler"])
router.add_api_route("/scheduler/{agent_slug}/budget", update_agent_budget, methods=["PATCH"], tags=["Agent Scheduler"])
router.add_api_route("/scheduler/{agent_slug}/runs", list_agent_runs, methods=["GET"], tags=["Agent Scheduler"])
router.add_api_route("/scheduler/{agent_slug}/stats", get_agent_statistics, methods=["GET"], tags=["Agent Scheduler"])
router.add_api_route("/scheduler/runs/recent", list_all_recent_runs, methods=["GET"], tags=["Agent Scheduler"])


# =============================================================================
# System Health & Recovery Endpoints
# =============================================================================

@router.get("/system/health", tags=["System"])
async def get_system_health(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Get current system health status (Admin only).
    
    Returns information about job queues, resources, agents, campaign workers, and any anomalies.
    """
    
    from app.services import job_queue_service
    from app.services import campaign_worker_service
    from app.services import campaign_lease_service
    from app.services.agent_scheduler_service import agent_scheduler_service
    from datetime import datetime, timezone
    
    # Get job queue health
    health = await job_queue_service.get_system_health_status(db)
    
    # Add agent status
    agents = await agent_scheduler_service.get_all_agents(db, include_disabled=True)
    agent_status = {}
    for agent in agents:
        agent_status[agent.slug] = {
            "status": agent.status.value if agent.status else "unknown",
            "is_enabled": agent.is_enabled,
            "last_run": agent.last_run_at.isoformat() if agent.last_run_at else None,
        }
    
    health["agents"] = agent_status
    
    # Add campaign worker stats
    worker_stats = await campaign_worker_service.get_worker_stats(db)
    health["campaign_workers"] = worker_stats
    
    # Add campaign lease stats
    expired_leases = await campaign_lease_service.get_expired_leases(db, include_grace_period=False)
    claimable_campaigns = await campaign_lease_service.get_claimable_campaigns(db, limit=100)
    health["campaign_leases"] = {
        "expired_leases": len(expired_leases),
        "claimable_campaigns": len(claimable_campaigns),
    }
    
    health["timestamp"] = utc_now().isoformat()
    
    return health


@router.post("/system/recover", tags=["System"])
async def trigger_system_recovery(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Manually trigger system recovery (Admin only).
    
    This cleans up:
    - Stale job queue entries
    - Stuck agents
    - Orphaned resources
    - Stale tool executions
    
    Normally runs automatically on worker startup, but can be triggered manually.
    """
    
    from app.services import job_queue_service
    from app.services.agent_scheduler_service import agent_scheduler_service
    from app.models import ToolExecution, ToolExecutionStatus
    from app.models.agent_scheduler import AgentStatus
    from app.core.datetime_utils import utc_now, ensure_utc
    from datetime import timedelta
    from sqlalchemy import select
    
    results = {
        "success": True,
        "job_queue_recovery": {},
        "agent_recovery": {},
        "agent_run_recovery": {},
        "tool_execution_recovery": {},
        "timestamp": utc_now().isoformat(),
    }
    
    logger.info("=" * 60)
    logger.info("MANUAL SYSTEM RECOVERY - Beginning recovery process")
    logger.info("=" * 60)
    
    # 1. Recover job queue (stale running/queued jobs, orphaned resources)
    # Uses per-job expected_duration_minutes for threshold calculation
    job_recovery = await job_queue_service.recover_stale_jobs(db)
    results["job_queue_recovery"] = job_recovery
    
    # 2. Recover stuck agents (using per-agent expected duration)
    agents = await agent_scheduler_service.get_all_agents(db, include_disabled=True)
    agents_recovered = 0
    default_agent_threshold = 30  # minutes
    staleness_padding = 1.5  # Same as job recovery
    
    for agent in agents:
        if agent.status == AgentStatus.RUNNING:
            # Check if running too long based on expected duration
            if agent.last_run_at:
                last_run = ensure_utc(agent.last_run_at)
                running_time_minutes = (utc_now() - last_run).total_seconds() / 60
                
                # Use agent's expected duration if set, otherwise default
                expected = agent.expected_run_duration_minutes or default_agent_threshold
                threshold_minutes = int(expected * staleness_padding)
                
                if running_time_minutes > threshold_minutes:
                    logger.warning(
                        f"Recovery: Agent {agent.slug} was stuck in RUNNING status for {running_time_minutes:.1f} min "
                        f"(expected ~{expected} min, threshold {threshold_minutes} min)"
                    )
                    agent.status = AgentStatus.IDLE
                    agents_recovered += 1
    
    if agents_recovered > 0:
        await db.commit()
        logger.info(f"Recovery: Reset {agents_recovered} stuck agents to IDLE")
    
    results["agent_recovery"] = {"agents_recovered": agents_recovered}
    
    # 2b. Recover stale agent_runs (stuck in PENDING or RUNNING)
    from app.models.agent_scheduler import AgentRun, AgentRunStatus
    
    # Agent runs stuck for more than 4 hours are definitely stale
    run_threshold_time = utc_now() - timedelta(hours=4)
    
    stale_runs_result = await db.execute(
        select(AgentRun)
        .where(AgentRun.status.in_([AgentRunStatus.PENDING, AgentRunStatus.RUNNING]))
        .where(AgentRun.created_at < run_threshold_time)
    )
    stale_runs = list(stale_runs_result.scalars().all())
    
    runs_recovered = 0
    for run in stale_runs:
        old_status = run.status
        run.status = AgentRunStatus.FAILED
        run.error_message = f"SYSTEM_RECOVERY: Agent run was interrupted (was {old_status.value}, created {run.created_at})"
        run.completed_at = utc_now()
        runs_recovered += 1
        logger.warning(f"Recovery: Failed stale agent run {run.id} (was {old_status.value})")
    
    if runs_recovered > 0:
        await db.commit()
        logger.info(f"Recovery: Failed {runs_recovered} stale agent runs")
    
    results["agent_run_recovery"] = {"runs_recovered": runs_recovered}
    
    # 3. Recover stuck tool executions
    # Tool executions don't have expected duration yet, use fixed threshold
    threshold_time = utc_now() - timedelta(minutes=30)
    
    stale_executions_result = await db.execute(
        select(ToolExecution)
        .where(ToolExecution.status.in_([
            ToolExecutionStatus.PENDING,
            ToolExecutionStatus.RUNNING
        ]))
        .where(ToolExecution.created_at < threshold_time)
    )
    stale_executions = list(stale_executions_result.scalars().all())
    
    executions_recovered = 0
    for execution in stale_executions:
        execution.status = ToolExecutionStatus.FAILED
        execution.error_message = f"SYSTEM_RECOVERY: Execution was interrupted (created {execution.created_at})"
        execution.completed_at = utc_now()
        executions_recovered += 1
        logger.warning(f"Recovery: Failed stale tool execution {execution.id}")
    
    if executions_recovered > 0:
        await db.commit()
        logger.info(f"Recovery: Failed {executions_recovered} stale tool executions")
    
    results["tool_execution_recovery"] = {"executions_recovered": executions_recovered}
    
    logger.info("=" * 60)
    logger.info(f"MANUAL SYSTEM RECOVERY - Complete: {results}")
    logger.info("=" * 60)
    
    return results

