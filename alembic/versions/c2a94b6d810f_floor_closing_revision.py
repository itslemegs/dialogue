"""Preserve optional wording recorded at Proposal Floor closure."""
from alembic import op
import sqlalchemy as sa

revision = "c2a94b6d810f"
down_revision = "e8c741ac9021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("proposal_floor_state", sa.Column("closing_revision_text", sa.Text(), nullable=True))
    op.add_column("proposal_floor_state", sa.Column("closing_revision_by_id", sa.Integer(), nullable=True))
    op.add_column("proposal_floor_state", sa.Column("closing_revision_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_pfloor_closing_author", "proposal_floor_state", "user", ["closing_revision_by_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_pfloor_closing_author", "proposal_floor_state", type_="foreignkey")
    op.drop_column("proposal_floor_state", "closing_revision_at")
    op.drop_column("proposal_floor_state", "closing_revision_by_id")
    op.drop_column("proposal_floor_state", "closing_revision_text")
