"""Add current temp to temp table

Revision ID: 90d15ff94300
Revises: 57ae4ec794ac
Create Date: 2019-06-29 19:49:12.089098

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '90d15ff94300'
down_revision = '57ae4ec794ac'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('temp', sa.Column('current_temp', sa.Integer(), nullable=True))
    op.drop_index('ix_temp_target_temp', table_name='temp')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_index('ix_temp_target_temp', 'temp', ['target_temp'], unique=False)
    op.drop_column('temp', 'current_temp')
    # ### end Alembic commands ###
