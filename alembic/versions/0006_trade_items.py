"""Handelsware aus Kalkulationen; Anfragen der Art "Ersatzteile / Handelsware"

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("offer_id", sa.Integer(), sa.ForeignKey("offers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE")),
        sa.Column("source_sheet", sa.String(128)), sa.Column("source_row", sa.Integer()), sa.Column("line_no", sa.String(16)),
        sa.Column("description", sa.String(500)), sa.Column("category", sa.String(64)), sa.Column("outer_diameter", sa.Float()),
        sa.Column("quantity", sa.Float()), sa.Column("purchase_price", sa.Float()), sa.Column("purchase_total", sa.Float()),
        sa.Column("material_overhead", sa.Float()), sa.Column("sales_overhead", sa.Float()), sa.Column("risk_profit", sa.Float()),
        sa.Column("sales_total", sa.Float()), sa.Column("sales_price", sa.Float()), sa.Column("markup_factor", sa.Float()),
        sa.Column("source_note", sa.String(500)), sa.Column("is_estimate", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    for col in ("offer_id", "document_id", "description", "category"):
        op.create_index(f"ix_trade_items_{col}", "trade_items", [col])
    with op.batch_alter_table("inquiries") as batch:
        batch.add_column(sa.Column("kind", sa.String(16), nullable=False, server_default="apparat"))
        batch.add_column(sa.Column("reference_no", sa.String(128)))
        batch.add_column(sa.Column("due_date", sa.Date()))
        batch.add_column(sa.Column("trade_json", sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table("inquiries") as batch:
        for col in ("trade_json", "due_date", "reference_no", "kind"):
            batch.drop_column(col)
    op.drop_table("trade_items")
