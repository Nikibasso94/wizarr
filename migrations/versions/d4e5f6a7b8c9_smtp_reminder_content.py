"""add smtp reminder subject and body

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-28 11:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "smtp_configuration", sa.Column("reminder_subject", sa.String(), nullable=True)
    )
    op.add_column(
        "smtp_configuration", sa.Column("reminder_body", sa.Text(), nullable=True)
    )


def downgrade():
    op.drop_column("smtp_configuration", "reminder_body")
    op.drop_column("smtp_configuration", "reminder_subject")
