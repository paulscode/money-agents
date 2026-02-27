"""Campaign Action Service - Parse and execute actions from AI responses.

This service handles the <campaign_action> tags that the Campaign Discussion
agent can include in its responses.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import (
    Campaign,
    CampaignStatus,
    User,
    UserInputRequest,
    InputStatus,
    TaskStream,
    CampaignTask,
    TaskStatus,
)

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security.injection")

# Valid status transitions for campaign actions.
# If a current status is not listed, no transitions are allowed via campaign actions.
# This prevents LLM-injected actions from making dangerous transitions
# (e.g., jumping straight to COMPLETED or TERMINATED).
ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    CampaignStatus.INITIALIZING.value: {
        CampaignStatus.REQUIREMENTS_GATHERING.value,
        CampaignStatus.PAUSED.value,
        CampaignStatus.TERMINATED.value,
    },
    CampaignStatus.REQUIREMENTS_GATHERING.value: {
        CampaignStatus.EXECUTING.value,
        CampaignStatus.PAUSED.value,
        CampaignStatus.TERMINATED.value,
    },
    CampaignStatus.EXECUTING.value: {
        CampaignStatus.MONITORING.value,
        CampaignStatus.PAUSED.value,
        CampaignStatus.WAITING_FOR_INPUTS.value,
        CampaignStatus.TERMINATED.value,
    },
    CampaignStatus.MONITORING.value: {
        CampaignStatus.EXECUTING.value,
        CampaignStatus.PAUSED.value,
        CampaignStatus.COMPLETED.value,
        CampaignStatus.TERMINATED.value,
    },
    CampaignStatus.WAITING_FOR_INPUTS.value: {
        CampaignStatus.EXECUTING.value,
        CampaignStatus.PAUSED.value,
        CampaignStatus.TERMINATED.value,
    },
    CampaignStatus.ACTIVE.value: {
        CampaignStatus.PAUSED.value,
        CampaignStatus.COMPLETED.value,
        CampaignStatus.TERMINATED.value,
    },
    CampaignStatus.PAUSED.value: {
        CampaignStatus.EXECUTING.value,
        CampaignStatus.MONITORING.value,
        CampaignStatus.ACTIVE.value,
        CampaignStatus.TERMINATED.value,
    },
    CampaignStatus.PAUSED_FAILOVER.value: {
        CampaignStatus.EXECUTING.value,
        CampaignStatus.PAUSED.value,
        CampaignStatus.TERMINATED.value,
    },
    # Terminal states: COMPLETED, TERMINATED, FAILED — no outgoing transitions
}


class ActionType(str, Enum):
    """Supported campaign action types."""
    PROVIDE_INPUT = "provide_input"
    UPDATE_STATUS = "update_status"
    ADD_NOTE = "add_note"
    PRIORITIZE_STREAM = "prioritize_stream"
    SKIP_TASK = "skip_task"


@dataclass
class CampaignAction:
    """Represents a parsed campaign action from AI response."""
    action_type: ActionType
    content: str  # The content/value inside the action tags
    attributes: Dict[str, str] = field(default_factory=dict)
    raw_xml: str = ""  # The original XML for display
    
    # Execution state
    action_id: str = ""  # Generated unique ID for tracking
    status: str = "pending"  # pending, applied, rejected, failed
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "action_id": self.action_id,
            "action_type": self.action_type.value,
            "content": self.content,
            "attributes": self.attributes,
            "raw_xml": self.raw_xml,
            "status": self.status,
            "error_message": self.error_message,
            "preview": self.get_preview(),
        }
    
    def get_preview(self) -> str:
        """Generate a human-readable preview of the action."""
        if self.action_type == ActionType.PROVIDE_INPUT:
            key = self.attributes.get("key", "unknown")
            return f"Set input '{key}' to: {self.content[:100]}{'...' if len(self.content) > 100 else ''}"
        
        elif self.action_type == ActionType.UPDATE_STATUS:
            new_status = self.attributes.get("new_status", "unknown")
            reason = self.content[:100] if self.content else "No reason provided"
            return f"Change campaign status to '{new_status}': {reason}"
        
        elif self.action_type == ActionType.ADD_NOTE:
            category = self.attributes.get("category", "note")
            return f"Add {category} note: {self.content[:100]}{'...' if len(self.content) > 100 else ''}"
        
        elif self.action_type == ActionType.PRIORITIZE_STREAM:
            stream_name = self.attributes.get("stream_name", "unknown")
            return f"Prioritize stream '{stream_name}': {self.content[:100] if self.content else 'No reason'}"
        
        elif self.action_type == ActionType.SKIP_TASK:
            task_id = self.attributes.get("task_id", "unknown")
            reason = self.attributes.get("reason", self.content or "No reason")
            return f"Skip task '{task_id}': {reason[:100]}"
        
        return f"Unknown action: {self.action_type}"


@dataclass 
class ActionParseResult:
    """Result of parsing actions from AI response."""
    clean_content: str  # Response content with action tags removed
    actions: List[CampaignAction] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)


class CampaignActionService:
    """
    Service for parsing and executing campaign actions from AI responses.
    
    Action flow:
    1. AI includes <campaign_action> tags in response
    2. Parser extracts actions and clean content
    3. Actions are sent to frontend for preview/confirmation
    4. User confirms → actions are executed (with server-side confirmation check)
    5. Results are sent back to frontend
    
    Security: Actions are tracked with confirmation state to prevent
    replay attacks and ensure each action is only executed once.
    """
    
    # Regex pattern for extracting campaign_action tags
    # Matches: <campaign_action type="..." attr="...">content</campaign_action>
    ACTION_PATTERN = re.compile(
        r'<campaign_action\s+([^>]*)>(.*?)</campaign_action>',
        re.DOTALL | re.IGNORECASE
    )
    
    # Pattern for extracting attributes from the tag
    ATTR_PATTERN = re.compile(r'(\w+)=["\']([^"\']*)["\']')
    
    # Maximum actions per response to prevent abuse
    MAX_ACTIONS_PER_RESPONSE = 10
    MAX_ACTION_CONTENT_LENGTH = 50_000  # Truncate individual action content

    # Class-level shared set for anti-replay persistence when Redis is unavailable.
    # Shared across all instances so replay detection works even with multiple service instances.
    _memory_executed_ids: set[str] = set()
    _redis_client = None  # lazily initialised
    _REDIS_PREFIX = "campaign_action:executed:"
    _REDIS_TTL = 86400  # 24 hours
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self._action_counter = 0
        self._redis = self._get_redis()
    
    @classmethod
    def _get_redis(cls):
        """Lazy-init a Redis client for anti-replay persistence (GAP-7).
        
        Uses Redis DB 5 to avoid collisions with app(0), broker(1),
        results(2), rate-limit(3), and token-blocklist(4).
        Falls back to in-memory set when Redis is unavailable.
        """
        if cls._redis_client is not None:
            return cls._redis_client
        import os
        redis_url = os.environ.get("REDIS_URL", "")
        if not redis_url:
            return None
        try:
            import redis as _redis_mod
            base = redis_url.rsplit("/", 1)[0]
            cls._redis_client = _redis_mod.Redis.from_url(
                f"{base}/5", decode_responses=True, socket_connect_timeout=2,
            )
            cls._redis_client.ping()
            logger.info("Campaign action anti-replay using Redis (DB 5)")
            return cls._redis_client
        except Exception as exc:
            logger.warning(
                "Redis unavailable for action anti-replay, using in-memory fallback: %s", exc
            )
            return None
    
    def _is_action_executed(self, action_id: str) -> bool:
        """Check if an action has already been executed (anti-replay)."""
        if self._redis:
            try:
                return bool(self._redis.exists(f"{self._REDIS_PREFIX}{action_id}"))
            except Exception:
                pass
        return action_id in self._memory_executed_ids

    def _mark_action_executed(self, action_id: str) -> None:
        """Mark an action as executed to prevent replay."""
        self._memory_executed_ids.add(action_id)
        if self._redis:
            try:
                self._redis.setex(
                    f"{self._REDIS_PREFIX}{action_id}", self._REDIS_TTL, "1"
                )
            except Exception:
                pass

    def parse_response(self, response_content: str) -> ActionParseResult:
        """
        Parse a response and extract any campaign actions.
        
        Returns the clean content (actions removed) and list of parsed actions.
        """
        actions: List[CampaignAction] = []
        parse_errors: List[str] = []
        
        # Find all action tags
        matches = list(self.ACTION_PATTERN.finditer(response_content))
        
        # Security: limit the number of actions per response
        if len(matches) > self.MAX_ACTIONS_PER_RESPONSE:
            parse_errors.append(
                f"Too many actions ({len(matches)}); max is {self.MAX_ACTIONS_PER_RESPONSE}. "
                f"Only the first {self.MAX_ACTIONS_PER_RESPONSE} will be processed."
            )
            matches = matches[:self.MAX_ACTIONS_PER_RESPONSE]
        
        for match in matches:
            try:
                attrs_str = match.group(1)
                content = match.group(2).strip()
                raw_xml = match.group(0)
                
                # Truncate oversized content
                if len(content) > self.MAX_ACTION_CONTENT_LENGTH:
                    content = content[:self.MAX_ACTION_CONTENT_LENGTH]
                    parse_errors.append(
                        f"Action content truncated from {len(match.group(2).strip())} to "
                        f"{self.MAX_ACTION_CONTENT_LENGTH} characters"
                    )
                
                # Parse attributes
                attributes = dict(self.ATTR_PATTERN.findall(attrs_str))
                
                # Get action type
                action_type_str = attributes.get("type", "")
                if not action_type_str:
                    parse_errors.append(f"Action missing 'type' attribute: {raw_xml[:50]}...")
                    continue
                
                try:
                    action_type = ActionType(action_type_str)
                except ValueError:
                    parse_errors.append(f"Unknown action type '{action_type_str}'")
                    continue
                
                # Generate unique action ID
                self._action_counter += 1
                action_id = f"action_{self._action_counter}_{utc_now().timestamp()}"
                
                # Create action object
                action = CampaignAction(
                    action_type=action_type,
                    content=content,
                    attributes=attributes,
                    raw_xml=raw_xml,
                    action_id=action_id,
                )
                actions.append(action)
                
            except Exception as e:
                parse_errors.append(f"Failed to parse action: {str(e)}")
        
        # Remove action tags from content for clean display
        clean_content = self.ACTION_PATTERN.sub('', response_content).strip()
        
        # Clean up any double newlines left by removal
        clean_content = re.sub(r'\n{3,}', '\n\n', clean_content)
        
        return ActionParseResult(
            clean_content=clean_content,
            actions=actions,
            parse_errors=parse_errors,
        )
    
    async def execute_action(
        self,
        campaign_id: UUID,
        action: CampaignAction,
        user_id: UUID,
    ) -> Tuple[bool, str]:
        """
        Execute a single campaign action.
        
        Returns (success, message).
        """
        try:
            if action.action_type == ActionType.PROVIDE_INPUT:
                return await self._handle_provide_input(campaign_id, action, user_id)
            
            elif action.action_type == ActionType.UPDATE_STATUS:
                return await self._handle_update_status(campaign_id, action)
            
            elif action.action_type == ActionType.ADD_NOTE:
                return await self._handle_add_note(campaign_id, action, user_id)
            
            elif action.action_type == ActionType.PRIORITIZE_STREAM:
                return await self._handle_prioritize_stream(campaign_id, action)
            
            elif action.action_type == ActionType.SKIP_TASK:
                return await self._handle_skip_task(campaign_id, action)
            
            else:
                return False, f"Unknown action type: {action.action_type}"
                
        except Exception as e:
            logger.exception(f"Failed to execute action {action.action_id}")
            return False, f"Error executing action: {str(e)}"
    
    async def execute_actions(
        self,
        campaign_id: UUID,
        actions: List[CampaignAction],
        user_id: UUID,
    ) -> List[Dict[str, Any]]:
        """
        Execute multiple actions and return results.
        
        Security checks:
        - Campaign ownership: user must own the campaign or be admin (GAP-11)
        - Anti-replay: each action_id can only be executed once
        - Rate limiting: max actions per call enforced
        
        Returns list of result dicts with action_id, success, message.
        """
        # GAP-11: verify user owns this campaign (or is admin)
        campaign_result = await self.db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        campaign = campaign_result.scalar_one_or_none()
        if not campaign:
            return [{"action_id": a.action_id, "action_type": a.action_type.value,
                      "success": False, "message": "Campaign not found"}
                     for a in actions]
        
        if campaign.user_id != user_id:
            # Check if user is admin
            user_result = await self.db.execute(
                select(User).where(User.id == user_id)
            )
            user = user_result.scalar_one_or_none()
            if not user or user.role != "admin":
                logger.warning(
                    "OWNERSHIP_BLOCKED: user %s tried to execute actions on campaign %s (owner: %s)",
                    user_id, campaign_id, campaign.user_id,
                )
                return [{"action_id": a.action_id, "action_type": a.action_type.value,
                          "success": False, "message": "Not authorized for this campaign"}
                         for a in actions]
        
        results = []
        
        for action in actions:
            # Anti-replay: reject already-executed actions
            if self._is_action_executed(action.action_id):
                logger.warning(
                    f"REPLAY_BLOCKED: action {action.action_id} already executed"
                )
                results.append({
                    "action_id": action.action_id,
                    "action_type": action.action_type.value,
                    "success": False,
                    "message": "Action already executed (replay blocked)",
                })
                continue
            
            success, message = await self.execute_action(campaign_id, action, user_id)
            action.status = "applied" if success else "failed"
            action.error_message = None if success else message
            
            # Mark as executed to prevent replay
            self._mark_action_executed(action.action_id)
            
            results.append({
                "action_id": action.action_id,
                "action_type": action.action_type.value,
                "success": success,
                "message": message,
            })
        
        # Commit all changes at once
        await self.db.flush()
        
        return results
    
    # =========================================================================
    # Action Handlers
    # =========================================================================
    
    MAX_INPUT_LENGTH = 10_000  # Maximum length for user-provided input values
    
    async def _handle_provide_input(
        self,
        campaign_id: UUID,
        action: CampaignAction,
        user_id: UUID,
    ) -> Tuple[bool, str]:
        """Handle provide_input action."""
        from app.services.stream_executor_service import provide_user_input
        from app.services.prompt_injection_guard import sanitize_external_content
        
        input_key = action.attributes.get("key")
        if not input_key:
            return False, "Missing 'key' attribute for provide_input action"
        
        value = action.content
        if not value:
            return False, "No value provided for input"
        
        # Enforce length limit
        if len(value) > self.MAX_INPUT_LENGTH:
            value = value[:self.MAX_INPUT_LENGTH]
        
        # Sanitize external content
        value, _ = sanitize_external_content(value, source="provide_input")
        
        # Use existing service to provide the input
        success = await provide_user_input(
            db=self.db,
            campaign_id=campaign_id,
            input_key=input_key,
            value=value,
            user_id=user_id,
        )
        
        if success:
            return True, f"Successfully set input '{input_key}'"
        else:
            return False, f"Input '{input_key}' not found or already provided"
    
    async def _handle_update_status(
        self,
        campaign_id: UUID,
        action: CampaignAction,
    ) -> Tuple[bool, str]:
        """Handle update_status action."""
        new_status_str = action.attributes.get("new_status")
        if not new_status_str:
            return False, "Missing 'new_status' attribute"
        
        # Validate status
        try:
            new_status = CampaignStatus(new_status_str)
        except ValueError:
            valid = [s.value for s in CampaignStatus]
            return False, f"Invalid status '{new_status_str}'. Valid: {valid}"
        
        # Get campaign
        result = await self.db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            return False, "Campaign not found"
        
        old_status = campaign.status
        
        # Validate status transition
        allowed = ALLOWED_STATUS_TRANSITIONS.get(old_status.value, set())
        if new_status.value not in allowed:
            security_logger.warning(
                "Blocked invalid status transition %s -> %s for campaign %s",
                old_status.value,
                new_status.value,
                campaign_id,
            )
            return False, (
                f"Status transition from '{old_status.value}' to "
                f"'{new_status.value}' is not allowed"
            )
        
        campaign.status = new_status
        
        return True, f"Campaign status changed from '{old_status.value}' to '{new_status.value}'"
    
    async def _handle_add_note(
        self,
        campaign_id: UUID,
        action: CampaignAction,
        user_id: UUID,
    ) -> Tuple[bool, str]:
        """
        Handle add_note action.
        
        Stores notes in the campaign's performance_data field until
        a dedicated campaign_notes table is created.
        """
        category = action.attributes.get("category", "note")
        content = action.content
        
        if not content:
            return False, "No note content provided"
        
        # Get campaign
        result = await self.db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            return False, "Campaign not found"
        
        # Store note in performance_data for now
        # TODO: Create proper campaign_notes table as per design doc
        performance_data = campaign.performance_data or {}
        notes = performance_data.get("notes", [])
        notes.append({
            "category": category,
            "content": content,
            "created_at": utc_now().isoformat(),
            "created_by": "agent",
        })
        performance_data["notes"] = notes
        campaign.performance_data = performance_data
        
        return True, f"Added {category} note to campaign"
    
    async def _handle_prioritize_stream(
        self,
        campaign_id: UUID,
        action: CampaignAction,
    ) -> Tuple[bool, str]:
        """Handle prioritize_stream action."""
        stream_name = action.attributes.get("stream_name")
        if not stream_name:
            return False, "Missing 'stream_name' attribute"
        
        # Find the stream
        result = await self.db.execute(
            select(TaskStream).where(
                TaskStream.campaign_id == campaign_id,
                TaskStream.name == stream_name,
            )
        )
        stream = result.scalar_one_or_none()
        
        if not stream:
            return False, f"Stream '{stream_name}' not found in campaign"
        
        # Increase priority (lower number = higher priority)
        # Set to 0 to make it highest priority
        old_priority = stream.priority or 10
        stream.priority = 0
        
        return True, f"Stream '{stream_name}' priority increased (was {old_priority}, now 0)"
    
    async def _handle_skip_task(
        self,
        campaign_id: UUID,
        action: CampaignAction,
    ) -> Tuple[bool, str]:
        """Handle skip_task action."""
        task_id_str = action.attributes.get("task_id")
        if not task_id_str:
            return False, "Missing 'task_id' attribute"
        
        try:
            task_id = UUID(task_id_str)
        except ValueError:
            return False, f"Invalid task_id format: {task_id_str}"
        
        # Find the task
        result = await self.db.execute(
            select(CampaignTask).where(CampaignTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        
        if not task:
            return False, f"Task '{task_id_str}' not found"
        
        # Verify task belongs to this campaign's stream
        result = await self.db.execute(
            select(TaskStream).where(TaskStream.id == task.stream_id)
        )
        stream = result.scalar_one_or_none()
        
        if not stream or stream.campaign_id != campaign_id:
            return False, "Task does not belong to this campaign"
        
        # Skip the task by marking it as completed with skip flag
        reason = action.attributes.get("reason", action.content or "Skipped by AI assistant")
        old_status = task.status
        task.status = TaskStatus.SKIPPED
        task.result = {"skipped": True, "reason": reason}
        
        return True, f"Task '{task.title}' skipped (was {old_status.value})"
