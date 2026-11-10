"""Klassifizierungsversion und Inhalts-Hash-Kennzeichen für Dokumente

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("classifier_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("content_hashed", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("content_hashed")
        batch.drop_column("classifier_version")
