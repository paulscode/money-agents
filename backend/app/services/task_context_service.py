"""
Task context service for Brainstorm integration.

This service provides task context that can be injected into AI conversations,
and handles parsing of task actions from AI responses.
"""

import logging
import re
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional, List
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskType, TaskStatus

logger = logging.getLogger(__name__)

# Pattern to detect task creation requests from LLM
TASK_CREATE_PATTERN = re.compile(
    r'\[TASK:\s*(.+?)\]',
    re.IGNORECASE | re.DOTALL
)

# Pattern to detect task completion acknowledgments
TASK_COMPLETE_PATTERN = re.compile(
    r'\[TASK_COMPLETE:\s*([a-f0-9-]+)\s*(?:,\s*(.+?))?\]',
    re.IGNORECASE
)

# Pattern to detect task defer requests
TASK_DEFER_PATTERN = re.compile(
    r'\[TASK_DEFER:\s*([a-f0-9-]+)\s*,\s*(\d+)\s*(day|hour|week)s?\]',
    re.IGNORECASE
)


class TaskContextService:
    """Service for providing task context to AI and parsing task actions."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_task_context_for_prompt(
        self,
        user_id: UUID,
        max_tasks: int = 5,
    ) -> str:
        """
        Generate task context to inject into Brainstorm prompts.
        
        Returns a formatted string with the user's high-priority tasks.
        """
        # Get active, high-priority tasks
        query = (
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.status.in_([
                    TaskStatus.READY.value,
                    TaskStatus.IN_PROGRESS.value,
                    TaskStatus.BLOCKED.value,
                ]),
            )
            .order_by(Task.priority_score.desc())
            .limit(max_tasks)
        )
        
        result = await self.db.execute(query)
        tasks = list(result.scalars().all())
        
        if not tasks:
            return ""
        
        # Get counts for context
        count_query = select(func.count(Task.id)).where(
            Task.user_id == user_id,
            Task.status.in_([
                TaskStatus.READY.value,
                TaskStatus.IN_PROGRESS.value,
                TaskStatus.BLOCKED.value,
            ]),
        )
        result = await self.db.execute(count_query)
        total_active = result.scalar() or 0
        
        # Get overdue count
        overdue_query = select(func.count(Task.id)).where(
            Task.user_id == user_id,
            Task.due_date < utc_now(),
            Task.status.in_([TaskStatus.READY.value, TaskStatus.IN_PROGRESS.value]),
        )
        result = await self.db.execute(overdue_query)
        overdue_count = result.scalar() or 0
        
        # Build context string
        context_lines = [
            "\n## Current Task Context",
            f"You have {total_active} active task{'s' if total_active != 1 else ''}",
        ]
        
        if overdue_count > 0:
            context_lines.append(f"⚠️ {overdue_count} task{'s are' if overdue_count != 1 else ' is'} overdue!")
        
        context_lines.append("\n**Top Priority Tasks:**")
        
        for i, task in enumerate(tasks, 1):
            status_emoji = {
                TaskStatus.READY.value: "🟢",
                TaskStatus.IN_PROGRESS.value: "🔵",
                TaskStatus.BLOCKED.value: "🔴",
            }.get(task.status, "⚪")
            
            due_str = ""
            if task.due_date:
                if task.due_date < utc_now():
                    due_str = " (OVERDUE)"
                else:
                    days_until = (task.due_date - utc_now()).days
                    if days_until == 0:
                        due_str = " (due today)"
                    elif days_until == 1:
                        due_str = " (due tomorrow)"
                    elif days_until <= 7:
                        due_str = f" (due in {days_until} days)"
            
            value_str = ""
            if task.estimated_value:
                value_str = f" [${task.estimated_value:,.0f}]"
            
            context_lines.append(
                f"{i}. {status_emoji} **{task.title}**{due_str}{value_str}"
            )
            if task.description and len(task.description) < 100:
                context_lines.append(f"   {task.description}")
        
        return "\n".join(context_lines)
    
    async def get_task_summary(self, user_id: UUID) -> dict:
        """Get a summary of the user's task state for API responses."""
        # Get counts by status
        status_query = (
            select(Task.status, func.count(Task.id))
            .where(Task.user_id == user_id)
            .group_by(Task.status)
        )
        result = await self.db.execute(status_query)
        status_counts = {row[0]: row[1] for row in result.fetchall()}
        
        # Get overdue count
        overdue_query = select(func.count(Task.id)).where(
            Task.user_id == user_id,
            Task.due_date < utc_now(),
            Task.status.in_([TaskStatus.READY.value, TaskStatus.IN_PROGRESS.value]),
        )
        result = await self.db.execute(overdue_query)
        overdue_count = result.scalar() or 0
        
        # Get total estimated value
        value_query = select(func.sum(Task.estimated_value)).where(
            Task.user_id == user_id,
            Task.status.in_([TaskStatus.READY.value, TaskStatus.IN_PROGRESS.value]),
            Task.estimated_value.isnot(None),
        )
        result = await self.db.execute(value_query)
        total_value = result.scalar() or 0
        
        return {
            "active": status_counts.get(TaskStatus.READY.value, 0) + 
                      status_counts.get(TaskStatus.IN_PROGRESS.value, 0),
            "blocked": status_counts.get(TaskStatus.BLOCKED.value, 0),
            "overdue": overdue_count,
            "completed_today": 0,  # TODO: Implement
            "total_value": total_value,
            "by_status": status_counts,
        }
    
    def extract_task_creation(self, text: str) -> List[dict]:
        """Extract task creation requests from AI response."""
        matches = TASK_CREATE_PATTERN.findall(text)
        tasks = []
        for match in matches:
            # Parse the task content - format: title | description | due:X | value:Y
            parts = [p.strip() for p in match.split('|')]
            task_data = {"title": parts[0]}
            
            for part in parts[1:]:
                if part.startswith('due:'):
                    task_data['due'] = part[4:].strip()
                elif part.startswith('value:'):
                    try:
                        task_data['value'] = float(part[6:].strip().replace('$', '').replace(',', ''))
                    except ValueError:
                        pass
                elif not task_data.get('description'):
                    task_data['description'] = part
            
            tasks.append(task_data)
        return tasks
    
    def extract_task_completions(self, text: str) -> List[dict]:
        """Extract task completion acknowledgments from AI response."""
        matches = TASK_COMPLETE_PATTERN.findall(text)
        return [
            {"task_id": m[0], "notes": m[1] if len(m) > 1 and m[1] else None}
            for m in matches
        ]
    
    def extract_task_deferrals(self, text: str) -> List[dict]:
        """Extract task deferral requests from AI response."""
        matches = TASK_DEFER_PATTERN.findall(text)
        deferrals = []
        for m in matches:
            task_id, amount, unit = m
            try:
                amount = int(amount)
                if unit.lower().startswith('hour'):
                    delta = timedelta(hours=amount)
                elif unit.lower().startswith('week'):
                    delta = timedelta(weeks=amount)
                else:
                    delta = timedelta(days=amount)
                
                defer_until = utc_now() + delta
                deferrals.append({
                    "task_id": task_id,
                    "defer_until": defer_until.isoformat(),
                })
            except (ValueError, TypeError):
                pass
        return deferrals
    
    def clean_task_tags(self, text: str) -> str:
        """Remove task action tags from text for clean display."""
        text = TASK_CREATE_PATTERN.sub('', text)
        text = TASK_COMPLETE_PATTERN.sub('', text)
        text = TASK_DEFER_PATTERN.sub('', text)
        return text.strip()


