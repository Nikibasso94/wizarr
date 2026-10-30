import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from flask import render_template

from app.models import EmailLog, SMTPConfiguration, User
from app.services.ldap.encryption import decrypt_credential

logger = logging.getLogger(__name__)


class EmailSendError(Exception):
    """Raised when an email could not be sent. Callers decide whether to swallow it."""


def _get_active_config() -> SMTPConfiguration | None:
    config = SMTPConfiguration.query.first()
    if not config or not config.enabled:
        return None
    return config


def _log_email(
    to_email: str, subject: str, kind: str, status: str, error_message: str | None
) -> None:
    """Record one send attempt. A logging failure must never mask or block
    the real send outcome, so this isolates its own errors.
    """
    from app.extensions import db

    try:
        db.session.add(
            EmailLog(
                to_email=to_email,
                subject=subject,
                kind=kind,
                status=status,
                error_message=error_message,
            )
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("Failed to record email log for %s: %s", to_email, exc)


def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    config: SMTPConfiguration | None = None,
    *,
    kind: str = "other",
) -> None:
    """Low-level send. Raises EmailSendError on any failure."""
    config = config or _get_active_config()
    if config is None:
        _log_email(
            to_email, subject, kind, "failed", "SMTP is not configured or not enabled"
        )
        raise EmailSendError("SMTP is not configured or not enabled")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = (
        f"{config.from_name} <{config.from_email}>"
        if config.from_name
        else config.from_email
    )
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    # Spam filters expect a Message-ID whose domain matches the sender, not
    # the container's internal hostname (the default Message-ID domain).
    domain = config.from_email.rsplit("@", 1)[-1]
    msg["Message-ID"] = make_msgid(domain=domain)
    msg.set_content(text_body or html_body)
    if text_body:
        msg.add_alternative(html_body, subtype="html")

    password = (
        decrypt_credential(config.password_encrypted)
        if config.password_encrypted
        else None
    )

    try:
        if config.encryption == "ssl":
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                config.host, config.port, context=context, timeout=10
            ) as server:
                if config.username and password:
                    server.login(config.username, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(config.host, config.port, timeout=10) as server:
                if config.encryption == "starttls":
                    server.starttls(context=ssl.create_default_context())
                if config.username and password:
                    server.login(config.username, password)
                server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("Failed to send email to %s: %s", to_email, exc)
        _log_email(to_email, subject, kind, "failed", str(exc))
        raise EmailSendError(str(exc)) from exc

    _log_email(to_email, subject, kind, "sent", None)


def send_invite_email(to_email: str, invite_link: str, invite_code: str) -> None:
    html = render_template("email/invite.html", link=invite_link, code=invite_code)
    text = render_template("email/invite.txt", link=invite_link, code=invite_code)
    send_email(to_email, "You've been invited", html, text, kind="invite")


def send_expiry_reminder_email(
    user: User, days_left: int, *, to_email: str | None = None, services: str = ""
) -> None:
    """Send one reminder, optionally combining several of the same person's
    accounts into one email. `to_email` overrides `user.email` - needed
    when `user` is just a representative picked from a group of accounts
    resolved to the same real address via identity linking. `services` is
    a pre-joined, human-readable list of the server names involved, for
    the {{services}} placeholder in a custom reminder message.
    """
    to_email = to_email or user.email
    config = _get_active_config()
    custom_subject = (config.reminder_subject or "").strip() if config else ""
    custom_body = (config.reminder_body or "").strip() if config else ""

    if custom_subject and custom_body:
        from app.services.custom_email import render_custom_email

        subject, html, text = render_custom_email(
            custom_subject,
            custom_body,
            user,
            extra={"days_left": str(days_left), "services": services},
        )
    else:
        subject = "Your access is expiring soon"
        html = render_template(
            "email/expiry_reminder.html", user=user, days_left=days_left
        )
        text = render_template(
            "email/expiry_reminder.txt", user=user, days_left=days_left
        )

    send_email(to_email, subject, html, text, config=config, kind="reminder")


def send_test_email(to_email: str, config: SMTPConfiguration) -> None:
    """Used by the settings 'send test email' button, with an unsaved config."""
    send_email(
        to_email,
        "Wizarr SMTP test email",
        "<p>This is a test email from Wizarr. If you received this, SMTP is configured correctly.</p>",
        "This is a test email from Wizarr. If you received this, SMTP is configured correctly.",
        config=config,
        kind="test",
    )
