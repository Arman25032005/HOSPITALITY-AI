"""token versions llm latency and indexes

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agent_executions', sa.Column('llm_ms', sa.Integer(), nullable=False, server_default='0'))
    op.create_index('ix_agent_exec_hotel_created', 'agent_executions', ['hotel_id', 'created_at'], unique=False)
    op.create_index('ix_audit_entity', 'audit_events', ['entity_type', 'entity_id'], unique=False)
    op.create_index('ix_audit_hotel_created', 'audit_events', ['hotel_id', 'created_at'], unique=False)
    op.create_index('ix_messages_conv_created', 'messages', ['conversation_id', 'created_at'], unique=False)
    op.create_index('ix_res_hotel_checkin', 'reservations', ['hotel_id', 'check_in'], unique=False)
    op.create_index('ix_sr_hotel_status', 'service_requests', ['hotel_id', 'status'], unique=False)
    op.add_column('users', sa.Column('token_version', sa.Integer(), nullable=False, server_default='0'))
    op.alter_column('agent_executions', 'llm_ms', server_default=None)
    op.alter_column('users', 'token_version', server_default=None)
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_column('users', 'token_version')
    op.drop_index('ix_sr_hotel_status', table_name='service_requests')
    op.drop_index('ix_res_hotel_checkin', table_name='reservations')
    op.drop_index('ix_messages_conv_created', table_name='messages')
    op.drop_index('ix_audit_hotel_created', table_name='audit_events')
    op.drop_index('ix_audit_entity', table_name='audit_events')
    op.drop_index('ix_agent_exec_hotel_created', table_name='agent_executions')
    op.drop_column('agent_executions', 'llm_ms')
    # ### end Alembic commands ###
