"""Tests for the SMTP settings route: pre-fill, validation, normalization."""

import pytest

from app.models import SMTPConfiguration


@pytest.fixture(autouse=True)
def _cleanup_smtp_config(session):
    # The `session` fixture does not clean up this singleton config table.
    yield
    SMTPConfiguration.query.delete()
    session.commit()


def _base_form(**overrides):
    data = {
        "enabled": "y",
        "host": "smtp.example.com",
        "port": "587",
        "encryption": "starttls",
        "username": "",
        "password": "",
        "from_email": "wizarr@example.com",
        "from_name": "Wizarr",
        "reminder_schedule": "3",
    }
    data.update(overrides)
    return data


def test_save_normalizes_the_reminder_schedule(admin_client, session):
    resp = admin_client.post(
        "/settings/smtp", data=_base_form(reminder_schedule="7, 3,3,1")
    )

    assert resp.status_code == 200
    config = SMTPConfiguration.query.first()
    assert config.reminder_schedule == "7,3,1"


def test_save_accepts_empty_schedule_to_disable_reminders(admin_client, session):
    resp = admin_client.post("/settings/smtp", data=_base_form(reminder_schedule=""))

    assert resp.status_code == 200
    config = SMTPConfiguration.query.first()
    assert config.reminder_schedule == ""


def test_save_rejects_invalid_schedule_token(admin_client, session):
    resp = admin_client.post(
        "/settings/smtp", data=_base_form(reminder_schedule="7,abc,1")
    )

    assert resp.status_code == 200
    assert b"positive whole numbers" in resp.data
    assert SMTPConfiguration.query.first() is None


def test_get_prefills_the_saved_schedule(admin_client, session):
    config = SMTPConfiguration(
        enabled=True,
        host="smtp.example.com",
        port=587,
        encryption="none",
        from_email="wizarr@example.com",
        reminder_schedule="7,3,1",
    )
    session.add(config)
    session.commit()

    resp = admin_client.get("/settings/smtp")

    assert resp.status_code == 200
    assert b"7,3,1" in resp.data


def test_save_persists_custom_reminder_content(admin_client, session):
    resp = admin_client.post(
        "/settings/smtp",
        data=_base_form(
            reminder_subject="Hi {{username}}",
            reminder_body="{{days_left}} days left",
        ),
    )

    assert resp.status_code == 200
    config = SMTPConfiguration.query.first()
    assert config.reminder_subject == "Hi {{username}}"
    assert config.reminder_body == "{{days_left}} days left"


def test_save_blank_reminder_content_stores_none(admin_client, session):
    resp = admin_client.post(
        "/settings/smtp",
        data=_base_form(reminder_subject="", reminder_body=""),
    )

    assert resp.status_code == 200
    config = SMTPConfiguration.query.first()
    assert config.reminder_subject is None
    assert config.reminder_body is None


def test_get_prefills_custom_reminder_content(admin_client, session):
    config = SMTPConfiguration(
        enabled=True,
        host="smtp.example.com",
        port=587,
        encryption="none",
        from_email="wizarr@example.com",
        reminder_schedule="3",
        reminder_subject="Custom subject",
        reminder_body="Custom body text",
    )
    session.add(config)
    session.commit()

    resp = admin_client.get("/settings/smtp")

    assert resp.status_code == 200
    assert b"Custom subject" in resp.data
    assert b"Custom body text" in resp.data
