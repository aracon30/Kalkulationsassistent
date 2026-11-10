"""Materialauszug: Bauteilzeilen je Kalkulation

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("calculation_id", sa.Integer(), sa.ForeignKey("calculations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("offer_item_id", sa.Integer(), sa.ForeignKey("offer_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_sheet", sa.String(128)), sa.Column("source_row", sa.Integer()),
        sa.Column("line_no", sa.String(16)), sa.Column("description", sa.String(255)), sa.Column("form", sa.String(128)),
        sa.Column("dimensions", sa.String(128)), sa.Column("standard", sa.String(128)), sa.Column("material_raw", sa.String(128)),
        sa.Column("material", sa.String(64)), sa.Column("category", sa.String(64)),
        sa.Column("quantity", sa.Float()), sa.Column("length_m", sa.Float()), sa.Column("unit_price", sa.Float()),
        sa.Column("price_unit", sa.String(16)), sa.Column("apz_cost", sa.Float()), sa.Column("cost", sa.Float()),
        sa.Column("manufacturing_cost", sa.Float()), sa.Column("total_cost", sa.Float()),
        sa.Column("dn", sa.Float()), sa.Column("pn", sa.Float()), sa.Column("outer_diameter", sa.Float()),
        sa.Column("wall_thickness", sa.Float()), sa.Column("plate_length", sa.Float()), sa.Column("plate_width", sa.Float()),
        sa.Column("thread", sa.String(32)), sa.Column("est_weight", sa.Float()), sa.Column("est_weight_unit", sa.String(16)),
        sa.Column("price_per_kg", sa.Float()),
    )
    for col in ("calculation_id", "offer_item_id", "document_id", "description", "material", "category"):
        op.create_index(f"ix_material_lines_{col}", "material_lines", [col])
    op.create_index("ix_material_lines_lookup", "material_lines", ["category", "material", "outer_diameter", "dn"])


def downgrade() -> None:
    op.drop_table("material_lines")
