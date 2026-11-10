"""Handelsware: Abmessung, Material, Lieferzeit, Zeichnung, früherer Auftrag, Baugruppe

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("trade_items") as batch:
        batch.add_column(sa.Column("dimensions", sa.String(128)))
        batch.add_column(sa.Column("material", sa.String(128)))
        batch.add_column(sa.Column("lead_time", sa.String(64)))
        batch.add_column(sa.Column("drawing_ref", sa.String(128)))
        batch.add_column(sa.Column("last_order", sa.String(64)))
        batch.add_column(sa.Column("is_assembly", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("trade_items") as batch:
        for col in ("is_assembly", "last_order", "drawing_ref", "lead_time", "material", "dimensions"):
            batch.drop_column(col)
