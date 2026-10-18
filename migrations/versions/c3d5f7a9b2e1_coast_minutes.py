"""Coast-aware control: temp.coast_minutes

Revision ID: c3d5f7a9b2e1
Revises: b2c4e6f8a1d3
Create Date: 2026-10-18 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d5f7a9b2e1'
down_revision = 'b2c4e6f8a1d3'
branch_labels = None
depends_on = None


def upgrade():
    # 0 keeps today's behaviour (a plain thermostat) until it is switched on.
    with op.batch_alter_table('temp') as batch_op:
        batch_op.add_column(sa.Column('coast_minutes', sa.Float(), nullable=False,
                                      server_default='0'))


def downgrade():
    with op.batch_alter_table('temp') as batch_op:
        batch_op.drop_column('coast_minutes')
