"""rôle staff, statut remboursée, seuil de stock, adresse mémorisée

Revision ID: 2a7c4e91d0b3
Revises: 1bffc7066945
Create Date: 2026-10-30
"""
import sqlalchemy as sa
from alembic import op

revision = "2a7c4e91d0b3"
down_revision = "1bffc7066945"
branch_labels = None
depends_on = None

ROLES_NEW = "role IN ('customer', 'staff', 'admin')"
ROLES_OLD = "role IN ('customer', 'admin')"
STATUS_NEW = ("status IN ('pending_payment', 'paid', 'preparing', 'shipped', 'delivered', 'cancelled', 'refunded')")
STATUS_OLD = "status IN ('pending_payment', 'paid', 'preparing', 'shipped', 'delivered', 'cancelled')"


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("default_address", sa.String(200), nullable=True))
        b.add_column(sa.Column("default_city", sa.String(80), nullable=True))
        b.add_column(sa.Column("default_country", sa.String(2), nullable=True))
        b.drop_constraint("ck_users_role", type_="check")
        b.create_check_constraint("ck_users_role", ROLES_NEW)
    with op.batch_alter_table("products") as b:
        b.add_column(sa.Column("low_stock_threshold", sa.Integer(), nullable=False, server_default="5"))
    with op.batch_alter_table("orders") as b:
        b.drop_constraint("ck_orders_status", type_="check")
        b.create_check_constraint("ck_orders_status", STATUS_NEW)


def downgrade() -> None:
    op.execute("UPDATE orders SET status = 'cancelled' WHERE status = 'refunded'")
    op.execute("UPDATE users SET role = 'customer' WHERE role = 'staff'")
    with op.batch_alter_table("orders") as b:
        b.drop_constraint("ck_orders_status", type_="check")
        b.create_check_constraint("ck_orders_status", STATUS_OLD)
    with op.batch_alter_table("products") as b:
        b.drop_column("low_stock_threshold")
    with op.batch_alter_table("users") as b:
        b.drop_constraint("ck_users_role", type_="check")
        b.create_check_constraint("ck_users_role", ROLES_OLD)
        b.drop_column("default_country")
        b.drop_column("default_city")
        b.drop_column("default_address")
