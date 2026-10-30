"""Tests for the email send log: EmailLog rows written by send_email(),
the Sent Emails settings page, and the retention cleanup job.
"""

import datetime
import smtplib
from unittest.mock import MagicMock, patch

import pytest

from app.extensions import db
from app.models import EmailLog, SMTPConfiguration, User
from app.services.email import (
    EmailSendError,
    send_email,
    send_expiry_reminder_email,
    send_invite_email,
    send_test_email,
)
from app.tasks.maintenance import cleanup_email_logs


def _user(session, *, username="alice", email="alice@example.com"):
    user = User(
        token=f"tok-{id(object())}", username=username, email=email, code="CODE"
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture(autouse=True)
def _cleanup(session):
    # The `session` fixture does not clean up these tables.
    yield
    SMTPConfiguration.query.delete()
    EmailLog.query.delete()
    session.commit()


@pytest.fixture
def smtp_config(session):
    config = SMTPConfiguration(
        enabled=True,
        host="smtp.example.com",
        port=587,
        encryption="none",
        from_email="wizarr@example.com",
    )
    session.add(config)
    session.commit()
    return config


# ─── send_email() logging ───────────────────────────────────────────────────


def test_success_is_logged(smtp_config, session):
    with patch("app.services.email.smtplib.SMTP") as smtp_cls:
        server = MagicMock()
        smtp_cls.return_value.__enter__.return_value = server
        send_email("to@example.com", "Subject", "<p>Hi</p>", kind="custom")

    log = EmailLog.query.one()
    assert log.to_email == "to@example.com"
    assert log.subject == "Subject"
    assert log.kind == "custom"
    assert log.status == "sent"
    assert log.error_message is None


def test_smtp_failure_is_logged(smtp_config, session):
    with patch("app.services.email.smtplib.SMTP") as smtp_cls:
        server = MagicMock()
        server.send_message.side_effect = smtplib.SMTPException("boom")
        smtp_cls.return_value.__enter__.return_value = server

        with pytest.raises(EmailSendError):
            send_email("to@example.com", "Subject", "<p>Hi</p>", kind="reminder")

    log = EmailLog.query.one()
    assert log.kind == "reminder"
    assert log.status == "failed"
    assert "boom" in log.error_message


def test_not_configured_is_logged(session):
    assert SMTPConfiguration.query.first() is None

    with pytest.raises(EmailSendError):
        send_email("to@example.com", "Subject", "<p>Hi</p>", kind="test")

    log = EmailLog.query.one()
    assert log.kind == "test"
    assert log.status == "failed"
    assert "not configured" in log.error_message


def test_default_kind_is_other(smtp_config, session):
    with patch("app.services.email.smtplib.SMTP") as smtp_cls:
        server = MagicMock()
        smtp_cls.return_value.__enter__.return_value = server
        send_email("to@example.com", "Subject", "<p>Hi</p>")

    assert EmailLog.query.one().kind == "other"


# ─── each higher-level caller tags the right kind ──────────────────────────


def test_send_invite_email_logs_kind_invite(smtp_config, session):
    with patch("app.services.email.smtplib.SMTP") as smtp_cls:
        server = MagicMock()
        smtp_cls.return_value.__enter__.return_value = server
        send_invite_email("to@example.com", "https://example.com/j/ABC", "ABC")

    assert EmailLog.query.one().kind == "invite"


def test_send_expiry_reminder_email_logs_kind_reminder(smtp_config, session):
    user = _user(session)
    with patch("app.services.email.smtplib.SMTP") as smtp_cls:
        server = MagicMock()
        smtp_cls.return_value.__enter__.return_value = server
        send_expiry_reminder_email(user, 3)

    assert EmailLog.query.one().kind == "reminder"


def test_send_test_email_logs_kind_test(smtp_config, session):
    with patch("app.services.email.smtplib.SMTP") as smtp_cls:
        server = MagicMock()
        smtp_cls.return_value.__enter__.return_value = server
        send_test_email("to@example.com", smtp_config)

    assert EmailLog.query.one().kind == "test"


def test_bulk_custom_email_route_logs_kind_custom(admin_client, smtp_config, session):
    user = _user(session, username="bob", email="bob@example.com")

    with patch("app.services.email.smtplib.SMTP") as smtp_cls:
        server = MagicMock()
        smtp_cls.return_value.__enter__.return_value = server
        admin_client.post(
            "/users/send-email",
            data={"uids": [str(user.id)], "subject": "Subject", "body": "Body"},
            headers={"HX-Request": "true"},
        )

    assert EmailLog.query.one().kind == "custom"


# ─── Sent Emails settings page ──────────────────────────────────────────────


def test_sent_emails_table_filters_by_status_and_kind(admin_client, session):
    session.add(
        EmailLog(to_email="a@example.com", subject="A", kind="invite", status="sent")
    )
    session.add(
        EmailLog(
            to_email="b@example.com",
            subject="B",
            kind="reminder",
            status="failed",
            error_message="boom",
        )
    )
    session.commit()

    resp = admin_client.get("/settings/sent-emails/table")
    assert b"a@example.com" in resp.data
    assert b"b@example.com" in resp.data

    resp = admin_client.get("/settings/sent-emails/table?status=failed")
    assert b"a@example.com" not in resp.data
    assert b"b@example.com" in resp.data

    resp = admin_client.get("/settings/sent-emails/table?kind=invite")
    assert b"a@example.com" in resp.data
    assert b"b@example.com" not in resp.data

    resp = admin_client.get("/settings/sent-emails/table?status=sent&kind=reminder")
    assert b"a@example.com" not in resp.data
    assert b"b@example.com" not in resp.data


def test_sent_emails_table_paginates(admin_client, session):
    for i in range(55):
        session.add(
            EmailLog(
                to_email=f"user{i}@example.com", subject="S", kind="test", status="sent"
            )
        )
    session.commit()

    resp = admin_client.get("/settings/sent-emails/table")
    assert b"Page 1 / 2" in resp.data

    resp = admin_client.get("/settings/sent-emails/table?page=2")
    assert b"Page 2 / 2" in resp.data


# ─── cleanup_email_logs() ───────────────────────────────────────────────────


def test_cleanup_deletes_only_old_entries(app, session):
    now = datetime.datetime.now(datetime.UTC)
    old = EmailLog(
        to_email="old@example.com",
        subject="Old",
        kind="test",
        status="sent",
        sent_at=now - datetime.timedelta(days=91),
    )
    recent = EmailLog(
        to_email="recent@example.com",
        subject="Recent",
        kind="test",
        status="sent",
        sent_at=now - datetime.timedelta(days=10),
    )
    session.add_all([old, recent])
    session.commit()
    recent_id = recent.id

    cleanup_email_logs(app)

    remaining = EmailLog.query.all()
    assert [log.id for log in remaining] == [recent_id]
    assert db.session.get(EmailLog, recent_id) is not None
