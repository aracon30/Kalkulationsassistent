"""Anfragen (Vorkalkulation neuer Angebote); Simulationen entfernt

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inquiries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("customer", sa.String(255)), sa.Column("product_type", sa.String(64)), sa.Column("dn", sa.Float()),
        sa.Column("design_pressure", sa.Float()), sa.Column("material", sa.String(64)), sa.Column("filter_elements", sa.Integer()),
        sa.Column("filter_area", sa.Float()), sa.Column("weight", sa.Float()), sa.Column("quantity", sa.Integer()),
        sa.Column("reference_item_id", sa.Integer(), sa.ForeignKey("offer_items.id", ondelete="SET NULL")),
        sa.Column("calc_inputs_json", sa.Text()), sa.Column("material_json", sa.Text()), sa.Column("result_price", sa.Float()),
        sa.Column("notes", sa.Text()), sa.Column("status", sa.String(32), nullable=False, server_default="in Arbeit"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.drop_table("simulations")


def downgrade() -> None:
    op.drop_table("inquiries")
    op.create_table(
        "simulations",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("name", sa.String(255), nullable=False),
        sa.Column("offer_item_id", sa.Integer(), sa.ForeignKey("offer_items.id", ondelete="SET NULL")),
        sa.Column("base_calculation_id", sa.Integer(), sa.ForeignKey("calculations.id", ondelete="SET NULL")),
        sa.Column("scheme_id", sa.String(64)), sa.Column("inputs_json", sa.Text(), nullable=False),
        sa.Column("results_json", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
