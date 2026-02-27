"""Context Compression Service - Smart context management for campaign discussions.

This service provides:
1. Token counting for accurate context budget management
2. Keyword extraction and intent classification
3. Context compression and summarization
4. Snapshot creation for historical data
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

import tiktoken
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models import Campaign, CampaignTask, TaskStream, Message
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)


# =============================================================================
# Token Counting
# =============================================================================

class TokenCounter:
    """
    Accurate token counting using tiktoken.
    
    Uses cl100k_base encoding (GPT-4, Claude compatible).
    """
    
    _encoder: Optional[tiktoken.Encoding] = None
    
    @classmethod
    def get_encoder(cls) -> tiktoken.Encoding:
        """Get or create the tiktoken encoder (singleton)."""
        if cls._encoder is None:
            cls._encoder = tiktoken.get_encoding("cl100k_base")
        return cls._encoder
    
    @classmethod
    def count_tokens(cls, text: str) -> int:
        """Count tokens in a text string."""
        if not text:
            return 0
        encoder = cls.get_encoder()
        return len(encoder.encode(text))
    
    @classmethod
    def count_messages_tokens(cls, messages: List[Dict[str, str]]) -> int:
        """
        Count tokens in a list of chat messages.
        
        Accounts for message formatting overhead (~4 tokens per message).
        """
        total = 0
        for msg in messages:
            # Base overhead per message
            total += 4
            total += cls.count_tokens(msg.get("role", ""))
            total += cls.count_tokens(msg.get("content", ""))
        # Overhead for the conversation structure
        total += 2
        return total
    
    @classmethod
    def truncate_to_tokens(cls, text: str, max_tokens: int) -> str:
        """Truncate text to fit within a token limit."""
        if not text:
            return text
        
        encoder = cls.get_encoder()
        tokens = encoder.encode(text)
        
        if len(tokens) <= max_tokens:
            return text
        
        # Truncate and decode
        truncated_tokens = tokens[:max_tokens]
        return encoder.decode(truncated_tokens) + "..."


# =============================================================================
# Intent Classification
# =============================================================================

class QueryIntent(str, Enum):
    """Classification of user query intent."""
    STATUS = "status"           # "How's it going?" "What's the progress?"
    BLOCKER = "blocker"         # "Why is X blocked?" "What's holding things up?"
    TASK_DETAIL = "task_detail" # "Tell me about task X" "What does Y do?"
    INPUT_HELP = "input_help"   # "Help me with inputs" "What should I put for X?"
    HISTORY = "history"         # "What happened?" "Show me the history"
    BUDGET = "budget"           # "How much have we spent?" "Budget status?"
    STRATEGY = "strategy"       # "What should we do next?" "Any suggestions?"
    ACTION = "action"           # "Fill in X" "Skip task Y" "Pause the campaign"
    GENERAL = "general"         # Default - general discussion


@dataclass
class QueryAnalysis:
    """Result of analyzing a user query."""
    intent: QueryIntent
    keywords: Set[str]
    mentioned_streams: Set[str]
    mentioned_tasks: Set[str]
    mentioned_inputs: Set[str]
    confidence: float  # 0.0 to 1.0
    needs_tier2: bool
    needs_tier3: bool


class ContextAnalyzer:
    """
    Analyzes user queries to determine context requirements.
    
    Uses keyword matching and pattern recognition for fast classification.
    """
    
    # Intent patterns - order matters (more specific first)
    INTENT_PATTERNS: List[Tuple[QueryIntent, List[str]]] = [
        (QueryIntent.ACTION, [
            r'\b(fill|set|provide|update|change|skip|pause|resume|cancel)\b',
            r'\b(apply|execute|do it|make it)\b',
        ]),
        (QueryIntent.BLOCKER, [
            r'\b(block|stuck|wait|hold|depend|need)\b',
            r'\bwhy.*(not|can\'t|won\'t|block)\b',
            r'\bwhat\'s (stopping|preventing|blocking)\b',
        ]),
        (QueryIntent.INPUT_HELP, [
            r'\b(input|value|fill in|provide|what should)\b',
            r'\bhelp.*(with|me|decide)\b',
            r'\b(suggest|recommend).*(value|input)\b',
        ]),
        (QueryIntent.TASK_DETAIL, [
            r'\b(task|step|what does|explain|tell me about)\b',
            r'\bwhat.*(do|does|mean)\b',
            r'\bdetail|specific\b',
        ]),
        (QueryIntent.HISTORY, [
            r'\b(history|happened|past|before|earlier|log)\b',
            r'\bwhat.*(happened|did|was)\b',
            r'\bshow.*(history|log|record)\b',
        ]),
        (QueryIntent.BUDGET, [
            r'\b(budget|cost|spend|spent|money|dollar|\$)\b',
            r'\bhow much\b',
            r'\b(afford|expense|price)\b',
        ]),
        (QueryIntent.STATUS, [
            r'\b(status|progress|going|doing|update)\b',
            r'\bhow.*(is|are|\'s)\b',
            r'\b(overview|summary)\b',
        ]),
        (QueryIntent.STRATEGY, [
            r'\b(strategy|plan|next|should|recommend|suggest)\b',
            r'\bwhat.*(next|now|do)\b',
            r'\b(advice|guidance|help)\b',
        ]),
    ]
    
    def __init__(self, campaign_context: Optional[Dict] = None):
        """
        Initialize with optional campaign context for entity matching.
        
        Args:
            campaign_context: Dict with 'streams', 'tasks', 'inputs' lists
        """
        self.stream_names: Set[str] = set()
        self.task_names: Set[str] = set()
        self.input_keys: Set[str] = set()
        
        if campaign_context:
            # Extract entity names for matching
            for stream in campaign_context.get("streams", []):
                name = stream.get("name", "")
                if name:
                    self.stream_names.add(name.lower())
                    # Also add without underscores
                    self.stream_names.add(name.replace("_", " ").lower())
            
            for task in campaign_context.get("tasks", []):
                title = task.get("title", "")
                if title:
                    self.task_names.add(title.lower())
            
            for inp in campaign_context.get("inputs", []):
                key = inp.get("key", "")
                if key:
                    self.input_keys.add(key.lower())
                    self.input_keys.add(key.replace("_", " ").lower())
    
    def analyze(self, query: str) -> QueryAnalysis:
        """
        Analyze a user query to determine context requirements.
        """
        query_lower = query.lower()
        
        # Extract keywords (nouns and important words)
        keywords = self._extract_keywords(query_lower)
        
        # Find mentioned entities
        mentioned_streams = self._find_mentions(query_lower, self.stream_names)
        mentioned_tasks = self._find_mentions(query_lower, self.task_names)
        mentioned_inputs = self._find_mentions(query_lower, self.input_keys)
        
        # Classify intent
        intent, confidence = self._classify_intent(query_lower)
        
        # Determine tier requirements
        needs_tier2 = (
            intent in (QueryIntent.TASK_DETAIL, QueryIntent.BLOCKER, QueryIntent.INPUT_HELP) or
            bool(mentioned_streams) or
            bool(mentioned_tasks) or
            bool(mentioned_inputs)
        )
        
        needs_tier3 = intent == QueryIntent.HISTORY
        
        return QueryAnalysis(
            intent=intent,
            keywords=keywords,
            mentioned_streams=mentioned_streams,
            mentioned_tasks=mentioned_tasks,
            mentioned_inputs=mentioned_inputs,
            confidence=confidence,
            needs_tier2=needs_tier2,
            needs_tier3=needs_tier3,
        )
    
    def _extract_keywords(self, text: str) -> Set[str]:
        """Extract important keywords from text."""
        # Remove common stop words and extract meaningful words
        stop_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
            'would', 'could', 'should', 'may', 'might', 'must', 'shall',
            'can', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by',
            'from', 'as', 'into', 'through', 'during', 'before', 'after',
            'above', 'below', 'between', 'under', 'again', 'further',
            'then', 'once', 'here', 'there', 'when', 'where', 'why',
            'how', 'all', 'each', 'few', 'more', 'most', 'other', 'some',
            'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so',
            'than', 'too', 'very', 'just', 'and', 'but', 'if', 'or',
            'because', 'until', 'while', 'about', 'against', 'this',
            'that', 'these', 'those', 'am', 'i', 'me', 'my', 'we', 'our',
            'you', 'your', 'he', 'him', 'his', 'she', 'her', 'it', 'its',
            'they', 'them', 'their', 'what', 'which', 'who', 'whom',
        }
        
        # Extract words (3+ chars)
        words = re.findall(r'\b[a-z]{3,}\b', text)
        
        return {w for w in words if w not in stop_words}
    
    def _find_mentions(self, text: str, entities: Set[str]) -> Set[str]:
        """Find which entities from the set are mentioned in text."""
        mentioned = set()
        for entity in entities:
            if entity in text:
                mentioned.add(entity)
        return mentioned
    
    def _classify_intent(self, text: str) -> Tuple[QueryIntent, float]:
        """Classify the intent of a query."""
        best_intent = QueryIntent.GENERAL
        best_score = 0.0
        
        for intent, patterns in self.INTENT_PATTERNS:
            score = 0.0
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    score += 1.0
            
            # Normalize by number of patterns
            score = score / len(patterns) if patterns else 0.0
            
            if score > best_score:
                best_score = score
                best_intent = intent
        
        # Minimum threshold
        if best_score < 0.3:
            return QueryIntent.GENERAL, 0.5
        
        return best_intent, min(best_score + 0.3, 1.0)


# =============================================================================
# Context Compression
# =============================================================================

@dataclass
class CompressionResult:
    """Result of compressing context data."""
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    summary: str
    preserved_count: int  # Number of recent items kept in full
    compressed_count: int  # Number of items summarized


class ContextCompressor:
    """
    Compresses campaign context data to fit within token budgets.
    
    Strategies:
    1. Rolling window summarization - Keep recent items full, summarize old
    2. Relevance-based filtering - Keep items matching keywords
    3. Grouping and batching - Group similar items together
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def compress_execution_history(
        self,
        executions: List[Dict],
        max_tokens: int = 2000,
        recent_count: int = 5,
        keywords: Optional[Set[str]] = None,
    ) -> CompressionResult:
        """
        Compress execution history while preserving recent and relevant items.
        """
        if not executions:
            return CompressionResult(
                original_tokens=0,
                compressed_tokens=0,
                compression_ratio=1.0,
                summary="No execution history.",
                preserved_count=0,
                compressed_count=0,
            )
        
        # Calculate original size
        original_text = self._format_executions_full(executions)
        original_tokens = TokenCounter.count_tokens(original_text)
        
        # If it fits, return as-is
        if original_tokens <= max_tokens:
            return CompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                compression_ratio=1.0,
                summary=original_text,
                preserved_count=len(executions),
                compressed_count=0,
            )
        
        # Split into recent (keep full) and old (summarize)
        recent = executions[:recent_count]
        old = executions[recent_count:]
        
        # Filter old by keywords if provided
        if keywords and old:
            relevant_old = [
                e for e in old
                if self._matches_keywords(e, keywords)
            ]
            old = relevant_old[:10]  # Cap at 10 relevant old items
        
        # Generate summary for old executions
        old_summary = self._summarize_executions(old) if old else ""
        
        # Format recent executions
        recent_text = self._format_executions_full(recent)
        
        # Combine
        if old_summary:
            compressed_text = f"**Recent Executions:**\n{recent_text}\n\n**Earlier Summary:** {old_summary}"
        else:
            compressed_text = recent_text
        
        compressed_tokens = TokenCounter.count_tokens(compressed_text)
        
        # If still too large, truncate
        if compressed_tokens > max_tokens:
            compressed_text = TokenCounter.truncate_to_tokens(compressed_text, max_tokens)
            compressed_tokens = max_tokens
        
        return CompressionResult(
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=compressed_tokens / original_tokens if original_tokens > 0 else 1.0,
            summary=compressed_text,
            preserved_count=len(recent),
            compressed_count=len(old),
        )
    
    async def compress_conversation_history(
        self,
        messages: List[Dict],
        max_tokens: int = 3000,
        recent_count: int = 10,
    ) -> CompressionResult:
        """
        Compress conversation history while preserving recent messages.
        """
        if not messages:
            return CompressionResult(
                original_tokens=0,
                compressed_tokens=0,
                compression_ratio=1.0,
                summary="",
                preserved_count=0,
                compressed_count=0,
            )
        
        # Calculate original size
        original_text = self._format_messages_full(messages)
        original_tokens = TokenCounter.count_tokens(original_text)
        
        if original_tokens <= max_tokens:
            return CompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                compression_ratio=1.0,
                summary=original_text,
                preserved_count=len(messages),
                compressed_count=0,
            )
        
        # Keep recent messages full
        recent = messages[-recent_count:] if len(messages) > recent_count else messages
        old = messages[:-recent_count] if len(messages) > recent_count else []
        
        # Summarize old messages
        old_summary = self._summarize_conversation(old) if old else ""
        
        # Format recent
        recent_text = self._format_messages_full(recent)
        
        # Combine
        if old_summary:
            compressed_text = f"**Previous conversation summary:** {old_summary}\n\n**Recent messages:**\n{recent_text}"
        else:
            compressed_text = recent_text
        
        compressed_tokens = TokenCounter.count_tokens(compressed_text)
        
        if compressed_tokens > max_tokens:
            compressed_text = TokenCounter.truncate_to_tokens(compressed_text, max_tokens)
            compressed_tokens = max_tokens
        
        return CompressionResult(
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=compressed_tokens / original_tokens if original_tokens > 0 else 1.0,
            summary=compressed_text,
            preserved_count=len(recent),
            compressed_count=len(old),
        )
    
    async def compress_task_details(
        self,
        tasks: List[Dict],
        max_tokens: int = 1500,
        relevant_ids: Optional[Set[str]] = None,
    ) -> CompressionResult:
        """
        Compress task details, keeping relevant tasks expanded.
        """
        if not tasks:
            return CompressionResult(
                original_tokens=0,
                compressed_tokens=0,
                compression_ratio=1.0,
                summary="No tasks.",
                preserved_count=0,
                compressed_count=0,
            )
        
        original_text = self._format_tasks_full(tasks)
        original_tokens = TokenCounter.count_tokens(original_text)
        
        if original_tokens <= max_tokens:
            return CompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                compression_ratio=1.0,
                summary=original_text,
                preserved_count=len(tasks),
                compressed_count=0,
            )
        
        # Split into relevant (keep full) and others (collapse)
        if relevant_ids:
            relevant = [t for t in tasks if t.get("id") in relevant_ids]
            others = [t for t in tasks if t.get("id") not in relevant_ids]
        else:
            # Keep incomplete/blocked tasks expanded
            relevant = [t for t in tasks if t.get("status") in ("pending", "running", "blocked")]
            others = [t for t in tasks if t.get("status") in ("completed", "skipped", "failed")]
        
        # Format relevant tasks fully
        relevant_text = self._format_tasks_full(relevant) if relevant else ""
        
        # Collapse others to summary
        others_summary = self._collapse_tasks(others) if others else ""
        
        if relevant_text and others_summary:
            compressed_text = f"{relevant_text}\n\n**Other tasks:** {others_summary}"
        elif relevant_text:
            compressed_text = relevant_text
        else:
            compressed_text = others_summary
        
        compressed_tokens = TokenCounter.count_tokens(compressed_text)
        
        if compressed_tokens > max_tokens:
            compressed_text = TokenCounter.truncate_to_tokens(compressed_text, max_tokens)
            compressed_tokens = max_tokens
        
        return CompressionResult(
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=compressed_tokens / original_tokens if original_tokens > 0 else 1.0,
            summary=compressed_text,
            preserved_count=len(relevant),
            compressed_count=len(others),
        )
    
    # =========================================================================
    # Formatting Helpers
    # =========================================================================
    
    def _format_executions_full(self, executions: List[Dict]) -> str:
        """Format executions with full details."""
        lines = []
        for ex in executions:
            status = ex.get("status", "unknown")
            task = ex.get("task_title", "Unknown task")
            timestamp = ex.get("completed_at", ex.get("started_at", ""))
            result = ex.get("result_summary", "")
            
            line = f"- [{status}] {task}"
            if timestamp:
                line += f" ({timestamp})"
            if result:
                line += f": {result[:100]}"
            lines.append(line)
        
        return "\n".join(lines)
    
    def _summarize_executions(self, executions: List[Dict]) -> str:
        """Create a summary of executions."""
        if not executions:
            return ""
        
        completed = sum(1 for e in executions if e.get("status") == "completed")
        failed = sum(1 for e in executions if e.get("status") == "failed")
        total = len(executions)
        
        return f"{total} earlier executions ({completed} completed, {failed} failed)"
    
    def _format_messages_full(self, messages: List[Dict]) -> str:
        """Format messages with full content."""
        lines = []
        for msg in messages:
            role = msg.get("role", msg.get("sender_type", "unknown"))
            content = msg.get("content", "")[:500]  # Limit individual messages
            lines.append(f"**{role}:** {content}")
        return "\n\n".join(lines)
    
    def _summarize_conversation(self, messages: List[Dict]) -> str:
        """Create a summary of older conversation."""
        if not messages:
            return ""
        
        # Extract key topics/decisions
        topics = set()
        for msg in messages:
            content = msg.get("content", "").lower()
            # Look for decision-like language
            if any(word in content for word in ["decided", "agreed", "will", "should", "plan"]):
                # Extract a snippet
                topics.add(content[:50] + "...")
        
        if topics:
            return f"Previously discussed: {'; '.join(list(topics)[:3])}"
        
        return f"({len(messages)} earlier messages)"
    
    def _format_tasks_full(self, tasks: List[Dict]) -> str:
        """Format tasks with full details."""
        lines = []
        for task in tasks:
            status = task.get("status", "unknown")
            title = task.get("title", "Unknown")
            desc = task.get("description", "")
            
            line = f"- [{status}] **{title}**"
            if desc:
                line += f": {desc[:100]}"
            lines.append(line)
        
        return "\n".join(lines)
    
    def _collapse_tasks(self, tasks: List[Dict]) -> str:
        """Collapse tasks to a brief summary."""
        if not tasks:
            return ""
        
        by_status = {}
        for task in tasks:
            status = task.get("status", "unknown")
            by_status[status] = by_status.get(status, 0) + 1
        
        parts = [f"{count} {status}" for status, count in by_status.items()]
        return ", ".join(parts)
    
    def _matches_keywords(self, item: Dict, keywords: Set[str]) -> bool:
        """Check if an item matches any keywords."""
        text = " ".join(str(v) for v in item.values() if isinstance(v, str)).lower()
        return any(kw in text for kw in keywords)


