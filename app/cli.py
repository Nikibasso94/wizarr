"""Custom `flask` CLI commands."""

import click
from flask import Flask


def register_cli(app: Flask) -> None:
    @app.cli.command("send-test-reminder")
    @click.argument("email")
    @click.option(
        "--days",
        type=click.IntRange(min=1),
        default=None,
        help="Days left to show in the email. Defaults to the smallest "
        "configured reminder threshold, or 3.",
    )
    def send_test_reminder(email: str, days: int | None) -> None:
        """Send the expiry reminder email, with sample data, to EMAIL."""
        from app.services.email import EmailSendError, send_test_reminder_email

        try:
            send_test_reminder_email(email, days_left=days)
        except EmailSendError as exc:
            raise click.ClickException(f"Failed to send test reminder: {exc}") from exc
        click.echo(f"Test reminder sent to {email}")
