import re

import markdown
from flask import render_template

from app.models import User
from app.services.user_email import current_email

_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _resolve_username(user: User) -> str:
    if user.identity_id and user.identity and user.identity.nickname:
        return user.identity.nickname
    return user.username


_PLACEHOLDERS = {
    "username": _resolve_username,
    "email": lambda user: current_email(user) or "",
}


def substitute_placeholders(text: str, user: User) -> str:
    """Replace only the known {{username}}/{{email}} placeholders.

    Unknown placeholders (and any other `{{ }}` syntax) are left verbatim -
    this is a plain substitution, not a template language, so an
    admin-typed string can never reach a Jinja environment.
    """

    def _sub(match: re.Match) -> str:
        resolver = _PLACEHOLDERS.get(match.group(1))
        return resolver(user) if resolver else match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, text)


def render_custom_email(
    subject: str, body_markdown: str, user: User
) -> tuple[str, str, str]:
    """Return (subject, html_body, text_body) personalized for one recipient."""
    rendered_subject = substitute_placeholders(subject, user)
    rendered_markdown = substitute_placeholders(body_markdown, user)
    body_html = markdown.markdown(rendered_markdown, extensions=["nl2br"])
    html_body = render_template("email/custom.html", body_html=body_html)
    return rendered_subject, html_body, rendered_markdown
