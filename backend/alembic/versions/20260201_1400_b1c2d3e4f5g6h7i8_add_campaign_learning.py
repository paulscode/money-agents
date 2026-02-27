"""Add campaign learning models for Phase 5

This migration adds the Agent Intelligence (Phase 5) tables:
- campaign_patterns: Successful execution patterns that can be reused
- campaign_lessons: Lessons learned from failures/issues
- plan_revisions: History of plan revisions and outcomes
- proactive_suggestions: Agent-generated optimization suggestions

These enable:
1. Self-planning agents that can revise execution plans
2. Proactive communication with optimization suggestions  
3. Inter-campaign learning to improve over time

Revision ID: b1c2d3e4f5g6h7i8
Revises: a3b4c5d6e7f8
Create Date: 2026-02-01 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5g6h7i8'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================================================
    # Create enum types
    # ==========================================================================
    
    # PatternType enum
    pattern_type = postgresql.ENUM(
        'execution_sequence', 'input_collection', 'tool_combination',
        'error_recovery', 'optimization', 'timing',
        name='pattern_type',
        create_type=False
    )
    pattern_type.create(op.get_bind(), checkfirst=True)
    
    # PatternStatus enum
    pattern_status = postgresql.ENUM(
        'active', 'deprecated', 'experimental',
        name='pattern_status',
        create_type=False
    )
    pattern_status.create(op.get_bind(), checkfirst=True)
    
    # LessonCategory enum
    lesson_category = postgresql.ENUM(
        'failure', 'inefficiency', 'user_friction', 'budget_issue', 'timing', 'tool_issue',
        name='lesson_category',
        create_type=False
    )
    lesson_category.create(op.get_bind(), checkfirst=True)
    
    # RevisionTrigger enum
    revision_trigger = postgresql.ENUM(
        'task_failure', 'stream_blocked', 'budget_concern', 'user_feedback',
        'new_information', 'optimization', 'external_change',
        name='revision_trigger',
        create_type=False
    )
    revision_trigger.create(op.get_bind(), checkfirst=True)
    
    # SuggestionType enum
    suggestion_type = postgresql.ENUM(
        'optimization', 'warning', 'opportunity', 'cost_saving', 'time_saving', 'risk_mitigation',
        name='suggestion_type',
        create_type=False
    )
    suggestion_type.create(op.get_bind(), checkfirst=True)
    
    # SuggestionStatus enum
    suggestion_status = postgresql.ENUM(
        'pending', 'accepted', 'rejected', 'auto_applied', 'expired',
        name='suggestion_status',
        create_type=False
    )
    suggestion_status.create(op.get_bind(), checkfirst=True)
    
    # ==========================================================================
    # Create campaign_patterns table
    # ==========================================================================
    op.create_table(
        'campaign_patterns',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Pattern identification
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('pattern_type', postgresql.ENUM(
            'execution_sequence', 'input_collection', 'tool_combination',
            'error_recovery', 'optimization', 'timing',
            name='pattern_type', create_type=False
        ), nullable=False),
        
        # Status and confidence
        sa.Column('status', postgresql.ENUM(
            'active', 'deprecated', 'experimental',
            name='pattern_status', create_type=False
        ), server_default='experimental', nullable=False),
        sa.Column('confidence_score', sa.Float(), server_default='0.5', nullable=False),
        
        # Pattern content
        sa.Column('pattern_data', postgresql.JSONB(), nullable=False),
        sa.Column('applicability_conditions', postgresql.JSONB(), server_default='{}', nullable=True),
        
        # Usage statistics
        sa.Column('times_applied', sa.Integer(), server_default='0', nullable=False),
        sa.Column('times_successful', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_applied_at', sa.DateTime(timezone=True), nullable=True),
        
        # Source tracking
        sa.Column('source_campaign_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('discovered_by_agent', sa.String(100), nullable=True),
        
        # User ownership
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('is_global', sa.Boolean(), server_default='false', nullable=False),
        
        # Tags
        sa.Column('tags', postgresql.JSONB(), server_default='[]', nullable=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['source_campaign_id'], ['campaigns.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    
    # Create indexes for campaign_patterns
    op.create_index('idx_pattern_name', 'campaign_patterns', ['name'])
    op.create_index('idx_pattern_type_status', 'campaign_patterns', ['pattern_type', 'status'])
    op.create_index('idx_pattern_confidence', 'campaign_patterns', ['confidence_score'])
    op.create_index('idx_pattern_user', 'campaign_patterns', ['user_id'])
    op.create_index('idx_pattern_source_campaign', 'campaign_patterns', ['source_campaign_id'])
    
    # ==========================================================================
    # Create campaign_lessons table
    # ==========================================================================
    op.create_table(
        'campaign_lessons',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Lesson identification
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('category', postgresql.ENUM(
            'failure', 'inefficiency', 'user_friction', 'budget_issue', 'timing', 'tool_issue',
            name='lesson_category', create_type=False
        ), nullable=False),
        
        # What happened
        sa.Column('context', postgresql.JSONB(), nullable=False),
        sa.Column('trigger_event', sa.Text(), nullable=False),
        
        # Impact assessment
        sa.Column('impact_severity', sa.String(20), server_default='medium', nullable=False),
        sa.Column('budget_impact', sa.Float(), nullable=True),
        sa.Column('time_impact_minutes', sa.Integer(), nullable=True),
        
        # Prevention strategy
        sa.Column('prevention_steps', postgresql.JSONB(), nullable=False),
        sa.Column('detection_signals', postgresql.JSONB(), server_default='[]', nullable=True),
        
        # Source tracking
        sa.Column('source_campaign_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source_task_id', postgresql.UUID(as_uuid=True), nullable=True),
        
        # User ownership
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Usage tracking
        sa.Column('times_applied', sa.Integer(), server_default='0', nullable=False),
        
        # Tags
        sa.Column('tags', postgresql.JSONB(), server_default='[]', nullable=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['source_campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_task_id'], ['campaign_tasks.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    
    # Create indexes for campaign_lessons
    op.create_index('idx_lesson_title', 'campaign_lessons', ['title'])
    op.create_index('idx_lesson_category_severity', 'campaign_lessons', ['category', 'impact_severity'])
    op.create_index('idx_lesson_campaign', 'campaign_lessons', ['source_campaign_id'])
    op.create_index('idx_lesson_user', 'campaign_lessons', ['user_id'])
    
    # ==========================================================================
    # Create plan_revisions table
    # ==========================================================================
    op.create_table(
        'plan_revisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Campaign reference
        sa.Column('campaign_id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Revision metadata
        sa.Column('revision_number', sa.Integer(), nullable=False),
        sa.Column('trigger', postgresql.ENUM(
            'task_failure', 'stream_blocked', 'budget_concern', 'user_feedback',
            'new_information', 'optimization', 'external_change',
            name='revision_trigger', create_type=False
        ), nullable=False),
        sa.Column('trigger_details', sa.Text(), nullable=False),
        
        # Plan snapshots
        sa.Column('plan_before', postgresql.JSONB(), nullable=False),
        sa.Column('plan_after', postgresql.JSONB(), nullable=False),
        
        # What changed
        sa.Column('changes_summary', sa.Text(), nullable=False),
        sa.Column('tasks_added', sa.Integer(), server_default='0', nullable=False),
        sa.Column('tasks_removed', sa.Integer(), server_default='0', nullable=False),
        sa.Column('tasks_modified', sa.Integer(), server_default='0', nullable=False),
        sa.Column('streams_added', sa.Integer(), server_default='0', nullable=False),
        sa.Column('streams_removed', sa.Integer(), server_default='0', nullable=False),
        
        # Reasoning
        sa.Column('reasoning', sa.Text(), nullable=False),
        sa.Column('expected_improvement', sa.Text(), nullable=True),
        
        # Outcome tracking
        sa.Column('outcome_assessed', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('outcome_success', sa.Boolean(), nullable=True),
        sa.Column('outcome_notes', sa.Text(), nullable=True),
        
        # Who initiated
        sa.Column('initiated_by', sa.String(50), nullable=False),
        sa.Column('approved_by_user', sa.Boolean(), server_default='false', nullable=False),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('outcome_assessed_at', sa.DateTime(timezone=True), nullable=True),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    )
    
    # Create indexes for plan_revisions
    op.create_index('idx_revision_campaign_number', 'plan_revisions', ['campaign_id', 'revision_number'])
    op.create_index('idx_revision_trigger', 'plan_revisions', ['trigger'])
    
    # ==========================================================================
    # Create proactive_suggestions table
    # ==========================================================================
    op.create_table(
        'proactive_suggestions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Campaign reference
        sa.Column('campaign_id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Suggestion details
        sa.Column('suggestion_type', postgresql.ENUM(
            'optimization', 'warning', 'opportunity', 'cost_saving', 'time_saving', 'risk_mitigation',
            name='suggestion_type', create_type=False
        ), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        
        # Status
        sa.Column('status', postgresql.ENUM(
            'pending', 'accepted', 'rejected', 'auto_applied', 'expired',
            name='suggestion_status', create_type=False
        ), server_default='pending', nullable=False),
        
        # Urgency and confidence
        sa.Column('urgency', sa.String(20), server_default='medium', nullable=False),
        sa.Column('confidence', sa.Float(), server_default='0.7', nullable=False),
        
        # Supporting evidence
        sa.Column('evidence', postgresql.JSONB(), nullable=False),
        sa.Column('based_on_patterns', postgresql.JSONB(), server_default='[]', nullable=True),
        sa.Column('based_on_lessons', postgresql.JSONB(), server_default='[]', nullable=True),
        
        # Recommended action
        sa.Column('recommended_action', postgresql.JSONB(), nullable=False),
        sa.Column('estimated_benefit', sa.Text(), nullable=True),
        sa.Column('estimated_cost', sa.Float(), nullable=True),
        
        # Auto-apply settings
        sa.Column('can_auto_apply', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('auto_apply_conditions', postgresql.JSONB(), nullable=True),
        
        # User response
        sa.Column('user_response_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('user_feedback', sa.Text(), nullable=True),
        
        # Outcome tracking
        sa.Column('outcome_tracked', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('actual_benefit', sa.Text(), nullable=True),
        
        # Expiration
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    )
    
    # Create indexes for proactive_suggestions
    op.create_index('idx_suggestion_campaign_status', 'proactive_suggestions', ['campaign_id', 'status'])
    op.create_index('idx_suggestion_type_urgency', 'proactive_suggestions', ['suggestion_type', 'urgency'])


def downgrade() -> None:
    # Drop tables
    op.drop_table('proactive_suggestions')
    op.drop_table('plan_revisions')
    op.drop_table('campaign_lessons')
    op.drop_table('campaign_patterns')
    
    # Drop enum types
    op.execute('DROP TYPE IF EXISTS suggestion_status')
    op.execute('DROP TYPE IF EXISTS suggestion_type')
    op.execute('DROP TYPE IF EXISTS revision_trigger')
    op.execute('DROP TYPE IF EXISTS lesson_category')
    op.execute('DROP TYPE IF EXISTS pattern_status')
    op.execute('DROP TYPE IF EXISTS pattern_type')
