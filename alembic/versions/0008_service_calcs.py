"""Serviceeinsätze: Kalkulationen und Zeilen

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_calcs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("offer_id", sa.Integer(), sa.ForeignKey("offers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE")),
        sa.Column("title", sa.String(255)), sa.Column("total_cost", sa.Float()), sa.Column("offer_price", sa.Float()),
        sa.Column("abroad", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_service_calcs_offer_id", "service_calcs", ["offer_id"])
    op.create_index("ix_service_calcs_document_id", "service_calcs", ["document_id"])
    op.create_table(
        "service_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_calc_id", sa.Integer(), sa.ForeignKey("service_calcs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_sheet", sa.String(128)), sa.Column("source_row", sa.Integer()), sa.Column("line_no", sa.String(16)),
        sa.Column("description", sa.String(255)), sa.Column("kind", sa.String(32)),
        sa.Column("rate_domestic", sa.Float()), sa.Column("rate_abroad", sa.Float()), sa.Column("quantity", sa.Float()), sa.Column("total", sa.Float()),
    )
    for col in ("service_calc_id", "description", "kind"):
        op.create_index(f"ix_service_lines_{col}", "service_lines", [col])


def downgrade() -> None:
    op.drop_table("service_lines")
    op.drop_table("service_calcs")
