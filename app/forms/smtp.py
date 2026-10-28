from flask_babel import gettext as _
from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    Field,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    NumberRange,
    Optional,
    ValidationError,
)


def _valid_reminder_schedule(_form: FlaskForm, field: Field) -> None:
    """Reject anything that isn't a comma-separated list of positive integers."""
    raw = field.data or ""
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit() or int(token) <= 0:
            raise ValidationError(
                _(
                    "Enter a comma-separated list of positive whole numbers, "
                    "e.g. %(example)s.",
                    example="7,3,1",
                )
            )


class SMTPSettingsForm(FlaskForm):
    """Form for SMTP configuration settings."""

    enabled = BooleanField(_l("Enable SMTP"), default=False, validators=[Optional()])

    host = StringField(
        _l("SMTP Host"),
        validators=[DataRequired()],
        render_kw={"placeholder": "smtp.example.com"},
    )
    port = IntegerField(
        _l("Port"),
        default=587,
        validators=[DataRequired(), NumberRange(min=1, max=65535)],
    )
    encryption = SelectField(
        _l("Encryption"),
        choices=[
            ("none", _l("None")),
            ("starttls", _l("STARTTLS")),
            ("ssl", _l("SSL/TLS")),
        ],
        default="starttls",
        validators=[DataRequired()],
    )

    username = StringField(_l("Username"), validators=[Optional()])
    password = PasswordField(_l("Password"), validators=[Optional()])

    from_email = StringField(
        _l("From Email"),
        validators=[DataRequired(), Email()],
        render_kw={"placeholder": "wizarr@example.com"},
    )
    from_name = StringField(_l("From Name"), validators=[Optional()], default="Wizarr")

    reminder_schedule = StringField(
        _l("Send expiry reminders (days before expiry, comma-separated)"),
        default="3",
        validators=[Optional(), _valid_reminder_schedule],
        render_kw={"placeholder": "7,3,1"},
    )
    reminder_subject = StringField(
        _l("Custom reminder subject"), validators=[Optional()]
    )
    reminder_body = TextAreaField(
        _l("Custom reminder message"), validators=[Optional()]
    )


class SMTPTestEmailForm(FlaskForm):
    """Separate form for the 'send test email' button recipient field."""

    test_recipient_email = StringField(
        _l("Send test to"), validators=[DataRequired(), Email()]
    )
