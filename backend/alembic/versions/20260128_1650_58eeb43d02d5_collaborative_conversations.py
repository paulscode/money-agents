"""collaborative_conversations

Revision ID: 58eeb43d02d5
Revises: 66124bcab6bf
Create Date: 2026-01-28 16:50:56.834806

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '58eeb43d02d5'
down_revision = '66124bcab6bf'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create message_reads table
    op.create_table('message_reads',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('message_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_message_user_read', 'message_reads', ['message_id', 'user_id'], unique=True)
    op.create_index(op.f('ix_message_reads_message_id'), 'message_reads', ['message_id'], unique=False)
    op.create_index(op.f('ix_message_reads_user_id'), 'message_reads', ['user_id'], unique=False)
    
    # Rename conversations.user_id to created_by_user_id
    op.alter_column('conversations', 'user_id', new_column_name='created_by_user_id')
    
    # Add mentioned_user_ids to messages
    op.add_column('messages', sa.Column('mentioned_user_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    
    # Add sender_id foreign key constraint to messages (previously didn't have it)
    op.create_foreign_key('fk_messages_sender_id_users', 'messages', 'users', ['sender_id'], ['id'])
    
    # Drop read_at from messages (now tracked in message_reads)
    op.drop_column('messages', 'read_at')
    
    # Add composite index for finding conversations by type and related_id
    op.create_index('idx_conversation_type_related_id', 'conversations', ['conversation_type', 'related_id'], unique=False)


def downgrade() -> None:
    # Drop index
    op.drop_index('idx_conversation_type_related_id', table_name='conversations')
    
    # Add read_at back to messages
    op.add_column('messages', sa.Column('read_at', sa.DateTime(timezone=True), nullable=True))
    
    # Drop sender_id foreign key
    op.drop_constraint('fk_messages_sender_id_users', 'messages', type_='foreignkey')
    
    # Drop mentioned_user_ids
    op.drop_column('messages', 'mentioned_user_ids')
    
    # Rename created_by_user_id back to user_id
    op.alter_column('conversations', 'created_by_user_id', new_column_name='user_id')
    
    # Drop message_reads table
    op.drop_index(op.f('ix_message_reads_user_id'), table_name='message_reads')
    op.drop_index(op.f('ix_message_reads_message_id'), table_name='message_reads')
    op.drop_index('idx_message_user_read', table_name='message_reads')
    op.drop_table('message_reads')
