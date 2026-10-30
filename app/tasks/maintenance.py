import datetime
import logging
import os

from app.services.expiry import (
    disable_or_delete_user_if_expired,
)


def checkpoint_wal_database(app=None):
    """Checkpoint the SQLite WAL file to prevent unbounded growth.

    WAL mode accumulates changes in the -wal file. Periodic checkpointing
    ensures the WAL doesn't grow too large and improves backup reliability.

    This should be called periodically (e.g., daily) via the scheduler.

    Args:
        app: Flask application instance. If None, will try to get from current context.
    """
    if app is None:
        from flask import current_app

        try:
            app = current_app._get_current_object()  # type: ignore
        except RuntimeError:
            logging.error(
                "checkpoint_wal_database called outside application context and no app provided"
            )
            return

    with app.app_context():
        from app.extensions import db

        # Only checkpoint SQLite databases in WAL mode
        if "sqlite" not in str(db.engine.url):
            return

        try:
            # Check if we're actually in WAL mode first
            with db.engine.connect() as conn:
                result = conn.exec_driver_sql("PRAGMA journal_mode").fetchone()
                journal_mode = result[0] if result else "unknown"

                if journal_mode.lower() != "wal":
                    # Not in WAL mode, nothing to checkpoint
                    logging.debug(
                        f"Skipping WAL checkpoint (journal_mode={journal_mode})"
                    )
                    return

                # Execute WAL checkpoint - this merges WAL back into main database
                # PASSIVE mode doesn't block readers/writers
                conn.exec_driver_sql("PRAGMA wal_checkpoint(PASSIVE)")
                logging.info("✅ SQLite WAL checkpoint completed")
        except Exception as e:
            logging.warning(f"Failed to checkpoint WAL: {e}")


# 0x10000: check all tables, not only the tables that this connection used.
# 0x00002: run ANALYZE on the tables that need it.
# The scheduler uses pooled connections, so the "used tables" of one
# connection are random. Thus the daily run also uses 0x10002.
SQLITE_OPTIMIZE_MASK = 0x10002

# Maximum number of index rows that ANALYZE reads. This keeps the run short
# on large tables (SQLite 3.46+ uses the same value for PRAGMA optimize).
SQLITE_ANALYSIS_LIMIT = 400


def optimize_sqlite_database(app=None):
    """Update the SQLite query planner statistics with ``PRAGMA optimize``.

    Without statistics, the query planner can choose a bad index. The
    background leader runs this function once at startup and once a day.

    Args:
        app: Flask application instance. If None, will try to get from current context.
    """
    if app is None:
        from flask import current_app

        try:
            app = current_app._get_current_object()  # type: ignore
        except RuntimeError:
            logging.error(
                "optimize_sqlite_database called outside application context and no app provided"
            )
            return

    with app.app_context():
        from app.extensions import db

        if db.engine.dialect.name != "sqlite":
            return

        try:
            with db.engine.connect() as conn:
                conn.exec_driver_sql(f"PRAGMA analysis_limit={SQLITE_ANALYSIS_LIMIT}")
                conn.exec_driver_sql(f"PRAGMA optimize={SQLITE_OPTIMIZE_MASK:#x}")
                conn.commit()
            logging.info("SQLite PRAGMA optimize completed")
        except Exception as e:
            logging.warning(f"SQLite PRAGMA optimize failed: {e}")


def _get_expiry_check_interval():
    """Get the interval for expiry checks based on environment."""
    # Use 1 minute for development, 15 minutes for production
    if os.getenv("WIZARR_ENABLE_SCHEDULER") == "true":
        return 1  # Development mode: every 1 minute
    return 15  # Production mode: every 15 minutes


def check_expiring(app=None):
    """Check for and process expired users based on expiry action setting.

    Args:
        app: Flask application instance. If None, will try to get from current context.
    """
    if app is None:
        from flask import current_app

        try:
            app = current_app._get_current_object()  # type: ignore
        except RuntimeError:
            # If we're outside application context, we need the app to be passed
            logging.error(
                "check_expiring called outside application context and no app provided"
            )
            return

    with app.app_context():
        processed = disable_or_delete_user_if_expired()
        if len(processed) > 0:
            logging.info(
                "🧹 Expiry cleanup: Processed %s expired users.", len(processed)
            )
        # Only log in development mode to avoid spam in production logs
        elif os.getenv("WIZARR_ENABLE_SCHEDULER") == "true":
            logging.info("🕒 Expiry cleanup: No expired users found.")


