"""Tests for the bulk custom-email-to-users feature: placeholder
substitution, the compose modal, template loading, and the send route.
"""

from unittest.mock import patch

import pytest

from app.models import EmailTemplate, Identity, MediaServer, User
from app.services.custom_email import substitute_placeholders
from app.services.email import EmailSendError

HX = {"HX-Request": "true"}


def _user(
    session,
    *,
    username="alice",
    email="alice@example.com",
    identity_id=None,
    server_id=None,
):
    user = User(
        token=f"tok-{id(object())}",
        username=username,
        email=email,
        code="CODE",
        identity_id=identity_id,
        server_id=server_id,
    )
    session.add(user)
    session.commit()
    return user


def _server(session, name, server_type="plex"):
    server = MediaServer(
        name=name, server_type=server_type, url="http://media.local", api_key="k"
    )
    session.add(server)
    session.commit()
    return server


@pytest.fixture(autouse=True)
def _cleanup_email_templates(session):
    yield
    EmailTemplate.query.delete()
    session.commit()


# ─── substitute_placeholders ────────────────────────────────────────────────


def test_substitute_placeholders_replaces_known_tokens(session):
    user = _user(session, username="bob", email="bob@example.com")

    result = substitute_placeholders("Hi {{username}}, we sent this to {{email}}", user)

    assert result == "Hi bob, we sent this to bob@example.com"


def test_substitute_placeholders_leaves_unknown_tokens(session):
    user = _user(session)

    result = substitute_placeholders("{{username}} and {{mystery}}", user)

    assert result == "alice and {{mystery}}"


def test_substitute_placeholders_tolerates_whitespace(session):
    user = _user(session, username="carol")

    assert substitute_placeholders("{{ username }}", user) == "carol"


def test_substitute_placeholders_uses_identity_nickname(session):
    identity = Identity(nickname="Carol Nickname")
    session.add(identity)
    session.commit()
    user = _user(session, username="carol", identity_id=identity.id)

    assert substitute_placeholders("{{username}}", user) == "Carol Nickname"


# ─── email_users_modal (compose modal) ──────────────────────────────────────


def test_modal_embeds_uids_as_hidden_fields(admin_client, session):
    u1 = _user(session, username="u1", email="u1@example.com")
    u2 = _user(session, username="u2", email="u2@example.com")

    resp = admin_client.get(f"/users/email-modal?uids={u1.id}&uids={u2.id}", headers=HX)

    assert resp.status_code == 200
    html = resp.data.decode()
    assert f'name="uids" value="{u1.id}"' in html
    assert f'name="uids" value="{u2.id}"' in html


def test_modal_with_no_uids_returns_400(admin_client, session):
    resp = admin_client.get("/users/email-modal", headers=HX)

    assert resp.status_code == 400


def test_modal_prefills_from_template_id(admin_client, session):
    user = _user(session)
    tmpl = EmailTemplate(
        name="Donation ask", subject="Please help", body="We need **help**."
    )
    session.add(tmpl)
    session.commit()

    resp = admin_client.get(
        f"/users/email-modal?uids={user.id}&template_id={tmpl.id}", headers=HX
    )

    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Please help" in html
    assert "We need **help**." in html


# ─── load_email_template ────────────────────────────────────────────────────


def test_load_email_template_returns_subject_and_body(admin_client, session):
    tmpl = EmailTemplate(name="Greeting", subject="Hello!", body="Hi there.")
    session.add(tmpl)
    session.commit()

    resp = admin_client.get(f"/users/email-templates/{tmpl.id}", headers=HX)

    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Hello!" in html
    assert "Hi there." in html


# ─── send_users_email ───────────────────────────────────────────────────────


def test_sends_to_all_selected_recipients(admin_client, session):
    u1 = _user(session, username="u1", email="u1@example.com")
    u2 = _user(session, username="u2", email="u2@example.com")

    with patch("app.services.email.send_email") as send:
        resp = admin_client.post(
            "/users/send-email",
            data={
                "uids": [str(u1.id), str(u2.id)],
                "subject": "Hi {{username}}",
                "body": "Thanks {{username}}",
            },
            headers=HX,
        )

    assert resp.status_code == 200
    assert send.call_count == 2
    sent_to = {call.args[0] for call in send.call_args_list}
    assert sent_to == {"u1@example.com", "u2@example.com"}