# =============================================================================
# Context Budget Manager
# =============================================================================

@dataclass
class ContextBudget:
    """Token budget allocation for context."""
    system_prompt: int = 2000
    tier1_core: int = 2000
    tier2_detailed: int = 4000
    tier3_historical: int = 2000
    conversation_history: int = 8000
    user_message: int = 2000
    response_buffer: int = 6000
    safety_margin: int = 6000
    
    @property
    def total(self) -> int:
        return (
            self.system_prompt +
            self.tier1_core +
            self.tier2_detailed +
            self.tier3_historical +
            self.conversation_history +
            self.user_message +
            self.response_buffer +
            self.safety_margin
        )


class ContextBudgetManager:
    """
    Manages token budgets for context building.
    
    Ensures total context stays within model limits while
    maximizing useful information.
    """
    
    # Model context windows
    MODEL_LIMITS = {
        "gpt-4": 8192,
        "gpt-4-turbo": 128000,
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
        "gpt-3.5-turbo": 16385,
        "claude-opus-4-6": 200000,
        "claude-sonnet-4-6": 200000,
        "claude-haiku-4-5": 200000,
        "claude-opus-4": 200000,
        "claude-sonnet-4": 200000,
        "claude-3-opus": 200000,
        "claude-3-sonnet": 200000,
        "claude-3-haiku": 200000,
        "claude-3-5-sonnet": 200000,
        "default": 32000,
    }
    
    def __init__(self, model: str = "default"):
        self.model = model
        self.limit = self.MODEL_LIMITS.get(model, self.MODEL_LIMITS["default"])
        self.budget = self._calculate_budget()
    
    def _calculate_budget(self) -> ContextBudget:
        """Calculate budget based on model limit."""
        # For larger models, we can be more generous
        if self.limit >= 100000:
            return ContextBudget(
                system_prompt=3000,
                tier1_core=3000,
                tier2_detailed=8000,
                tier3_historical=4000,
                conversation_history=16000,
                user_message=4000,
                response_buffer=10000,
                safety_margin=10000,
            )
        elif self.limit >= 32000:
            return ContextBudget()  # Default values
        else:
            # Smaller models - tighter budgets
            return ContextBudget(
                system_prompt=1500,
                tier1_core=1500,
                tier2_detailed=2000,
                tier3_historical=1000,
                conversation_history=4000,
                user_message=1000,
                response_buffer=2000,
                safety_margin=2000,
            )
    
    def get_available_budget(
        self,
        system_tokens: int = 0,
        tier1_tokens: int = 0,
        conversation_tokens: int = 0,
    ) -> Dict[str, int]:
        """
        Calculate remaining budget for each component.
        
        Returns dict with available tokens for each tier.
        """
        used = system_tokens + tier1_tokens + conversation_tokens
        remaining = self.limit - used - self.budget.response_buffer - self.budget.safety_margin
        
        return {
            "tier2": min(self.budget.tier2_detailed, remaining),
            "tier3": min(self.budget.tier3_historical, max(0, remaining - self.budget.tier2_detailed)),
            "total_remaining": remaining,
        }
    
    def check_overflow(self, total_tokens: int) -> bool:
        """Check if we're over budget."""
        return total_tokens > (self.limit - self.budget.response_buffer - self.budget.safety_margin)
    
    def get_compression_threshold(self) -> int:
        """Get threshold at which compression should trigger."""
        return int(self.limit * 0.8)