def send_expiry_reminders(app=None):
    """Email users whose access expires within the configured reminder window.

    Accounts that are due in the same run and resolve to the same real
    address (e.g. several linked server accounts of one person) are
    combined into a single email listing every due service, instead of
    one email per account. Each group is isolated: a failure sending to
    one group is logged and does not stop the remaining groups, mirroring
    check_expiring()'s use of disable_or_delete_user_if_expired().

    Args:
        app: Flask application instance. If None, will try to get from current context.
    """
    if app is None:
        from flask import current_app

        try:
            app = current_app._get_current_object()  # type: ignore
        except RuntimeError:
            logging.error(
                "send_expiry_reminders called outside application context and no app provided"
            )
            return

    with app.app_context():
        from collections import defaultdict

        from app.extensions import db
        from app.models import SMTPConfiguration
        from app.services.email import EmailSendError, send_expiry_reminder_email
        from app.services.expiry import (
            get_users_needing_reminder,
            parse_reminder_schedule,
        )
        from app.services.user_email import resolve_recipient_email

        config = SMTPConfiguration.query.first()
        if not config or not config.enabled:
            logging.info(
                "📧 Expiry reminders: skipped (SMTP is not configured or not enabled)."
            )
            return

        thresholds = parse_reminder_schedule(config.reminder_schedule)
        if not thresholds:
            logging.info(
                "📧 Expiry reminders: skipped (no reminder schedule configured)."
            )
            return

        now = datetime.datetime.now(datetime.UTC)
        candidates = []  # (user, days_left, due_thresholds)
        for user in get_users_needing_reminder(thresholds):
            # Database stores naive UTC datetimes.
            user_expires = user.expires
            if user_expires.tzinfo is None:
                user_expires = user_expires.replace(tzinfo=datetime.UTC)
            days_left = max(1, round((user_expires - now).total_seconds() / 86400))

            already_sent = set(user.expiry_reminders_sent or [])
            due = {t for t in thresholds if days_left <= t and t not in already_sent}
            if due:
                candidates.append((user, days_left, due))

        groups = defaultdict(list)
        unresolved = 0
        for user, days_left, due in candidates:
            to_email = resolve_recipient_email(user)
            if to_email:
                groups[to_email.lower()].append((user, days_left, due))
            else:
                unresolved += 1
                logging.warning(
                    "📧 Expiry reminders: user %s (%s) is due but has no "
                    "resolvable e-mail address, skipping.",
                    user.id,
                    user.username,
                )

        logging.info(
            "📧 Expiry reminders: thresholds=%s, due accounts=%s, "
            "unresolved=%s, groups to send=%s",
            thresholds,
            len(candidates),
            unresolved,
            len(groups),
        )

        sent = 0
        for to_email, group in groups.items():
            try:
                representative = min(group, key=lambda g: g[1])[0]
                group_days_left = min(days_left for _, days_left, _ in group)
                services = ", ".join(
                    sorted({u.server.name for u, _, _ in group if u.server})
                )

                send_expiry_reminder_email(
                    representative,
                    group_days_left,
                    to_email=to_email,
                    services=services,
                )
                for user, _, due in group:
                    already_sent = set(user.expiry_reminders_sent or [])
                    user.expiry_reminders_sent = sorted(already_sent | due)
                db.session.commit()
                sent += 1
            except EmailSendError as exc:
                db.session.rollback()
                logging.warning(
                    "Failed to send expiry reminder to %s: %s", to_email, exc
                )
            except Exception as exc:
                db.session.rollback()
                logging.error(
                    "Unexpected error sending expiry reminder to %s: %s",
                    to_email,
                    exc,
                )

        if sent:
            logging.info("📧 Sent %s expiry reminder email(s).", sent)


# Keep the sent-email log for this long before a daily cleanup job removes it.
EMAIL_LOG_RETENTION_DAYS = 90


def cleanup_email_logs(app=None):
    """Delete email log entries older than EMAIL_LOG_RETENTION_DAYS.

    Args:
        app: Flask application instance. If None, will try to get from current context.
    """
    if app is None:
        from flask import current_app

        try:
            app = current_app._get_current_object()  # type: ignore
        except RuntimeError:
            logging.error(
                "cleanup_email_logs called outside application context and no app provided"
            )
            return

    with app.app_context():
        from app.extensions import db
        from app.models import EmailLog

        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            days=EMAIL_LOG_RETENTION_DAYS
        )
        deleted = EmailLog.query.filter(EmailLog.sent_at < cutoff).delete()
        db.session.commit()

        if deleted:
            logging.info("🧹 Deleted %s old email log entries.", deleted)
