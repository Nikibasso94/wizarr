"""add user.expiry_reminders_sent, drop expiry_reminder_sent_at

Revision ID: b2c3d4e5f6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-10-28 00:05:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

OLD_COLUMN = "expiry_reminder_sent_at"
NEW_COLUMN = "expiry_reminders_sent"


def _existing_columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("user")}


def _restore_lower_username_index() -> None:
    # batch_alter_table reflects the table to rebuild it, but SQLAlchemy
    # cannot reflect this expression-based index (see the SAWarning
    # "Skipped unsupported reflection of expression-based index
    # ix_user_server_lower_username" in migrations/env.py's logs), so the
    # rebuild silently drops it without recreating it. Without this index,
    # LDAP/API username lookups fall back to a full table scan.
    conn = op.get_bind()
    exists = conn.execute(
        sa.text(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='ix_user_server_lower_username'"
        )
    ).first()
    if not exists:
        op.create_index(
            "ix_user_server_lower_username",
            "user",
            ["server_id", sa.text("lower(username)")],
        )


def upgrade():
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_user")
    existing = _existing_columns()

    if NEW_COLUMN not in existing:
        # Plain ADD COLUMN does not rebuild the table, so
        # invitation_user/password_reset_token/activity_session rows are
        # untouched by this step.
        op.add_column("user", sa.Column(NEW_COLUMN, sa.JSON(), nullable=True))

    if OLD_COLUMN not in existing:
        return

    # batch_alter_table's drop_column rebuilds the table: CREATE new,
    # COPY, DROP TABLE old, RENAME. PRAGMA foreign_keys=ON is always on in
    # this app (see app/extensions.py's Engine "connect" listener, which
    # also governs the connection Alembic migrations run on), and in
    # SQLite that makes the DROP TABLE perform an implicit DELETE that
    # fires ON DELETE CASCADE for every row in invitation_user,
    # password_reset_token and activity_session (all FK user.id
    # ondelete="CASCADE"), wiping them. This exact bug hit invitation_user
    # in 20251226_add_ldap_support.py and needed
    # 20260401_repair_invitation_user_data.py to fix. Disable FK
    # enforcement for the rebuild, matching a1c2d3e4f5a6's own downgrade
    # of this same column.
    conn = op.get_bind()
    is_sqlite = conn.dialect.name == "sqlite"
    if is_sqlite:
        conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        with op.batch_alter_table("user", schema=None) as batch_op:
            batch_op.drop_column(OLD_COLUMN)
    finally:
        if is_sqlite:
            conn.execute(sa.text("PRAGMA foreign_keys=ON"))

    if is_sqlite:
        _restore_lower_username_index()


def downgrade():
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_user")
    existing = _existing_columns()

    if OLD_COLUMN not in existing:
        op.add_column("user", sa.Column(OLD_COLUMN, sa.DateTime(), nullable=True))

    if NEW_COLUMN not in existing:
        return

    conn = op.get_bind()
    is_sqlite = conn.dialect.name == "sqlite"
    if is_sqlite:
        conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        with op.batch_alter_table("user", schema=None) as batch_op:
            batch_op.drop_column(NEW_COLUMN)
    finally:
        if is_sqlite:
            conn.execute(sa.text("PRAGMA foreign_keys=ON"))

    if is_sqlite:
        _restore_lower_username_index()
