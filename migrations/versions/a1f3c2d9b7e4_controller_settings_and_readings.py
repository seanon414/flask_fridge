"""Controller settings and reading log

Revision ID: a1f3c2d9b7e4
Revises: 90d15ff94300
Create Date: 2026-10-17 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1f3c2d9b7e4'
down_revision = '90d15ff94300'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('temp', sa.Column('hysteresis', sa.Float(), nullable=False, server_default='1.0'))
    op.add_column('temp', sa.Column('min_cycle_seconds', sa.Integer(), nullable=False, server_default='300'))
    op.add_column('temp', sa.Column('mode', sa.String(length=8), nullable=False, server_default='auto'))
    op.create_table('reading',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=False),
    sa.Column('temp_f', sa.Float(), nullable=True),
    sa.Column('target_f', sa.Float(), nullable=True),
    sa.Column('fridge_on', sa.Boolean(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_reading_timestamp'), 'reading', ['timestamp'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_reading_timestamp'), table_name='reading')
    op.drop_table('reading')
    with op.batch_alter_table('temp') as batch_op:
        batch_op.drop_column('mode')
        batch_op.drop_column('min_cycle_seconds')
        batch_op.drop_column('hysteresis')
