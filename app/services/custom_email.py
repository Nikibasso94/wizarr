import re

import markdown
from flask import render_template

from app.models import User
from app.services.user_email import resolve_recipient_email

_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _resolve_username(user: User) -> str:
    if user.identity_id and user.identity and user.identity.nickname:
        return user.identity.nickname
    return user.username


_PLACEHOLDERS = {
    "username": _resolve_username,
    # resolve_recipient_email(), not current_email(): the latter is scoped
    # to Wizarr-editable (jellyfin/emby/no-server) accounts for the admin
    # edit-email form and resolves to "" for Plex/Kavita/etc. accounts
    # even when their address is populated.
    "email": lambda user: resolve_recipient_email(user) or "",
}


def substitute_placeholders(
    text: str, user: User, extra: dict[str, str] | None = None
) -> str:
    """Replace the known {{username}}/{{email}} placeholders, plus any
    caller-supplied ones in `extra` (e.g. {{days_left}} for reminder
    emails, which isn't resolvable from a User alone).

    Unknown placeholders (and any other `{{ }}` syntax) are left verbatim -
    this is a plain substitution, not a template language, so an
    admin-typed string can never reach a Jinja environment.
    """

    def _sub(match: re.Match) -> str:
        key = match.group(1)
        if extra and key in extra:
            return extra[key]
        resolver = _PLACEHOLDERS.get(key)
        return resolver(user) if resolver else match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, text)


def render_custom_email(
    subject: str, body_markdown: str, user: User, extra: dict[str, str] | None = None
) -> tuple[str, str, str]:
    """Return (subject, html_body, text_body) personalized for one recipient."""
    rendered_subject = substitute_placeholders(subject, user, extra)
    rendered_markdown = substitute_placeholders(body_markdown, user, extra)
    body_html = markdown.markdown(rendered_markdown, extensions=["nl2br"])
    html_body = render_template("email/custom.html", body_html=body_html)
    return rendered_subject, html_body, rendered_markdown
