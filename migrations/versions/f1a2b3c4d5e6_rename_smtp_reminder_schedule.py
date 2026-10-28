"""rename smtp reminder_days_before to reminder_schedule

Revision ID: f1a2b3c4d5e6
Revises: a1c2d3e4f5a6
Create Date: 2026-10-28 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "a1c2d3e4f5a6"
branch_labels = None
depends_on = None

OLD_COLUMN = "reminder_days_before"
NEW_COLUMN = "reminder_schedule"


def _existing_columns() -> set[str]:
    return {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns("smtp_configuration")
    }


def upgrade():
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_smtp_configuration")
    existing = _existing_columns()

    if NEW_COLUMN not in existing:
        op.add_column(
            "smtp_configuration", sa.Column(NEW_COLUMN, sa.String(), nullable=True)
        )

    if OLD_COLUMN in existing:
        conn = op.get_bind()
        conn.execute(
            sa.text(
                f"UPDATE smtp_configuration SET {NEW_COLUMN} = CASE "
                f"WHEN {OLD_COLUMN} IS NOT NULL AND {OLD_COLUMN} > 0 "
                f"THEN CAST({OLD_COLUMN} AS TEXT) ELSE '' END "
                f"WHERE {NEW_COLUMN} IS NULL"
            )
        )

    # No table references smtp_configuration.id, so unlike the `user` table
    # rebuild in the next migration, this batch rebuild needs no PRAGMA
    # foreign_keys dance.
    with op.batch_alter_table("smtp_configuration", schema=None) as batch_op:
        batch_op.alter_column(
            NEW_COLUMN,
            existing_type=sa.String(),
            nullable=False,
            server_default="3",
        )
        if OLD_COLUMN in existing:
            batch_op.drop_column(OLD_COLUMN)


def downgrade():
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_smtp_configuration")
    existing = _existing_columns()

    if OLD_COLUMN not in existing:
        op.add_column(
            "smtp_configuration", sa.Column(OLD_COLUMN, sa.Integer(), nullable=True)
        )

    # Lossy by design: collapse the CSV schedule back to a single int,
    # picking the longest lead time (the most conservative choice).
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(f"SELECT id, {NEW_COLUMN} FROM smtp_configuration")
    ).fetchall()
    for row in rows:
        raw = row[1] or ""
        values = [int(t) for t in raw.split(",") if t.strip().isdigit() and int(t) > 0]
        conn.execute(
            sa.text(f"UPDATE smtp_configuration SET {OLD_COLUMN} = :v WHERE id = :id"),
            {"v": max(values) if values else 0, "id": row[0]},
        )

    with op.batch_alter_table("smtp_configuration", schema=None) as batch_op:
        batch_op.alter_column(
            OLD_COLUMN,
            existing_type=sa.Integer(),
            nullable=False,
            server_default="3",
        )
        if NEW_COLUMN in existing:
            batch_op.drop_column(NEW_COLUMN)