def test_placeholder_substitution_reaches_sent_content(admin_client, session):
    user = _user(session, username="dana", email="dana@example.com")

    with patch("app.services.email.send_email") as send:
        admin_client.post(
            "/users/send-email",
            data={
                "uids": [str(user.id)],
                "subject": "Hi {{username}}",
                "body": "Dear {{username}}, see {{email}}.",
            },
            headers=HX,
        )

    args = send.call_args.args
    assert args[0] == "dana@example.com"
    assert args[1] == "Hi dana"
    assert "Dear dana" in args[2]  # html body
    assert "dana@example.com" in args[2]


def test_per_recipient_failure_does_not_block_others(admin_client, session):
    ok = _user(session, username="ok", email="ok@example.com")
    bad = _user(session, username="bad", email="bad@example.com")

    def _send(to_email, *_args, **_kwargs):
        if to_email == "bad@example.com":
            raise EmailSendError("boom")

    with patch("app.services.email.send_email", side_effect=_send) as send:
        resp = admin_client.post(
            "/users/send-email",
            data={
                "uids": [str(ok.id), str(bad.id)],
                "subject": "Subject",
                "body": "Body",
            },
            headers=HX,
        )

    assert resp.status_code == 200
    assert send.call_count == 2
    html = resp.data.decode()
    assert "Sent to 1 of 2" in html
    assert "bad@example.com" in html
    assert "boom" in html


def test_duplicate_identity_accounts_receive_one_email(admin_client, session):
    identity = Identity(primary_email="shared@example.com")
    session.add(identity)
    session.commit()
    u1 = _user(
        session, username="acc1", email="shared@example.com", identity_id=identity.id
    )
    u2 = _user(
        session, username="acc2", email="shared@example.com", identity_id=identity.id
    )

    with patch("app.services.email.send_email") as send:
        admin_client.post(
            "/users/send-email",
            data={
                "uids": [str(u1.id), str(u2.id)],
                "subject": "Subject",
                "body": "Body",
            },
            headers=HX,
        )

    assert send.call_count == 1


def test_plex_user_without_identity_receives_the_email(admin_client, session):
    """Regression test: resolving the recipient must not depend on the
    server being jellyfin/emby (current_email() is scoped to those for the
    edit-email form and would wrongly resolve to "" for a Plex account)."""
    plex = _server(session, "Plex", server_type="plex")
    user = _user(
        session, username="plexuser", email="plex@example.com", server_id=plex.id
    )

    with patch("app.services.email.send_email") as send:
        resp = admin_client.post(
            "/users/send-email",
            data={"uids": [str(user.id)], "subject": "Subject", "body": "Body"},
            headers=HX,
        )

    assert resp.status_code == 200
    send.assert_called_once()
    assert send.call_args.args[0] == "plex@example.com"


def test_missing_subject_or_body_returns_400(admin_client, session):
    user = _user(session)

    resp = admin_client.post(
        "/users/send-email",
        data={"uids": [str(user.id)], "subject": "", "body": "Body"},
        headers=HX,
    )

    assert resp.status_code == 400


def test_save_as_template_persists_alongside_send(admin_client, session):
    user = _user(session)

    with patch("app.services.email.send_email"):
        admin_client.post(
            "/users/send-email",
            data={
                "uids": [str(user.id)],
                "subject": "Subject",
                "body": "Body",
                "save_as_template": "true",
                "template_name": "My Template",
            },
            headers=HX,
        )

    tmpl = EmailTemplate.query.filter_by(name="My Template").first()
    assert tmpl is not None
    assert tmpl.subject == "Subject"
    assert tmpl.body == "Body"


def test_save_as_template_with_existing_name_upserts(admin_client, session):
    session.add(EmailTemplate(name="My Template", subject="Old", body="Old body"))
    session.commit()
    user = _user(session)

    with patch("app.services.email.send_email"):
        admin_client.post(
            "/users/send-email",
            data={
                "uids": [str(user.id)],
                "subject": "New",
                "body": "New body",
                "save_as_template": "true",
                "template_name": "My Template",
            },
            headers=HX,
        )

    assert EmailTemplate.query.filter_by(name="My Template").count() == 1
    tmpl = EmailTemplate.query.filter_by(name="My Template").first()
    assert tmpl.subject == "New"
    assert tmpl.body == "New body"
