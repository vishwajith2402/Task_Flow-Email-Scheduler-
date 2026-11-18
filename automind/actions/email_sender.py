import mimetypes
import smtplib
from email.message import EmailMessage
from pathlib import Path

from automind.config import SMTP_HOST, SMTP_PORT


def send_email(
    sender_email: str,
    app_password: str,
    recipient: str,
    subject: str,
    message: str,
    attachments: list[str] | None = None,
) -> None:
    email = EmailMessage()
    email["From"] = sender_email
    email["To"] = recipient
    email["Subject"] = subject
    email.set_content(message)
    for attachment_path in attachments or []:
        _attach_file(email, attachment_path)

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.login(sender_email, app_password)
            smtp.send_message(email)
    except smtplib.SMTPAuthenticationError as exc:
        raise ValueError("Gmail login failed. Check the sender email and Gmail app password.") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise ValueError("The recipient email address was refused by Gmail.") from exc
    except OSError as exc:
        raise ValueError(f"Could not connect to Gmail SMTP: {exc}") from exc


def _attach_file(email: EmailMessage, attachment_path: str) -> None:
    path = Path(attachment_path)
    if not path.exists() or not path.is_file():
        raise ValueError(f"Attachment not found: {attachment_path}")

    mime_type, _ = mimetypes.guess_type(path.name)
    maintype, subtype = (mime_type.split("/", 1) if mime_type else ("application", "octet-stream"))
    with path.open("rb") as file_handle:
        data = file_handle.read()

    email.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
