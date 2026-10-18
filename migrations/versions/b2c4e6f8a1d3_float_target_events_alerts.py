"""Float target, setting-change events, alert state

Revision ID: b2c4e6f8a1d3
Revises: a1f3c2d9b7e4
Create Date: 2026-10-18 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c4e6f8a1d3'
down_revision = 'a1f3c2d9b7e4'
branch_labels = None
depends_on = None


def upgrade():
    # SQLite can't ALTER a column type in place, so batch mode rebuilds the table.
    with op.batch_alter_table('temp') as batch_op:
        batch_op.alter_column('target_temp', existing_type=sa.Integer(),
                              type_=sa.Float(), existing_nullable=True)

    op.create_table('event',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('detail', sa.String(length=120), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_event_timestamp'), 'event', ['timestamp'], unique=False)

    op.create_table('alert_state',
    sa.Column('key', sa.String(length=32), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('since', sa.DateTime(), nullable=True),
    sa.Column('last_notified', sa.DateTime(), nullable=True),
    sa.Column('message', sa.String(length=200), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )


def downgrade():
    op.drop_table('alert_state')
    op.drop_index(op.f('ix_event_timestamp'), table_name='event')
    op.drop_table('event')
    with op.batch_alter_table('temp') as batch_op:
        batch_op.alter_column('target_temp', existing_type=sa.Float(),
                              type_=sa.Integer(), existing_nullable=True)
