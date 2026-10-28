"""Unit tests for the SMTP email-sending service."""

import smtplib
from unittest.mock import MagicMock, patch

import pytest

from app.models import SMTPConfiguration, User
from app.services.email import EmailSendError, send_email, send_expiry_reminder_email
from app.services.ldap.encryption import encrypt_credential


def _user(session, *, username="alice", email="alice@example.com"):
    user = User(
        token=f"tok-{id(object())}", username=username, email=email, code="CODE"
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture(autouse=True)
def _cleanup_smtp_config(session):
    # The `session` fixture does not clean up this singleton config table.
    yield
    SMTPConfiguration.query.delete()
    session.commit()


@pytest.fixture
def smtp_config(session):
    config = SMTPConfiguration(
        enabled=True,
        host="smtp.example.com",
        port=587,
        encryption="starttls",
        username="user@example.com",
        password_encrypted=encrypt_credential("secret"),
        from_email="wizarr@example.com",
        from_name="Wizarr",
        reminder_schedule="3",
    )
    session.add(config)
    session.commit()
    yield config
    SMTPConfiguration.query.delete()
    session.commit()


def test_send_email_raises_when_not_configured(session):
    assert SMTPConfiguration.query.first() is None

    with pytest.raises(EmailSendError):
        send_email("to@example.com", "Subject", "<p>Hi</p>")


def test_send_email_raises_when_disabled(session):
    config = SMTPConfiguration(
        enabled=False,
        host="smtp.example.com",
        port=587,
        encryption="none",
        from_email="wizarr@example.com",
        reminder_schedule="3",
    )
    session.add(config)
    session.commit()

    try:
        with pytest.raises(EmailSendError):
            send_email("to@example.com", "Subject", "<p>Hi</p>")
    finally:
        SMTPConfiguration.query.delete()
        session.commit()


def test_send_email_uses_starttls_and_logs_in(smtp_config):
    with patch("app.services.email.smtplib.SMTP") as smtp_cls:
        server = MagicMock()
        smtp_cls.return_value.__enter__.return_value = server

        send_email("to@example.com", "Subject", "<p>Hi</p>", "Hi")

    server.starttls.assert_called_once()
    server.login.assert_called_once_with("user@example.com", "secret")
    server.send_message.assert_called_once()
    sent_msg = server.send_message.call_args[0][0]
    assert sent_msg["To"] == "to@example.com"
    assert sent_msg["Subject"] == "Subject"
    assert "Wizarr" in sent_msg["From"]


def test_send_email_uses_ssl(smtp_config):
    smtp_config.encryption = "ssl"

    with patch("app.services.email.smtplib.SMTP_SSL") as smtp_ssl_cls:
        server = MagicMock()
        smtp_ssl_cls.return_value.__enter__.return_value = server

        send_email("to@example.com", "Subject", "<p>Hi</p>")

    server.login.assert_called_once()
    server.send_message.assert_called_once()


def test_send_email_wraps_smtp_exceptions(smtp_config):
    with patch("app.services.email.smtplib.SMTP") as smtp_cls:
        server = MagicMock()
        server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
        smtp_cls.return_value.__enter__.return_value = server

        with pytest.raises(EmailSendError):
            send_email("to@example.com", "Subject", "<p>Hi</p>")


# ─── send_expiry_reminder_email: custom content override ───────────────────


def test_reminder_uses_default_copy_when_no_config(session):
    user = _user(session)

    with pytest.raises(EmailSendError):
        send_expiry_reminder_email(user, 3)


def test_reminder_uses_default_copy_when_custom_fields_blank(smtp_config, session):
    user = _user(session, username="bob", email="bob@example.com")

    with patch("app.services.email.send_email") as send:
        send_expiry_reminder_email(user, 3)

    args = send.call_args.args
    assert args[0] == "bob@example.com"
    assert args[1] == "Your access is expiring soon"


def test_reminder_uses_custom_content_when_both_fields_set(smtp_config, session):
    smtp_config.reminder_subject = "Hi {{username}}, {{days_left}} days left"
    smtp_config.reminder_body = "Dear {{username}}, renew at {{email}}."
    session.commit()
    user = _user(session, username="carol", email="carol@example.com")

    with patch("app.services.email.send_email") as send:
        send_expiry_reminder_email(user, 5)

    args = send.call_args.args
    assert args[0] == "carol@example.com"
    assert args[1] == "Hi carol, 5 days left"
    assert "Dear carol" in args[2]  # html body
    assert "carol@example.com" in args[2]


def test_reminder_falls_back_when_only_subject_is_set(smtp_config, session):
    smtp_config.reminder_subject = "Custom subject"
    smtp_config.reminder_body = None
    session.commit()
    user = _user(session)

    with patch("app.services.email.send_email") as send:
        send_expiry_reminder_email(user, 2)

    assert send.call_args.args[1] == "Your access is expiring soon"
