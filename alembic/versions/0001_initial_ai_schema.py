# Alembic revision for the dedicated AI Postgres: 0001 initial ai schema.
# Applies only to the AI database, never to CGS clinic tables.
"""Initial AI service schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-08-23 14:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. entities table
    op.create_table(
        'entities',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=True),
        sa.Column('system_prompt', sa.Text(), nullable=True),
        sa.Column('configuration', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_entities_type', 'entities', ['type'], unique=False)
    op.create_index('ix_entities_external_id', 'entities', ['external_id'], unique=False)
    op.create_index('idx_entity_type_ext', 'entities', ['type', 'external_id'], unique=False)

    # 2. conversations table
    op.create_table(
        'conversations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('entity_id', sa.String(length=36), nullable=False),
        sa.Column('participant_id', sa.String(length=255), nullable=False),
        sa.Column('channel', sa.String(length=50), nullable=True),
        sa.Column('state', sa.String(length=50), nullable=True),
        sa.Column('last_activity', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['entity_id'], ['entities.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_conversations_entity_id', 'conversations', ['entity_id'], unique=False)
    op.create_index('ix_conversations_participant_id', 'conversations', ['participant_id'], unique=False)
    op.create_index('idx_conv_entity_part', 'conversations', ['entity_id', 'participant_id'], unique=False)
    op.create_index('idx_conv_last_activity', 'conversations', ['last_activity'], unique=False)

    # 3. messages table
    op.create_table(
        'messages',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('sender_type', sa.String(length=50), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('tokens', sa.Integer(), nullable=True),
        sa.Column('msg_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'], unique=False)
    op.create_index('ix_messages_created_at', 'messages', ['created_at'], unique=False)

    # 4. conversation_summaries table
    op.create_table(
        'conversation_summaries',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('version', sa.Integer(), nullable=True),
        sa.Column('summary_text', sa.Text(), nullable=False),
        sa.Column('summary_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id')
    )

    # 5. summary_addons table
    op.create_table(
        'summary_addons',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_summary_addons_conversation_id', 'summary_addons', ['conversation_id'], unique=False)
    op.create_index('ix_summary_addons_status', 'summary_addons', ['status'], unique=False)

    # 6. ai_usage_logs table
    op.create_table(
        'ai_usage_logs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('entity_id', sa.String(length=36), nullable=True),
        sa.Column('conversation_id', sa.String(length=36), nullable=True),
        sa.Column('request_id', sa.String(length=255), nullable=False),
        sa.Column('model', sa.String(length=255), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('total_tokens', sa.Integer(), nullable=True),
        sa.Column('estimated_cost', sa.Numeric(precision=14, scale=8), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('usage_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['entity_id'], ['entities.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ai_usage_logs_entity_id', 'ai_usage_logs', ['entity_id'], unique=False)
    op.create_index('ix_ai_usage_logs_request_id', 'ai_usage_logs', ['request_id'], unique=False)
    op.create_index('ix_ai_usage_logs_created_at', 'ai_usage_logs', ['created_at'], unique=False)
    op.create_index('idx_usage_entity_created', 'ai_usage_logs', ['entity_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_table('ai_usage_logs')
    op.drop_table('summary_addons')
    op.drop_table('conversation_summaries')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('entities')