# Task management prompt section to add to Brainstorm
TASK_MANAGEMENT_PROMPT = """
## Task Management
You have access to the user's task list. You can help them manage tasks with these actions:

**Creating Tasks:**
When the user wants to create a task, use this format:
[TASK: title | optional description | due:Xd or due:Xh | value:$Y]

Examples:
- [TASK: Review proposal draft | Check for typos and clarity | due:2d | value:$500]
- [TASK: Call vendor about pricing | due:1d]
- [TASK: Research competitor features]

**Completing Tasks:**
When discussing a task that's done, acknowledge completion:
[TASK_COMPLETE: task-uuid-here, optional completion notes]

**Deferring Tasks:**
To defer a task to later:
[TASK_DEFER: task-uuid-here, 3 days]
[TASK_DEFER: task-uuid-here, 1 week]

When you perform task actions:
1. Use the appropriate tag format
2. Briefly acknowledge the action: "I've created that task" / "I've marked that as complete"
3. Continue the conversation naturally

If the user asks about their tasks, you can reference the task context provided below.
"""


def get_brainstorm_task_prompt(task_context: str) -> str:
    """
    Get the complete task management prompt section for Brainstorm.
    
    Args:
        task_context: The dynamic task context from get_task_context_for_prompt()
    
    Returns:
        Complete prompt section to inject into Brainstorm system prompt
    """
    if not task_context:
        return TASK_MANAGEMENT_PROMPT + "\n\nYou currently have no active tasks."
    
    return TASK_MANAGEMENT_PROMPT + "\n" + task_context