# =============================================================================
# Snapshot Service (for historical data)
# =============================================================================

@dataclass
class ContextSnapshot:
    """A compressed snapshot of historical context."""
    snapshot_type: str  # 'execution_history', 'conversation', 'tool_results'
    period_start: datetime
    period_end: datetime
    summary: str
    token_count: int
    item_count: int


class SnapshotService:
    """
    Creates and manages compressed snapshots of historical data.
    
    Note: This stores snapshots in campaign metadata for now.
    A proper implementation would use a dedicated table (campaign_context_snapshots).
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.compressor = ContextCompressor(db)
    
    async def create_execution_snapshot(
        self,
        campaign_id: UUID,
        executions: List[Dict],
        cutoff_date: Optional[datetime] = None,
    ) -> Optional[ContextSnapshot]:
        """
        Create a snapshot of execution history before cutoff date.
        """
        if not executions:
            return None
        
        # Default cutoff: 7 days ago
        if cutoff_date is None:
            cutoff_date = utc_now() - timedelta(days=7)
        
        # Filter old executions
        old_executions = [
            e for e in executions
            if self._parse_date(e.get("completed_at")) and 
               self._parse_date(e.get("completed_at")) < cutoff_date
        ]
        
        if not old_executions:
            return None
        
        # Compress
        result = await self.compressor.compress_execution_history(
            old_executions,
            max_tokens=1000,
            recent_count=0,  # We're only compressing old ones
        )
        
        # Determine period
        dates = [self._parse_date(e.get("completed_at")) for e in old_executions]
        dates = [d for d in dates if d]
        
        if not dates:
            return None
        
        return ContextSnapshot(
            snapshot_type="execution_history",
            period_start=min(dates),
            period_end=max(dates),
            summary=result.summary,
            token_count=result.compressed_tokens,
            item_count=len(old_executions),
        )
    
    async def create_conversation_snapshot(
        self,
        campaign_id: UUID,
        messages: List[Dict],
        keep_recent: int = 20,
    ) -> Optional[ContextSnapshot]:
        """
        Create a snapshot of older conversation messages.
        """
        if len(messages) <= keep_recent:
            return None
        
        old_messages = messages[:-keep_recent]
        
        result = await self.compressor.compress_conversation_history(
            old_messages,
            max_tokens=1000,
            recent_count=0,
        )
        
        return ContextSnapshot(
            snapshot_type="conversation",
            period_start=utc_now() - timedelta(days=30),  # Approximate
            period_end=utc_now(),
            summary=result.summary,
            token_count=result.compressed_tokens,
            item_count=len(old_messages),
        )
    
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse a date string, returning None on failure."""
        if not date_str:
            return None
        try:
            # Handle various formats
            if "T" in date_str:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError):
            return None


