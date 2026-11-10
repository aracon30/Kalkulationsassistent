"""Stückzahl je Position und kalkulierter Preis (neben dem gewählten Angebotspreis)

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("offer_items") as batch:
        batch.add_column(sa.Column("quantity", sa.Integer(), nullable=True))
    with op.batch_alter_table("calculations") as batch:
        batch.add_column(sa.Column("calculated_price", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("calculations") as batch:
        batch.drop_column("calculated_price")
    with op.batch_alter_table("offer_items") as batch:
        batch.drop_column("quantity")
