"""Add optional locally stored event cover reference.

Revision ID: e8c741ac9021
Revises: 7e95386f37b0
"""
from alembic import op
import sqlalchemy as sa

revision = 'e8c741ac9021'
down_revision = '7e95386f37b0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('event', sa.Column('cover_image', sa.String(), nullable=True))


def downgrade():
    op.drop_column('event', 'cover_image')
