"""add customer and orders

Revision ID: 7c1e4b2a9f30
Revises: d0f67f256061
Create Date: 2026-10-14 12:00:00.000000

Idempotent: production already runs db.create_all() at app start, so these
tables may exist before this migration is applied. Each table and index is
created only when missing.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7c1e4b2a9f30'
down_revision = 'd0f67f256061'
branch_labels = None
depends_on = None


def _create_index_if_missing(table, name, columns, unique=False):
    # Fresh inspector each time: the table may have just been created.
    existing = {ix['name'] for ix in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns, unique=unique)


def upgrade():
    insp = sa.inspect(op.get_bind())

    if not insp.has_table('customer'):
        op.create_table('customer',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('first_name', sa.String(length=128), nullable=True),
        sa.Column('last_name', sa.String(length=128), nullable=True),
        sa.Column('username', sa.String(length=64), nullable=True),
        sa.Column('photo_url', sa.String(length=512), nullable=True),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('maps_link', sa.String(length=512), nullable=True),
        sa.Column('can_message', sa.Boolean(), nullable=False),
        sa.Column('lang', sa.String(length=2), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    _create_index_if_missing('customer', 'ix_customer_telegram_id', ['telegram_id'], unique=True)

    if not insp.has_table('orders'):
        op.create_table('orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=True),
        sa.Column('delivery_type', sa.String(length=16), nullable=True),
        sa.Column('delivery_fee_charged', sa.Float(), nullable=True),
        sa.Column('delivery_fee_driver', sa.Float(), nullable=True),
        sa.Column('voucher_code', sa.String(length=50), nullable=True),
        sa.Column('subtotal', sa.Float(), nullable=True),
        sa.Column('total_cost', sa.Float(), nullable=True),
        sa.Column('total_to_collect', sa.Float(), nullable=True),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('maps_link', sa.String(length=512), nullable=True),
        sa.Column('admin_note', sa.Text(), nullable=True),
        sa.Column('cash_received', sa.Boolean(), nullable=True),
        sa.Column('cash_received_at', sa.DateTime(), nullable=True),
        sa.Column('notify_error', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('status_changed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    _create_index_if_missing('orders', 'ix_orders_customer_id', ['customer_id'])
    _create_index_if_missing('orders', 'ix_orders_status', ['status'])
    _create_index_if_missing('orders', 'ix_orders_created_at', ['created_at'])

    if not insp.has_table('order_item'):
        op.create_table('order_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=True),
        sa.Column('variant_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=100), nullable=True),
        sa.Column('options_text', sa.String(length=255), nullable=True),
        sa.Column('unit_price', sa.Float(), nullable=True),
        sa.Column('unit_cost', sa.Float(), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
        sa.ForeignKeyConstraint(['product_id'], ['product.id'], ),
        sa.ForeignKeyConstraint(['variant_id'], ['product_variant.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    _create_index_if_missing('order_item', 'ix_order_item_order_id', ['order_id'])

    if not insp.has_table('order_status_log'):
        op.create_table('order_status_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('from_status', sa.String(length=16), nullable=True),
        sa.Column('to_status', sa.String(length=16), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    _create_index_if_missing('order_status_log', 'ix_order_status_log_order_id', ['order_id'])


def downgrade():
    op.drop_table('order_status_log')
    op.drop_table('order_item')
    op.drop_table('orders')
    op.drop_table('customer')
