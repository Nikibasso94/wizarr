"""Tests for the expiry-reminder query, the parser, and the scheduled job."""

import datetime
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import SMTPConfiguration, User
from app.services.email import EmailSendError
from app.services.expiry import get_users_needing_reminder, parse_reminder_schedule
from app.tasks.maintenance import send_expiry_reminders


def _user(session, *, expires_in_days, email="user@example.com", reminded=None):
    user = User(
        token=f"tok-{id(object())}",
        username="user",
        email=email,
        code="CODE",
        expires=datetime.datetime.now(datetime.UTC)
        + datetime.timedelta(days=expires_in_days),
        expiry_reminders_sent=reminded,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture(autouse=True)
def _cleanup_smtp_config(session):
    # The `session` fixture does not clean up this singleton config table,
    # and a leftover row would be picked up by SMTPConfiguration.query.first()
    # in a later test, so every test in this file cleans up after itself.
    yield
    SMTPConfiguration.query.delete()
    session.commit()


def _smtp_config(session, schedule="3"):
    config = SMTPConfiguration(
        enabled=True,
        host="smtp.example.com",
        port=587,
        encryption="none",
        from_email="wizarr@example.com",
        reminder_schedule=schedule,
    )
    session.add(config)
    session.commit()
    return config


@pytest.fixture
def smtp_config(session):
    return _smtp_config(session)


# ─── parse_reminder_schedule ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("7,3,1", [7, 3, 1]),
        ("7, 3,3,1", [7, 3, 1]),
        ("", []),
        (None, []),
        ("   ", []),
        ("0,-1,3", [3]),
        ("abc,3", [3]),
        ("3", [3]),
        ("1,7,3", [7, 3, 1]),
    ],
)
def test_parse_reminder_schedule(raw, expected):
    assert parse_reminder_schedule(raw) == expected


# ─── get_users_needing_reminder ─────────────────────────────────────────────


def test_get_users_needing_reminder_filters_by_window(session):
    within_window = _user(session, expires_in_days=2)
    outside_window = _user(session, expires_in_days=10)
    already_expired = _user(session, expires_in_days=-1)
    no_email = _user(session, expires_in_days=1, email=None)

    result = get_users_needing_reminder([3])

    ids = {u.id for u in result}
    assert within_window.id in ids
    assert outside_window.id not in ids
    assert already_expired.id not in ids
    assert no_email.id not in ids


def test_get_users_needing_reminder_uses_the_largest_threshold(session):
    user = _user(session, expires_in_days=5)

    assert get_users_needing_reminder([1, 3]) == []
    assert [u.id for u in get_users_needing_reminder([1, 3, 7])] == [user.id]


def test_get_users_needing_reminder_empty_thresholds_returns_nothing(session):
    _user(session, expires_in_days=1)

    assert get_users_needing_reminder([]) == []


# ─── send_expiry_reminders ──────────────────────────────────────────────────


def test_send_expiry_reminders_noop_when_smtp_disabled(app, session):
    _user(session, expires_in_days=1)

    with patch("app.services.email.send_expiry_reminder_email") as send:
        send_expiry_reminders(app)

    send.assert_not_called()


def test_send_expiry_reminders_noop_when_schedule_is_empty(app, session):
    _smtp_config(session, schedule="")
    _user(session, expires_in_days=1)

    with patch("app.services.email.send_expiry_reminder_email") as send:
        send_expiry_reminders(app)

    send.assert_not_called()


def test_send_expiry_reminders_sends_and_marks_threshold(app, smtp_config, session):
    user_id = _user(session, expires_in_days=1).id
    sent_to_ids = []

    with patch(
        "app.services.email.send_expiry_reminder_email",
        side_effect=lambda user, _days_left: sent_to_ids.append(user.id),
    ):
        send_expiry_reminders(app)

    assert sent_to_ids == [user_id]
    assert db.session.get(User, user_id).expiry_reminders_sent == [3]


def test_send_expiry_reminders_is_idempotent(app, smtp_config, session):
    _user(session, expires_in_days=1)

    with patch("app.services.email.send_expiry_reminder_email") as send:
        send_expiry_reminders(app)
        send_expiry_reminders(app)

    send.assert_called_once()


def test_send_expiry_reminders_isolates_per_user_failures(app, smtp_config, session):
    failing_id = _user(session, expires_in_days=1, email="fail@example.com").id
    ok_id = _user(session, expires_in_days=2, email="ok@example.com").id

    def _send(user, _days_left):
        if user.id == failing_id:
            raise EmailSendError("boom")

    with patch(
        "app.services.email.send_expiry_reminder_email", side_effect=_send
    ) as send:
        send_expiry_reminders(app)

    assert send.call_count == 2
    assert db.session.get(User, failing_id).expiry_reminders_sent is None
    assert db.session.get(User, ok_id).expiry_reminders_sent == [3]


def test_send_expiry_reminders_sends_one_email_per_threshold_crossed(app, session):
    """A schedule of 7,3,1 fires once per threshold, across separate runs,
    as the user's remaining days drops below each one."""
    _smtp_config(session, schedule="7,3,1")
    user_id = _user(session, expires_in_days=5).id

    with patch("app.services.email.send_expiry_reminder_email") as send:
        send_expiry_reminders(app)  # days_left=5 -> only the 7-day threshold is due

    assert send.call_count == 1
    assert db.session.get(User, user_id).expiry_reminders_sent == [7]

    # Simulate time passing: the user now has 2 days left.
    user = db.session.get(User, user_id)
    user.expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=2)
    db.session.commit()

    with patch("app.services.email.send_expiry_reminder_email") as send:
        send_expiry_reminders(app)  # days_left=2 -> the 3-day threshold is now due

    assert send.call_count == 1
    assert db.session.get(User, user_id).expiry_reminders_sent == [3, 7]


def test_send_expiry_reminders_sends_one_email_when_multiple_thresholds_catch_up(
    app, session
):
    """A short-lived invite that starts already below several thresholds
    gets exactly one email in that run, but all applicable thresholds are
    marked sent (no later catch-up spam)."""
    _smtp_config(session, schedule="7,3,1")
    user_id = _user(session, expires_in_days=2).id

    with patch("app.services.email.send_expiry_reminder_email") as send:
        send_expiry_reminders(app)

    assert send.call_count == 1
    assert db.session.get(User, user_id).expiry_reminders_sent == [3, 7]

    with patch("app.services.email.send_expiry_reminder_email") as send:
        send_expiry_reminders(app)

    send.assert_not_called()


# ─── reset on expiry change ─────────────────────────────────────────────────


def test_admin_editing_expiry_resets_reminder_flag(admin_client, session):
    """Editing a user's expiry in the admin UI must reset the reminder flag,
    so a renewed user becomes eligible for a fresh reminder.
    """
    user = _user(session, expires_in_days=1, reminded=[7, 3])

    resp = admin_client.post(
        f"/user/{user.id}",
        data={"expires": "2030-01-01T00:00"},
    )

    assert resp.status_code == 200
    assert db.session.get(User, user.id).expiry_reminders_sent is None


def test_admin_clearing_expiry_resets_reminder_flag(admin_client, session):
    user = _user(session, expires_in_days=1, reminded=[7, 3])

    resp = admin_client.post(f"/user/{user.id}", data={"expires": ""})

    assert resp.status_code == 200
    assert db.session.get(User, user.id).expiry_reminders_sent is None
