import logging
import smtplib

from anymail.exceptions import AnymailError
from django.core.mail import EmailMessage

from apps.accounts.models import User

from .models import Email, EmailKind, EmailStatus

logger = logging.getLogger(__name__)


def send_email(user: User, kind: EmailKind, subject: str, body: str) -> bool:
    """Send one plain-text email and write it in the journal, whether it went out or not.

    Returns False when the mail server did not take it; the caller decides whether to retry.
    """
    message = EmailMessage(subject=subject, body=body, to=[user.email])
    journal = Email(user=user, to=user.email, kind=kind, subject=subject)
    try:
        message.send()
    # Mailpit or an SMTP server answers with SMTP errors or a dropped connection; Resend,
    # through anymail, with AnymailError (including its own network errors).
    except (smtplib.SMTPException, OSError, AnymailError) as error:
        logger.warning("Email to user %s failed: %s", user.pk, error)
        journal.status = EmailStatus.FAILED
        journal.error = str(error) or type(error).__name__
    else:
        journal.status = EmailStatus.SENT
        journal.provider_id = _provider_id(message)
    journal.save()
    return journal.status == EmailStatus.SENT


def _provider_id(message: EmailMessage) -> str:
    # Only anymail backends add a status to the message; Django's SMTP backend does not.
    status = getattr(message, "anymail_status", None)
    if status is None or status.message_id is None:
        return ""
    return str(status.message_id)
