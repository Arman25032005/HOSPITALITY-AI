"""knowledge ingestion columns

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('document_chunks', sa.Column('embedding', postgresql.ARRAY(sa.Float()), nullable=True))
    op.add_column('document_chunks', sa.Column('embedding_model', sa.String(length=100), nullable=True))
    op.add_column('document_chunks', sa.Column('suspicious', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('knowledge_documents', sa.Column('doc_key', sa.String(length=200), nullable=False, server_default=''))
    op.add_column('knowledge_documents', sa.Column('status', sa.String(length=20), nullable=False, server_default='indexed'))
    op.add_column('knowledge_documents', sa.Column('content_text', sa.Text(), nullable=False, server_default=''))
    op.add_column('knowledge_documents', sa.Column('content_sha256', sa.String(length=64), nullable=False, server_default=''))
    op.create_unique_constraint(op.f('uq_knowledge_documents_hotel_id'), 'knowledge_documents', ['hotel_id', 'doc_key', 'version'])
    for table, col in [('document_chunks', 'suspicious'), ('knowledge_documents', 'doc_key'),
                       ('knowledge_documents', 'status'), ('knowledge_documents', 'content_text'),
                       ('knowledge_documents', 'content_sha256')]:
        op.alter_column(table, col, server_default=None)  # app supplies values
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_constraint(op.f('uq_knowledge_documents_hotel_id'), 'knowledge_documents', type_='unique')
    op.drop_column('knowledge_documents', 'content_sha256')
    op.drop_column('knowledge_documents', 'content_text')
    op.drop_column('knowledge_documents', 'status')
    op.drop_column('knowledge_documents', 'doc_key')
    op.drop_column('document_chunks', 'suspicious')
    op.drop_column('document_chunks', 'embedding_model')
    op.drop_column('document_chunks', 'embedding')
    # ### end Alembic commands ###