# =============================================================================
# Main Integration Service
# =============================================================================

class SmartContextService:
    """
    High-level service that combines all context management features.
    
    Usage:
        service = SmartContextService(db, model="gpt-4o")
        
        # Analyze user query
        analysis = service.analyze_query("Why is the research stream blocked?")
        
        # Build optimized context
        context = await service.build_optimized_context(
            campaign_id=uuid,
            query_analysis=analysis,
            conversation_history=messages,
        )
    """
    
    def __init__(self, db: AsyncSession, model: str = "default"):
        self.db = db
        self.budget_manager = ContextBudgetManager(model)
        self.compressor = ContextCompressor(db)
        self.snapshot_service = SnapshotService(db)
    
    def analyze_query(
        self,
        query: str,
        campaign_context: Optional[Dict] = None,
    ) -> QueryAnalysis:
        """Analyze a user query to determine context requirements."""
        analyzer = ContextAnalyzer(campaign_context)
        return analyzer.analyze(query)
    
    async def build_optimized_context(
        self,
        campaign_id: UUID,
        core_context: Dict,
        detailed_context: Optional[Dict] = None,
        conversation_history: Optional[List[Dict]] = None,
        query_analysis: Optional[QueryAnalysis] = None,
    ) -> Dict[str, Any]:
        """
        Build optimized context within token budget.
        
        Returns:
            Dict with 'context', 'metadata' including token counts
        """
        # Count core context tokens
        core_text = str(core_context)
        core_tokens = TokenCounter.count_tokens(core_text)
        
        # Count conversation tokens
        conv_tokens = 0
        compressed_conversation = conversation_history or []
        if conversation_history:
            result = await self.compressor.compress_conversation_history(
                conversation_history,
                max_tokens=self.budget_manager.budget.conversation_history,
            )
            conv_tokens = result.compressed_tokens
            if result.compressed_count > 0:
                # Replace with compressed version indicator
                compressed_conversation = conversation_history[-10:]  # Keep recent
        
        # Get available budget for tier 2/3
        budget = self.budget_manager.get_available_budget(
            tier1_tokens=core_tokens,
            conversation_tokens=conv_tokens,
        )
        
        # Process detailed context if needed
        compressed_detailed = None
        detailed_tokens = 0
        if detailed_context and (not query_analysis or query_analysis.needs_tier2):
            # Compress tasks if present
            if "tasks" in detailed_context:
                relevant_ids = None
                if query_analysis and query_analysis.mentioned_tasks:
                    relevant_ids = query_analysis.mentioned_tasks
                
                task_result = await self.compressor.compress_task_details(
                    detailed_context["tasks"],
                    max_tokens=budget["tier2"] // 2,
                    relevant_ids=relevant_ids,
                )
                detailed_tokens += task_result.compressed_tokens
                compressed_detailed = compressed_detailed or {}
                compressed_detailed["tasks_summary"] = task_result.summary
            
            # Compress executions if present
            if "executions" in detailed_context:
                keywords = query_analysis.keywords if query_analysis else None
                exec_result = await self.compressor.compress_execution_history(
                    detailed_context["executions"],
                    max_tokens=budget["tier2"] // 2,
                    keywords=keywords,
                )
                detailed_tokens += exec_result.compressed_tokens
                compressed_detailed = compressed_detailed or {}
                compressed_detailed["executions_summary"] = exec_result.summary
        
        # Build final context
        total_tokens = core_tokens + conv_tokens + detailed_tokens
        
        return {
            "core": core_context,
            "detailed": compressed_detailed,
            "conversation_history": compressed_conversation,
            "metadata": {
                "core_tokens": core_tokens,
                "detailed_tokens": detailed_tokens,
                "conversation_tokens": conv_tokens,
                "total_tokens": total_tokens,
                "budget_limit": self.budget_manager.limit,
                "compression_applied": detailed_tokens < TokenCounter.count_tokens(str(detailed_context)) if detailed_context else False,
                "query_intent": query_analysis.intent.value if query_analysis else None,
            }
        }
