"""chunk full-text search tokens

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_chunks",
                  sa.Column("search_tokens", sa.Text(), nullable=False, server_default=""))
    op.alter_column("document_chunks", "search_tokens", server_default=None)
    op.execute("CREATE INDEX ix_document_chunks_fts ON document_chunks "
               "USING gin (to_tsvector('simple', search_tokens))")
    # Existing rows have empty tokens until `python -m scripts.reindex_knowledge` is run.


def downgrade() -> None:
    op.execute("DROP INDEX ix_document_chunks_fts")
    op.drop_column("document_chunks", "search_tokens")
